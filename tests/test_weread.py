from email.message import Message
import json
import re
import threading
import time
import unittest
from unittest.mock import patch
import urllib.parse

import weread
from qr_code import matrix
from tests.test_library import LibraryFixture
from weread import WeRead

# python-qrcode's matrix for b'hi' (version 1, error correction M, mask 2), to check the encoder bit for bit.
HI = ['111111100111101111111','100000100110101000001','101110101101101011101','101110101100101011101','101110101001101011101',
      '100000101100101000001','111111101010101111111','000000001011100000000','101111100000101111100','011101010010100100001',
      '001100110101010011110','111010000100000110100','111010100001010010101','000000001001111001001','111111100010101100010',
      '100000101111111001001','101110101000100100100','101110101110100100100','101110101001010011100','100000100110000110100',
      '111111101011010011110']


class FakeWeRead:
    """WeRead's web API and its COS bucket as weread.py sees them; the shapes come from its upload page."""
    COS = 'shelf-1.cos.accelerate.myqcloud.com'

    def __init__(self,verify=False):
        self.verify,self.skey,self.renewable = verify,'at',True
        self.calls,self.objects,self.parts,self.lock = [],{},{},threading.Lock()
        self.polls = 0

    def reply(self,data,cookies=(),status=200,raw=None):
        headers = Message()
        for cookie in cookies:headers['Set-Cookie'] = cookie
        return status,headers,raw if raw is not None else json.dumps(data).encode()

    def __call__(self,request,timeout):
        url = urllib.parse.urlsplit(request.full_url)
        body = request.data or b''
        data = json.loads(body) if url.netloc=='weread.qq.com' and body else {}
        cookies = dict(part.split('=',1) for part in (request.get_header('Cookie') or '').split('; ') if part)
        with self.lock:self.calls.append({'method':request.get_method(),'host':url.netloc,'path':url.path,'query':url.query,'data':data,'body':body,
                                          'cookies':cookies,'headers':dict(request.header_items())})
        if url.netloc==self.COS:return self.cos(request,url,body)
        if url.path=='/web/login/getuid':return self.reply({'uid':'uid-1'})
        if url.path=='/web/login/getinfo':
            # The first long poll ends without a scan, the next one with it.
            self.polls += 1
            return self.reply({'scan':0} if self.polls==1 else {'vid':42,'skey':'scan-skey','code':'scan-code','pf':0})
        if url.path=='/web/login/weblogin':
            if self.verify and 'otp' not in data:return self.reply({'needRemoteLoginVerify':1})
            if self.verify and data['otp']!='123456':return self.reply({'isVerifyFailed':1})
            return self.reply({'vid':42,'accessToken':'at','refreshToken':'rt'})
        if url.path=='/web/login/session/init':
            return self.reply({'success':1},[f'wr_vid={data["vid"]}; Path=/; HttpOnly',f'wr_skey={data["skey"]}; Path=/; HttpOnly',
                                             f'wr_rt={data["rt"]}; Path=/','wr_old=1; Max-Age=0; Path=/'])
        if url.path=='/web/login/renewal':
            if not self.renewable:return self.reply({'errCode':-2012,'errMsg':'登录超时'})
            return self.reply({'succ':1},[f'wr_skey={self.skey}; Path=/'])
        if cookies.get('wr_skey')!=self.skey:return self.reply({'errCode':-2012,'errMsg':'登录超时'})
        if url.path=='/web/user':return self.reply({'userVid':42,'name':'小林'})
        if url.path=='/web/cos/getcredential':
            return self.reply({'Response':{'Credentials':{'TmpSecretId':'tmp-id','TmpSecretKey':'tmp-key','Token':'tmp-token'},'ExpiredTime':0},
                               'ObjectName':'private/42/CB_book','region':'accelerate','bucket':'shelf-1','extraInfo':{}})
        if url.path=='/web/cos/notify':
            done = sum(c['path']=='/web/cos/notify' for c in self.calls)>1
            return self.reply({'status':0,'bookId':'CB_book'} if done and data['path'] in self.objects else {'status':1,'bookId':'CB_book'})
        return self.reply({'errCode':-1,'errMsg':'unknown'},status=404)

    def cos(self,request,url,body):
        query = urllib.parse.parse_qs(url.query,keep_blank_values=True)
        assert request.get_header('Authorization').startswith('q-sign-algorithm=sha1&q-ak=tmp-id&'),request.get_header('Authorization')
        assert request.get_header('X-cos-security-token')=='tmp-token'
        key = urllib.parse.unquote(url.path).lstrip('/')
        if 'uploads' in query:return self.reply(None,raw=b'<InitiateMultipartUploadResult><UploadId>up-1</UploadId></InitiateMultipartUploadResult>')
        if 'partNumber' in query:
            self.parts[int(query['partNumber'][0])] = body
            status,headers,raw = self.reply(None,raw=b'')
            headers['ETag'] = f'"etag-{query["partNumber"][0]}"'
            return status,headers,raw
        etags = re.findall(rb'<ETag>([^<]+)</ETag>',body)
        assert etags==[f'"etag-{n}"'.encode() for n in sorted(self.parts)],etags
        self.objects[key] = b''.join(self.parts[n] for n in sorted(self.parts))
        return self.reply(None,raw=b'<CompleteMultipartUploadResult/>')


def wait(condition,label,seconds=5):
    deadline = time.time()+seconds
    while time.time()<deadline:
        if condition():return
        time.sleep(.01)
    raise AssertionError('Timed out: '+label)


