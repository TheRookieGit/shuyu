from email.message import Message
import json
import threading
import time
import unittest
from unittest.mock import patch
import urllib.parse

import netdisk
from lite_upload import UPLOAD_FOLDER
from netdisk import NetDisk, NetDiskError, links, wanted
from tests.test_library import LibraryFixture, packed

QUARK_NOVEL = '书名：夸克书\n作者：某人\n\n第一章\n夸克里的小说。\n'.encode('gb18030')
BAIDU_NOVEL = '第一章\n百度里的小说。\n'.encode()
ZIPPED = packed({'《压缩里的书》作者：别人.txt':'包里的小说。'.encode()})


class FakeDrives:
    """Quark's and Baidu's web APIs as netdisk.py sees them; the shapes come from their web pages."""
    def __init__(self):
        self.calls,self.lock = [],threading.Lock()
        self.quark_polls = self.baidu_polls = self.quark_checks = 0
        self.quark_logged_in = True
        self.quark_folder = self.baidu_folder = False
        self.saved = {}

    def reply(self,data=None,status=200,raw=None,cookies=(),location=None,sink=None,content=None):
        headers = Message()
        for cookie in cookies:headers['Set-Cookie'] = cookie
        if location:headers['Location'] = location
        if content is not None:
            sink.write(content);return status,headers,b''
        return status,headers,raw if raw is not None else json.dumps(data,ensure_ascii=False).encode()

    def __call__(self,request,timeout,sink=None):
        url = urllib.parse.urlsplit(request.full_url)
        query = {k:v[-1] for k,v in urllib.parse.parse_qs(url.query,keep_blank_values=True).items()}
        cookies = dict(part.split('=',1) for part in (request.get_header('Cookie') or '').split('; ') if part)
        body = request.data or b''
        form = {k:v[-1] for k,v in urllib.parse.parse_qs(body.decode()).items()} if 'form-urlencoded' in (request.get_header('Content-type') or '') else {}
        data = json.loads(body) if body and not form else {}
        call = {'host':url.netloc,'path':url.path,'query':query,'cookies':cookies,'data':data,'form':form,'agent':request.get_header('User-agent')}
        with self.lock:self.calls.append(call)
        if url.netloc.endswith('quark.cn') or url.netloc.endswith('quark.test'):return self.quark(url,query,cookies,data,sink)
        return self.baidu(url,query,cookies,form,sink,call['agent'])

    def quark(self,url,query,cookies,data,sink):
        if url.path=='/cas/ajax/getTokenForQrcodeLogin':
            self.quark_polls += 1
            return self.reply({'status':2000000,'data':{'members':{'token':f'token-{self.quark_polls}'}}})
        if url.path=='/cas/ajax/getServiceTicketByQrcodeToken':
            self.quark_checks += 1
            if self.quark_checks==1:raise netdisk.Unreachable('dropped connection')
            # The first QR code expires before anyone scans it; the second one is scanned.
            if query['token']=='token-1':return self.reply({'status':50004002,'message':'Token Not Found'})
            return self.reply({'status':2000000,'data':{'members':{'service_ticket':'ticket'}}})
        if url.path=='/account/info':
            assert query['st']=='ticket'
            return self.reply({'success':True,'code':'OK','data':{'nickname':'夸克用户'}},cookies=['__pus=pus-cookie; Domain=.quark.cn; Path=/; HttpOnly'])
        if url.netloc=='dl.quark.test':return self.reply(status=302,location='https://cdn.quark.test'+url.path,raw=b'')
        if url.netloc=='cdn.quark.test':
            return self.reply(sink=sink,content={'/s-f1':QUARK_NOVEL,'/s-f2':ZIPPED}[url.path])
        if not self.quark_logged_in or cookies.get('__pus')!='pus-cookie':return self.reply({'status':401,'code':31001,'message':'require login [guest]'},401)
        path = url.path.removeprefix('/1/clouddrive/')
        if path=='share/sharepage/token':
            if data['pwd_id']!='qshare':return self.reply({'status':404,'code':41006,'message':'分享不存在'},404)
            return self.reply({'status':200,'code':0,'data':{'stoken':'stoken'}})
        if path=='share/sharepage/detail':
            assert query['stoken']=='stoken'
            items = {'0':[{'fid':'d1','file_name':'书','dir':True},{'fid':'f3','file_name':'封面.jpg','dir':False,'size':10,'share_fid_token':'t3'}],
                     'd1':[{'fid':'f1','file_name':'夸克书.txt','dir':False,'size':len(QUARK_NOVEL),'share_fid_token':'t1'},
                           {'fid':'f2','file_name':'合集.zip','dir':False,'size':len(ZIPPED),'share_fid_token':'t2'}]}[query['pdir_fid']]
            return self.reply({'status':200,'code':0,'data':{'list':items},'metadata':{'_total':len(items)}})
        if path=='file/info/path_list':return self.reply({'status':200,'code':0,'data':[{'fid':'mine'}] if self.quark_folder else []})
        if path=='file':
            self.quark_folder = True;return self.reply({'status':200,'code':0,'data':{'fid':'mine'}})
        if path=='share/sharepage/save':
            assert data['to_pdir_fid']=='mine' and data['fid_token_list']==[{'f1':'t1','f2':'t2'}[f] for f in data['fid_list']]
            self.saved['quark'] = data
            return self.reply({'status':200,'code':0,'data':{'task_id':'task'}})
        if path=='task':
            return self.reply({'status':200,'code':0,'data':{'status':2,'save_as':{'save_as_top_fids':['s-'+f for f in self.saved['quark']['fid_list']]}}})
        if path=='file/download':
            return self.reply({'status':200,'code':0,'data':[{'fid':f,'download_url':'https://dl.quark.test/'+f} for f in data['fids']]})
        return self.reply({'status':404,'code':1,'message':'unknown'},404)

    def baidu(self,url,query,cookies,form,sink,agent):
        signed = cookies.get('BDUSS')=='bduss' and cookies.get('STOKEN')=='pan-stoken'
        if url.netloc=='pan.baidu.com' and url.path=='/':return self.reply(raw=b'<html>',cookies=['BAIDUID=b; Domain=.baidu.com; Path=/'])
        if url.path=='/v2/api/getqrcode':return self.reply({'imgurl':'passport.baidu.com/v2/api/qrcode?sign=sign','errno':0,'sign':'sign'})
        if url.path=='/channel/unicast':
            self.baidu_polls += 1
            value = [{'status':1},{'status':0,'v':'temporary'}][min(self.baidu_polls,2)-1]
            return self.reply({'errno':0,'channel_id':'sign','channel_v':json.dumps(value)})
        if url.path=='/v3/login/main/qrbdusslogin':
            assert query['bduss']=='temporary'
            # Like Baidu's own reply: one key is single-quoted, so it is not valid JSON.
            return self.reply(raw=b'{"errInfo":{"no": "0"}, "code": "110000", \'data\': {"u": ""}}',cookies=['BDUSS=bduss; Domain=.baidu.com; Path=/; HttpOnly'])
        if url.path=='/v3/login/api/auth/':
            assert cookies.get('BDUSS')=='bduss'
            return self.reply(status=302,location='https://pan.baidu.com/disk/main',raw=b'')
        if url.path=='/disk/main':return self.reply(raw=b'<html>',cookies=['STOKEN=pan-stoken; Domain=.pan.baidu.com; Path=/'])
        if url.netloc=='d.pcs.baidu.com':
            assert agent=='pan.baidu.com',agent
            return self.reply(status=302,location='https://nd.baidupcs.test'+url.path,raw=b'')
        if url.netloc=='nd.baidupcs.test':return self.reply(sink=sink,content=BAIDU_NOVEL)
        if url.path=='/share/verify':
            if form['pwd']!='abcd':return self.reply(raw=b'\n\n{"errno":-9,"err_msg":""}')
            return self.reply({'errno':0,'randsk':'rk%3D'})
        if url.path=='/share/list':
            if cookies.get('BDCLND')!='rk%3D':return self.reply({'errno':9019,'errmsg':'need verify'})
            items = [{'fs_id':11,'server_filename':'百度书.txt','isdir':'0','size':str(len(BAIDU_NOVEL)),'path':'/资源/百度书.txt'},
                     {'fs_id':12,'server_filename':'sub','isdir':'1','size':'0','path':'/资源/sub'}] if query.get('root') else \
                    [{'fs_id':13,'server_filename':'说明.pdf','isdir':'0','size':'10','path':'/资源/sub/说明.pdf'}]
            return self.reply({'errno':0,'share_id':77,'uk':88,'list':items})
        if not signed:return self.reply({'errno':-6})
        if url.path=='/api/gettemplatevariable':return self.reply({'errno':0,'result':{'bdstoken':'bdstoken','username':'百度用户'}})
        if url.path=='/api/list':return self.reply({'errno':0 if self.baidu_folder else -9,'list':[]})
        if url.path=='/api/create':
            assert form['path']=='/书屿转存' and query['bdstoken']=='bdstoken'
            self.baidu_folder = True;return self.reply({'errno':0})
        if url.path=='/share/transfer':
            assert (query['shareid'],query['from'],query['sekey'],query['async'],form['path'])==('77','88','rk=','1','/书屿转存')
            ids = json.loads(form['fsidlist']);self.saved['baidu'] = ids
            return self.reply({'errno':0,'extra':{'list':[{'from_fs_id':i,'to':'/书屿转存/百度书.txt'} for i in ids]}})
        if url.path=='/api/filemetas':
            return self.reply({'errno':0,'info':[{'path':p,'dlink':'https://d.pcs.baidu.com/file/'+str(n)} for n,p in enumerate(json.loads(query['target']))]})
        return self.reply({'errno':404})