class WeReadTests(LibraryFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeWeRead()
        self.patches = [patch('weread.fetch',side_effect=lambda request,timeout:self.fake(request,timeout)),patch('weread.POLL',.01)]
        for item in self.patches:item.start()
        self.lib.weread = WeRead(self.lib)
        self.book = next(b for b in self.lib.listing({'q':'春日'})['items'] if b['ext']=='.txt')

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        super().tearDown()

    def sign_in(self):
        status = self.lib.weread.login()
        wait(lambda:self.lib.weread.signed_in and self.lib.weread.name,'signed in')
        return status

    def finished(self,task):
        wait(lambda:task['state'] not in ('uploading','importing'),'upload finished')
        return task

    def test_qr_code_matches_a_reference_encoder(self):
        self.assertEqual([''.join('1' if v else '0' for v in row) for row in matrix('hi')],HI)
        link = 'https://weread.qq.com/web/confirm?pf=2&uid=18b82ade-3c5e-4622-a1e8-3d99d6a4043a'
        self.assertEqual(len(matrix(link)),37,'a WeRead login link fits version 5')
        with self.assertRaises(ValueError):matrix('x'*200)

    def test_scan_to_sign_in_then_upload_a_version_to_the_shelf(self):
        status = self.sign_in()
        self.assertEqual(status['login']['state'],'waiting')
        self.assertEqual(status['login']['link'],'https://weread.qq.com/web/confirm?pf=2&uid=uid-1')
        self.assertEqual(status['login']['qr'],[''.join('1' if v else '0' for v in row) for row in matrix(status['login']['link'])])
        login = next(c for c in self.fake.calls if c['path']=='/web/login/weblogin')['data']
        self.assertEqual({k:login[k] for k in ('vid','skey','code','uid')},{'vid':42,'skey':'scan-skey','code':'scan-code','uid':'uid-1'})
        self.assertEqual(self.lib.weread.status(),{'signed_in':True,'name':'小林','login':{'state':'idle'}})
        stored = self.lib.weread._values()
        self.assertNotIn('rt',json.dumps(stored).replace('cookies',''),'cookies are stored protected');self.assertNotIn('wr_skey',json.dumps(stored))
        self.assertTrue(WeRead(self.lib).signed_in,'the sign-in survives a restart')
        self.assertNotIn('wr_old',self.lib.weread.cookies,'a cookie WeRead clears is dropped')

        raw = '第一章\n很长的正文。\n'.encode()*10
        with patch('weread.PART',64):
            path = self.lib.version_path(self.lib.book(self.book['id']))
            path.write_bytes(raw)
            task = self.finished(self.lib.weread.upload(self.book['id']))
        self.assertEqual((task['state'],task['book_id'],task['title']),('done','CB_book','春日'),task)
        self.assertEqual(self.lib.weread.task(task['id'])['state'],'done')
        self.assertEqual(self.fake.objects['private/42/CB_book'],raw,'uploaded in parts and put back together')
        self.assertGreater(len(self.fake.parts),1)
        credential = next(c for c in self.fake.calls if c['path']=='/web/cos/getcredential')
        self.assertEqual(urllib.parse.parse_qs(credential['query'])['name'],['春日.txt'])
        self.assertEqual(credential['cookies']['wr_skey'],'at')
        notify = [c['data'] for c in self.fake.calls if c['path']=='/web/cos/notify']
        self.assertEqual(notify[-1],{'path':'private/42/CB_book','name':'春日.txt'});self.assertEqual(len(notify),2,'waits while WeRead imports')

    def test_verification_code_when_weread_asks_for_one(self):
        self.fake.verify = True
        self.lib.weread.login()
        wait(lambda:self.lib.weread.status()['login']['state']=='verify','asked for a code')
        self.assertNotIn('params',self.lib.weread.status()['login'],'login parameters stay on the backend')
        self.lib.weread.login('000000')
        wait(lambda:self.lib.weread.status()['login'].get('error'),'wrong code')
        self.assertEqual(self.lib.weread.status()['login']['state'],'verify')
        self.lib.weread.login('123456')
        wait(lambda:self.lib.weread.signed_in,'signed in with the code')
        self.assertEqual([c['data'].get('otp') for c in self.fake.calls if c['path']=='/web/login/weblogin'],[None,'000000','123456'])

    def test_expired_sign_in_is_renewed_once_or_asks_to_scan_again(self):
        self.sign_in()
        self.fake.skey = 'renewed'
        task = self.finished(self.lib.weread.upload(self.book['id']))
        self.assertEqual(task['state'],'done',task)
        renewal = next(c for c in self.fake.calls if c['path']=='/web/login/renewal')['data']
        self.assertEqual(renewal['rq'],'%2Fweb%2Fcos%2Fgetcredential')
        self.assertEqual(self.lib.weread.cookies['wr_skey'],'renewed');self.assertEqual(WeRead(self.lib).cookies['wr_skey'],'renewed','renewed cookie saved')
        self.fake.skey,self.fake.renewable = 'other',False
        task = self.finished(self.lib.weread.upload(self.book['id']))
        self.assertEqual((task['state'],task['login']),('failed',True));self.assertIn('重新扫码',task['error'])
        self.assertFalse(self.lib.weread.signed_in);self.assertFalse(WeRead(self.lib).signed_in)
        with self.assertRaises(weread.WeReadError) as caught:self.lib.weread.upload(self.book['id'])
        self.assertTrue(caught.exception.login)

    def test_only_novels_go_to_weread(self):
        self.sign_in()
        pdf = next(b for b in self.lib.listing({'q':'sample'})['items'] if b['kind']=='pdf')
        with self.assertRaises(weread.WeReadError):self.lib.weread.upload(pdf['id'])


if __name__=='__main__':
    unittest.main()