def wait(condition,label,seconds=5):
    deadline = time.time()+seconds
    while time.time()<deadline:
        if condition():return
        time.sleep(.01)
    raise AssertionError('Timed out: '+label)


class NetDiskTests(LibraryFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeDrives()
        self.patches = [patch('netdisk.fetch',side_effect=lambda request,timeout,sink=None:self.fake(request,timeout,sink)),patch('netdisk.POLL',.01)]
        for item in self.patches:item.start()
        self.lib.netdisk = NetDisk(self.lib)

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        super().tearDown()

    def sign_in(self,key):
        status = self.lib.netdisk.drive(key).login()
        wait(lambda:self.lib.netdisk.status()[key]['signed_in'],key+' signed in')
        return status

    def finished(self,task):
        wait(lambda:task['state'] in ('done','failed'),'download finished')
        return task

    def test_share_links_and_extraction_codes_found_in_pasted_text(self):
        text = ('我用夸克网盘分享了「小说」，点击链接即可保存。\n链接：https://pan.quark.cn/s/34d748b55cc7 提取码：AB12\n'
                '链接: https://pan.baidu.com/s/1UgC33lOxfa_dhSr6beLnoA?pwd=4g8j 提取码: 4g8j 复制这段内容后打开百度网盘手机App\n'
                'https://pan.baidu.com/share/init?surl=abcDEF https://pan.quark.cn/s/abc')
        self.assertEqual(links(text),[{'drive':'quark','id':'34d748b55cc7','code':'AB12'},{'drive':'baidu','id':'UgC33lOxfa_dhSr6beLnoA','code':'4g8j'},
                                      {'drive':'baidu','id':'abcDEF','code':''},{'drive':'quark','id':'abc','code':''}])
        self.assertEqual(links('没有链接'),[])
        self.assertEqual(wanted('a.TXT',10),'');self.assertEqual(wanted('a.zip',100*1024*1024),'')
        self.assertTrue(wanted('a.jpg',10));self.assertTrue(wanted('a.txt',0));self.assertTrue(wanted('a.txt',65*1024*1024))

    def test_quark_qr_code_renews_until_scanned_then_novels_and_archives_land_in_the_library(self):
        status = self.sign_in('quark')
        self.assertEqual(status['login']['state'],'waiting');self.assertTrue(status['login']['qr']);self.assertIn('token=token-1',status['login']['link'])
        self.assertFalse(any(k.startswith('_') for k in status['login']),'tokens stay on the backend')
        self.assertEqual(self.fake.quark_polls,2,'an expired QR code is replaced')
        self.assertEqual(self.fake.quark_checks,3,'a dropped connection while waiting is tried again')
        self.assertEqual(self.lib.netdisk.status()['quark']['name'],'夸克用户')
        self.assertNotIn('pus-cookie',json.dumps(self.lib.netdisk.drive('quark').session.values()),'cookies are stored protected')
        self.assertTrue(NetDisk(self.lib).drive('quark').signed_in,'the sign-in survives a restart')

        task = self.finished(self.lib.netdisk.fetch('链接：https://pan.quark.cn/s/qshare'))
        self.assertEqual(task['state'],'done',task)
        self.assertEqual(sorted(b['title'] for b in task['books']),['压缩里的书','夸克书'])
        self.assertTrue((self.root/UPLOAD_FOLDER/'《夸克书》作者：某人【lite上传】.txt').is_file())
        self.assertEqual((self.root/UPLOAD_FOLDER/'《夸克书》作者：某人【lite上传】.txt').read_bytes(),QUARK_NOVEL)
        self.assertEqual(task['skipped'],['封面.jpg（不是小说或压缩包）']);self.assertEqual(task['failed'],[])
        self.assertTrue(any('合集.zip：取出 1 本小说' in note for note in task['notes']))
        self.assertEqual((self.fake.saved['quark']['fid_list'],self.fake.saved['quark']['pdir_fid']),(['f1','f2'],'d1'),'saved from its own folder')
        self.assertFalse(list((self.data/'netdisk').glob('*/*')),'downloads are removed once stored')
        again = self.finished(self.lib.netdisk.fetch('https://pan.quark.cn/s/qshare'))
        self.assertEqual(again['state'],'done')
        self.assertEqual(sum(c['path']=='/1/clouddrive/file' for c in self.fake.calls),1,'the 书屿转存 folder is made once')
        self.assertTrue((self.root/UPLOAD_FOLDER/'《夸克书》作者：某人【lite上传】（2）.txt').is_file())

        missing = self.finished(self.lib.netdisk.fetch('https://pan.quark.cn/s/gone'))
        self.assertEqual(missing['state'],'done');self.assertEqual(missing['failed'],['夸克网盘链接：夸克网盘：分享不存在'])

    def test_baidu_scan_then_extraction_code_checked_and_novel_downloaded(self):
        self.sign_in('baidu')
        self.assertEqual(self.lib.netdisk.status()['baidu']['name'],'百度用户')
        session = self.lib.netdisk.drive('baidu').session
        self.assertEqual((session.cookie('BDUSS'),session.cookie('STOKEN')),('bduss','pan-stoken'),'the drive token comes through the redirect')
        wrong = self.finished(self.lib.netdisk.fetch('https://pan.baidu.com/s/1bshare?pwd=zzzz'))
        self.assertEqual(wrong['failed'],['百度网盘链接：百度网盘：提取码不对，或者分享已失效。'])
        task = self.finished(self.lib.netdisk.fetch('链接: https://pan.baidu.com/s/1bshare 提取码: abcd'))
        self.assertEqual(task['state'],'done',task)
        self.assertEqual([b['title'] for b in task['books']],['百度书'])
        self.assertEqual((self.root/UPLOAD_FOLDER/task['books'][0]['rel'].split('/')[-1]).read_bytes(),BAIDU_NOVEL)
        self.assertEqual(self.fake.saved['baidu'],[11]);self.assertEqual(task['skipped'],['说明.pdf（不是小说或压缩包）'])

    def test_sign_in_needed_first_and_again_when_it_lapses(self):
        with self.assertRaises(NetDiskError) as caught:self.lib.netdisk.fetch('https://pan.baidu.com/s/1bshare 提取码 abcd')
        self.assertEqual(caught.exception.login,'baidu')
        with self.assertRaises(NetDiskError):self.lib.netdisk.fetch('随便一段话')
        self.sign_in('quark')
        self.fake.quark_logged_in = False
        task = self.finished(self.lib.netdisk.fetch('https://pan.quark.cn/s/qshare'))
        self.assertEqual((task['state'],task['login']),('failed','quark'));self.assertIn('重新扫码',task['message'])
        self.assertFalse(self.lib.netdisk.status()['quark']['signed_in'])
        self.sign_in('quark')
        self.assertEqual(self.lib.netdisk.drive('quark').logout()['signed_in'],False)
        self.assertFalse(NetDisk(self.lib).drive('quark').signed_in)


if __name__=='__main__':
    unittest.main()
