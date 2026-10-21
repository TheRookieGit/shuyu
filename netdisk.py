"""Download novels from Quark (夸克网盘) and Baidu (百度网盘) share links into the library, as if uploaded from Lite.

Neither drive has a public API for this. Following their web pages: sign in by scanning a QR code with the drive's
app, save the shared novels and archives into the reader's own drive (a 书屿转存 folder), download them from there,
and store each one through Lite's upload, so archives are unpacked and novels named the usual way."""
from __future__ import annotations

from email.message import Message
from http.cookiejar import Cookie, CookieJar
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from ai_cleaner import SUPPORTED, CleanError, MAX_SOURCE, protect_secret
from archive_engine import ArchiveError, ARCHIVES
from lite_upload import MAX_ARCHIVE
from qr_code import matrix

AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
FOLDER = '书屿转存'
LOGIN_SECONDS = 600
POLL = 2
MAX_FILES = 500
REDIRECTS = {301,302,303,307,308}


class NetDiskError(Exception):
    def __init__(self,message,login=''):
        super().__init__(message)
        self.login = login  # The drive to sign in to before trying again.


class Unreachable(NetDiskError):
    """The drive could not be reached this time; worth trying again."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def fetch(request,timeout,sink=None):
    """(status, headers, body) for one exchange, redirects not followed; with a sink, a 200 body is written there.
    Tests replace this."""
    try:response = OPENER.open(request,timeout=timeout)
    except urllib.error.HTTPError as exc:response = exc
    except (urllib.error.URLError,TimeoutError,OSError) as exc:
        raise Unreachable('连不上网盘，请检查 Windows 电脑的网络后再试。') from exc
    with response:
        status = response.status or response.code
        try:
            if sink is not None and status==200:
                shutil.copyfileobj(response,sink,1024*1024)
                return status,response.headers,b''
            return status,response.headers,response.read()
        except OSError as exc:
            raise NetDiskError('下载中断了，请重试。') from exc


class Reply:
    """What CookieJar.extract_cookies expects of a response."""
    def __init__(self,headers):
        self.headers = headers if headers is not None else Message()

    def info(self):
        return self.headers


def wanted(name,size):
    """Why a shared file is left out, or '' when it is a novel or archive Lite can take."""
    ext = PurePosixPath(name).suffix.lower()
    if ext not in SUPPORTED|ARCHIVES:return '不是小说或压缩包'
    limit = MAX_ARCHIVE if ext in ARCHIVES else MAX_SOURCE
    return '' if 0<size<=limit else f'超过 {limit//1024//1024} MB' if size>limit else '是空文件'


class Session:
    """Cookies across hosts and redirects, stored encrypted like the API keys."""
    def __init__(self,lib,key):
        self.lib,self.key = lib,key
        self.jar = CookieJar()
        values = self.values()
        self.name = values.get('name','')
        try:
            for item in json.loads(protect_secret(values['cookies'],True)) if values.get('cookies') else []:
                self.jar.set_cookie(Cookie(0,item['name'],item['value'],None,False,item['domain'],True,item['domain'].startswith('.'),
                                           item['path'],True,item['secure'],item['expires'],False,None,None,{}))
        except (CleanError,ValueError,TypeError,KeyError):
            self.jar.clear()

    def values(self):
        with self.lib.db() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?',(self.key,)).fetchone()
        return json.loads(row['value']) if row else {}

    def save(self):
        cookies = [{'name':c.name,'value':c.value,'domain':c.domain,'path':c.path,'secure':c.secure,'expires':c.expires}
                   for c in self.jar if not c.is_expired()]
        values = {'name':self.name,**({'cookies':protect_secret(json.dumps(cookies))} if cookies else {})}
        with self.lib.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(self.key,json.dumps(values,ensure_ascii=False)))

    def cookie(self,name):
        return next((c.value for c in self.jar if c.name==name and not c.is_expired()),'')

    def forget(self):
        self.jar.clear();self.name = '';self.save()

    def send(self,url,data=None,headers=None,timeout=60,sink=None):
        """(status, body, final url), following redirects with the cookies of each host."""
        headers = {'User-Agent':AGENT,**(headers or {})}
        for _ in range(10):
            request = urllib.request.Request(url,data=data,headers=headers)
            self.jar.add_cookie_header(request)
            status,reply,body = fetch(request,timeout,sink)
            self.jar.extract_cookies(Reply(reply),request)
            location = reply.get('Location') if reply is not None else None
            if status in REDIRECTS and location:
                url,data = urllib.parse.urljoin(url,location),None
                headers = {k:v for k,v in headers.items() if k!='Content-Type'}
                continue
            return status,body,url
        raise NetDiskError('网盘的跳转次数太多，请稍后再试。')

    def json(self,url,data=None,form=False,headers=None,timeout=60):
        body = None
        headers = dict(headers or {})
        if data is not None:
            body = urllib.parse.urlencode(data).encode() if form else json.dumps(data,ensure_ascii=False).encode()
            headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json;charset=UTF-8'
        status,raw,_ = self.send(url,body,headers,timeout)
        # Baidu's login replies come wrapped in a callback or padded with blank lines.
        text = raw.decode('utf-8','replace')
        match = re.search(r'\{.*\}',text,re.S)
        try:result = json.loads(match.group(0) if match else text)
        except ValueError:raise NetDiskError(f'网盘返回了无法识别的内容（HTTP {status}）。')
        if not isinstance(result,dict):raise NetDiskError('网盘返回了无法识别的内容。')
        result.setdefault('http',status)
        return result


class Drive:
    """One drive's sign-in by QR code and its share links; subclasses speak each drive's web API."""
    key = label = app = ''

    def __init__(self,lib):
        self.lib = lib
        self.session = Session(lib,'netdisk_'+self.key)
        self.login_state = {'state':'idle'}
        self.attempt = 0
        self.lock = threading.Lock()

    @property
    def signed_in(self):
        raise NotImplementedError

    def status(self):
        return {'label':self.label,'app':self.app,'signed_in':self.signed_in and self.login_state['state']!='checking',
                'name':self.session.name,'login':{k:v for k,v in self.login_state.items() if not k.startswith('_')}}

    def login(self):
        with self.lock:
            self.attempt += 1;attempt = self.attempt
            self.session.jar.clear()
            self.login_state = {'state':'waiting',**self.qr()}
            status = self.status()
        threading.Thread(target=self._wait,args=(attempt,),daemon=True).start()
        return status

    def logout(self):
        with self.lock:
            self.attempt += 1
            self.login_state = {'state':'idle'}
            self.session.forget()
        return self.status()

    def _wait(self,attempt):
        deadline,misses = time.time()+LOGIN_SECONDS,0
        try:
            while attempt==self.attempt and time.time()<deadline:
                # A long wait for the scan easily meets one dropped connection; only several in a row end it.
                try:
                    if self.poll(attempt):return
                    misses = 0
                except Unreachable:
                    misses += 1
                    if misses>=5:raise
                    time.sleep(POLL)
            if attempt==self.attempt:self.login_state = {'state':'failed','error':'二维码已过期，请重新获取。'}
        except NetDiskError as exc:
            if attempt==self.attempt:self.login_state = {'state':'failed','error':str(exc)}
        except (KeyError,TypeError,ValueError,AttributeError):
            if attempt==self.attempt:self.login_state = {'state':'failed','error':f'{self.label}的登录方式可能变了，没能完成登录。'}

    def signed(self,attempt,name):
        """Finish signing in: keep the cookies and the account name to show."""
        with self.lock:
            if attempt!=self.attempt:return
            self.session.name = str(name or '')[:40]
            self.session.save()
            self.login_state = {'state':'idle'}

    def expired(self):
        self.session.forget()
        raise NetDiskError(f'{self.label}的登录已过期，请重新扫码登录。',login=self.key)

    def fetch_file(self,url,item,work,headers):
        """Download one saved file into the work folder; the path, or why it did not arrive."""
        path = work/uuid.uuid4().hex
        with path.open('wb') as sink:status,_,_ = self.session.send(url,headers=headers,timeout=600,sink=sink)
        if status!=200:return f'下载失败（HTTP {status}）'
        if path.stat().st_size!=item['size']:return '下载不完整，请重试'
        return path

    # Subclasses: qr() -> fields for the login state, poll(attempt) -> True once signed in,
    # files(link) -> (share, files), download(share, files) -> yields (file, path or error) as each lands.


class Quark(Drive):
    key,label,app = 'quark','夸克网盘','夸克 App'
    API = 'https://drive-pc.quark.cn/1/clouddrive/'
    SCAN = 'https://su.quark.cn/4_eMHBJ'
    CLIENT = {'client_id':532,'v':'1.2'}

    @property
    def signed_in(self):
        return bool(self.session.cookie('__pus'))

    def qr(self):
        self.login_state['_request'] = request_id = self.login_state.get('_request') or str(uuid.uuid4())
        data = self.session.json('https://uop.quark.cn/cas/ajax/getTokenForQrcodeLogin?'+urllib.parse.urlencode({**self.CLIENT,'request_id':request_id}))
        token = ((data.get('data') or {}).get('members') or {}).get('token')
        if not token:raise NetDiskError('夸克没有给出登录二维码，请稍后再试。')
        link = self.SCAN+'?'+urllib.parse.urlencode({'token':token,'client_id':532,'ssb':'weblogin','uc_param_str':'',
                                                      'uc_biz_str':'S:custom|OPT:SAREA@0|OPT:IMMERSIVE@1|OPT:BACK_BTN_STYLE@0'})
        return {'link':link,'qr':[''.join('1' if v else '0' for v in row) for row in matrix(link)],'_token':token,'_request':request_id}

    def poll(self,attempt):
        time.sleep(POLL)
        state = self.login_state
        data = self.session.json('https://uop.quark.cn/cas/ajax/getServiceTicketByQrcodeToken?'+urllib.parse.urlencode(
            {**self.CLIENT,'token':state.get('_token',''),'request_id':state.get('_request','')}))
        if attempt!=self.attempt:return True
        code = data.get('status')
        if code==2000000:
            self.login_state = {'state':'checking'}
            ticket = data['data']['members']['service_ticket']
            info = self.session.json('https://pan.quark.cn/account/info?'+urllib.parse.urlencode({'st':ticket,'lw':'scan'}))
            if not info.get('success'):raise NetDiskError('夸克没有完成登录：'+str(info.get('msg') or '请重新扫码。'))
            self.signed(attempt,(info.get('data') or {}).get('nickname'))
            return True
        # The QR code lasts about three minutes; the web page then shows a new one, and so does this.
        if code==50004002:self.login_state = {'state':'waiting',**self.qr()}
        elif code!=50004001:raise NetDiskError('夸克登录没有完成：'+str(data.get('message') or code))
        return False

    def api(self,path,data=None,**params):
        result = self.session.json(self.API+path+'?'+urllib.parse.urlencode({'pr':'ucpro','fr':'pc',**params}),data,
                                   headers={'Referer':'https://pan.quark.cn/','Origin':'https://pan.quark.cn'})
        if result.get('code')==31001 or result.get('http')==401:self.expired()
        if result.get('code'):raise NetDiskError('夸克网盘：'+str(result.get('message') or result['code']))
        return result

    def files(self,link):
        token = self.api('share/sharepage/token',{'pwd_id':link['id'],'passcode':link['code']})['data']['stoken']
        share = {'id':link['id'],'stoken':token}
        found,folders = [],[('0','')]
        while folders and len(found)<MAX_FILES:
            folder,prefix = folders.pop(0)
            page = 1
            while True:
                data = self.api('share/sharepage/detail',pwd_id=link['id'],stoken=token,pdir_fid=folder,force=0,_page=page,_size=50,
                                _fetch_banner=0,_fetch_share=0,_fetch_total=1,_sort='file_type:asc,file_name:asc')
                items = data['data']['list']
                for item in items:
                    path = prefix+item['file_name']
                    if item.get('dir'):folders.append((item['fid'],path+'/'))
                    else:found.append({'name':item['file_name'],'path':path,'size':int(item.get('size') or 0),
                                       '_fid':item['fid'],'_token':item['share_fid_token'],'_parent':folder})
                if not items or page*50>=int((data.get('metadata') or {}).get('_total') or 0):break
                page += 1
        return share,found

    def folder(self):
        existing = self.api('file/info/path_list',{'file_path':['/'+FOLDER],'namespace':'0'}).get('data') or []
        if existing:return existing[0]['fid']
        return self.api('file',{'pdir_fid':'0','file_name':FOLDER,'dir_path':'','dir_init_lock':False})['data']['fid']

    def download(self,share,files,work):
        target = self.folder()
        groups = {}
        for item in files:groups.setdefault(item['_parent'],[]).append(item)
        for parent,items in groups.items():
            for start in range(0,len(items),50):
                batch = items[start:start+50]
                task = self.api('share/sharepage/save',{'fid_list':[i['_fid'] for i in batch],'fid_token_list':[i['_token'] for i in batch],
                    'to_pdir_fid':target,'pwd_id':share['id'],'stoken':share['stoken'],'pdir_fid':parent,'scene':'link'},
                    uc_param_str='',app='clouddrive',__dt=600,__t=int(time.time()*1000))['data']['task_id']
                for retry in range(120):
                    done = self.api('task',task_id=task,retry_index=retry,__dt=600,__t=int(time.time()*1000))['data']
                    if done.get('status')==2:break
                    time.sleep(1)
                else:raise NetDiskError('夸克网盘转存太久没有完成，请稍后再试。')
                saved = (done.get('save_as') or {}).get('save_as_top_fids') or []
                links = {}
                if saved:
                    for entry in self.api('file/download',{'fids':saved}).get('data') or []:links[entry.get('fid')] = entry
                for item,fid in zip(batch,saved):
                    entry = links.get(fid) or {}
                    if not entry.get('download_url'):yield item,'夸克网盘没有给出下载地址';continue
                    yield item,self.fetch_file(entry['download_url'],item,work,{'Referer':'https://pan.quark.cn/'})
                for item in batch[len(saved):]:yield item,'没有转存成功'

class Baidu(Drive):
    key,label,app = 'baidu','百度网盘','百度网盘 App'
    COMMON = {'channel':'chunlei','web':1,'app_id':250528,'clienttype':0}
    ERRORS = {-9:'提取码不对，或者分享已失效。',105:'链接不对，或者分享已失效。',-12:'需要提取码，请把提取码一起粘贴进来。',
              9019:'需要提取码，请把提取码一起粘贴进来。',-62:'尝试次数太多，百度要求验证码，请过一会儿再试。',
              -21:'分享已被取消。',-70:'分享里有违规文件，百度不让转存。',2:'参数不对。',4:'转存失败：网盘里已有同名文件或空间不足。',
              12:'转存失败：文件太多或者网盘空间不足。',120:'转存失败：这个分享被限制了。',-10:'网盘空间不足，转存不了。',
              -33:'一次转存的文件太多，超过了百度的限制。'}

    @property
    def signed_in(self):
        return bool(self.session.cookie('BDUSS') and self.session.cookie('STOKEN'))

    def qr(self):
        self.session.send('https://pan.baidu.com/')
        self.login_state['_gid'] = gid = self.login_state.get('_gid') or str(uuid.uuid4()).upper()
        now = int(time.time()*1000)
        data = self.session.json('https://passport.baidu.com/v2/api/getqrcode?'+urllib.parse.urlencode(
            {'lp':'pc','qrloginfrom':'pc','gid':gid,'apiver':'v3','tt':now,'tpl':'netdisk','_':now}))
        if not data.get('sign') or not data.get('imgurl'):raise NetDiskError('百度没有给出登录二维码，请稍后再试。')
        return {'image':'https://'+data['imgurl'].lstrip('/'),'_sign':data['sign'],'_gid':gid}

    def poll(self,attempt):
        state,now = self.login_state,int(time.time()*1000)
        # Baidu holds this request open until the phone does something, or for about half a minute.
        data = self.session.json('https://passport.baidu.com/channel/unicast?'+urllib.parse.urlencode(
            {'channel_id':state.get('_sign',''),'tpl':'netdisk','gid':state.get('_gid',''),'callback':'','apiver':'v3','tt':now,'_':now}),timeout=70)
        if attempt!=self.attempt:return True
        if data.get('errno')==1:return False
        if data.get('errno')!=0:
            self.login_state = {'state':'waiting',**self.qr()};return False
        value = json.loads(data.get('channel_v') or '{}')
        if value.get('status')==1:self.login_state = {**self.login_state,'state':'scanned'};return False
        if value.get('status')==2:raise NetDiskError('你在手机上取消了登录。')
        if value.get('status')!=0 or not value.get('v'):return False
        self.login_state = {'state':'checking'}
        now = int(time.time()*1000)
        _,raw,_ = self.session.send('https://passport.baidu.com/v3/login/main/qrbdusslogin?'+urllib.parse.urlencode({
            'v':now,'bduss':value['v'],'u':'https://pan.baidu.com/disk/home','loginVersion':'v4','qrcode':1,'tpl':'netdisk','apiver':'v3',
            'tt':now,'traceid':'','time':now//1000,'alg':'v3','sig':'','elapsed':1,'shaOne':'','rinfo':'{"fuid":""}','callback':''}))
        # Not quite JSON (one key is single-quoted), so only the result code is read.
        code = re.search(r'"code"\s*:\s*"?(\d+)',raw.decode('utf-8','replace'))
        if not code or code.group(1)!='110000':raise NetDiskError('百度没有完成登录：'+(code.group(1) if code else '请重新扫码。'))
        # The drive's own STOKEN comes from passing through passport's sign-in for the netdisk.
        self.session.send('https://passport.baidu.com/v3/login/api/auth/?'+urllib.parse.urlencode({'return_type':5,'tpl':'netdisk','u':'https://pan.baidu.com/disk/home'}))
        info = self.api('api/gettemplatevariable',fields='["bdstoken","username"]')['result']
        self.signed(attempt,info.get('username'))
        return True

    def api(self,path,form=None,referer='https://pan.baidu.com/disk/main',**params):
        result = self.session.json('https://pan.baidu.com/'+path+'?'+urllib.parse.urlencode({**self.COMMON,**params}),form,form=True,headers={'Referer':referer})
        errno = result.get('errno',0)
        if errno==-6:self.expired()
        if errno:raise NetDiskError('百度网盘：'+self.ERRORS.get(errno,f'出错了（{errno}）'))
        return result

    def files(self,link):
        referer = 'https://pan.baidu.com/s/1'+link['id']
        share = {'id':link['id'],'referer':referer,'key':''}
        if link['code']:
            data = self.api('share/verify',{'pwd':link['code'],'vcode':'','vcode_str':''},referer,surl=link['id'],t=int(time.time()*1000))
            share['key'] = urllib.parse.unquote(data['randsk'])
            # The share pages read the verified key from this cookie.
            self.session.jar.set_cookie(Cookie(0,'BDCLND',data['randsk'],None,False,'.baidu.com',True,True,'/',True,False,None,True,None,None,{}))
        found,folders,first = [],[None],None
        while folders and len(found)<MAX_FILES:
            folder = folders.pop(0)
            page = 1
            while True:
                params = {'shorturl':link['id'],'page':page,'num':100,'order':'name','desc':0,**({'dir':folder} if folder else {'root':1})}
                data = self.api('share/list',None,referer,**params)
                if first is None:first = data;share.update(share_id=data['share_id'],uk=data['uk'])
                items = data.get('list') or []
                for item in items:
                    if str(item.get('isdir'))=='1':folders.append(item['path'])
                    else:found.append({'name':item['server_filename'],'path':item['path'].lstrip('/'),'size':int(item.get('size') or 0),'_fsid':int(item['fs_id'])})
                if len(items)<100:break
                page += 1
        return share,found

    def folder(self,token):
        path = '/'+FOLDER
        try:self.api('api/list',dir=path,num=1)
        except NetDiskError as exc:
            if exc.login:raise
            self.api('api/create',{'path':path,'isdir':1,'block_list':'[]'},a='commit',bdstoken=token)
        return path

    def download(self,share,files,work):
        token = self.api('api/gettemplatevariable',fields='["bdstoken"]')['result']['bdstoken']
        target = self.folder(token)
        for start in range(0,len(files),100):
            batch = files[start:start+100]
            data = self.api('share/transfer',{'fsidlist':json.dumps([i['_fsid'] for i in batch]),'path':target},share['referer'],
                            shareid=share['share_id'],sekey=share['key'],ondup='newcopy',bdstoken=token,**{'from':share['uk'],'async':1})
            moved = {entry['from_fs_id']:entry['to'] for entry in (data.get('extra') or {}).get('list') or []}
            paths = [moved[i['_fsid']] for i in batch if i['_fsid'] in moved]
            links = {}
            if paths:
                for entry in self.api('api/filemetas',target=json.dumps(paths,ensure_ascii=False),dlink=1,bdstoken=token).get('info') or []:
                    links[entry.get('path')] = entry.get('dlink')
            for item in batch:
                dlink = links.get(moved.get(item['_fsid']))
                if not dlink:yield item,'没有转存成功';continue
                # Baidu serves its download links to its own client's user agent.
                yield item,self.fetch_file(dlink,item,work,{'User-Agent':'pan.baidu.com'})


QUARK = re.compile(r'pan\.quark\.cn/s/([0-9A-Za-z]+)')
BAIDU = re.compile(r'pan\.baidu\.com/(?:s/1([\w-]+)|share/init\?surl=([\w-]+))')
CODE = re.compile(r'(?:提取码|密码|访问码|pwd|passcode)\s*[:：=]?\s*([0-9A-Za-z]{4,8})',re.I)


def links(text):
    """Every share link in pasted text, with the extraction code written after it or in its ?pwd=."""
    text = str(text)
    found = sorted([(m.start(),'quark',m.group(1)) for m in QUARK.finditer(text)]+
                   [(m.start(),'baidu',m.group(1) or m.group(2)) for m in BAIDU.finditer(text)])
    result = []
    for i,(start,drive,key) in enumerate(found):
        end = found[i+1][0] if i+1<len(found) else len(text)
        code = CODE.search(text,start,end)
        result.append({'drive':drive,'id':key,'code':code.group(1) if code else ''})
    return result


class NetDisk:
    def __init__(self,lib):
        self.lib = lib
        self.drives = {drive.key:drive for drive in (Quark(lib),Baidu(lib))}
        self.tasks = {}

    def drive(self,key):
        if key not in self.drives:raise NetDiskError('只支持夸克网盘和百度网盘。')
        return self.drives[key]

    def status(self):
        return {key:drive.status() for key,drive in self.drives.items()}

    def fetch(self,text):
        found = links(text)
        if not found:raise NetDiskError('没有找到夸克网盘或百度网盘的分享链接。')
        for link in found:
            drive = self.drives[link['drive']]
            if not drive.signed_in:raise NetDiskError(f'请先用{drive.app}扫码登录{drive.label}。',login=drive.key)
        task = {'id':uuid.uuid4().hex[:12],'state':'reading','message':'正在读取分享链接…','books':[],'notes':[],'failed':[],'skipped':[],'login':''}
        self.tasks = {k:v for k,v in self.tasks.items() if v['state'] not in ('done','failed')}
        self.tasks[task['id']] = task
        threading.Thread(target=self._run,args=(task,found),daemon=True).start()
        return task

    def task(self,task_id):
        if task_id not in self.tasks:raise NetDiskError('找不到这次下载，可能书屿重新启动过。')
        return self.tasks[task_id]

    def _run(self,task,found):
        work = self.lib.data_dir/'netdisk'/task['id']
        work.mkdir(parents=True,exist_ok=True)
        try:
            for number,link in enumerate(found,1):
                drive = self.drives[link['drive']]
                where = f'{drive.label}链接' if len(found)==1 else f'第 {number} 个链接（{drive.label}）'
                task.update(state='reading',message=f'正在读取{where}…')
                try:share,files = drive.files(link)
                except NetDiskError as exc:
                    if exc.login:raise
                    task['failed'].append(f'{where}：{exc}');continue
                keep = []
                for item in files:
                    reason = wanted(item['name'],item['size'])
                    if reason:task['skipped'].append(f'{item["name"]}（{reason}）')
                    else:keep.append(item)
                if not keep:
                    task['failed'].append(f'{where}：里面没有 TXT、MD、EPUB 小说或压缩包。');continue
                task.update(state='saving',message=f'正在把 {len(keep)} 个文件转存到你{drive.label}的「{FOLDER}」文件夹…')
                try:
                    for done,(item,result) in enumerate(drive.download(share,keep,work),1):
                        if isinstance(result,str):task['failed'].append(f'{item["name"]}：{result}');continue
                        task.update(state='downloading',message=f'已下载 {done} / {len(keep)}：{item["name"]}')
                        self._store(task,item,result)
                except NetDiskError as exc:
                    if exc.login:raise
                    task['failed'].append(f'{where}：{exc}')
            task.update(state='done',message='')
        except NetDiskError as exc:
            task.update(state='failed',message=str(exc),login=exc.login)
        except (KeyError,TypeError,ValueError,AttributeError) as exc:
            task.update(state='failed',message='网盘的接口可能变了，没能完成下载。')
        finally:
            shutil.rmtree(work,ignore_errors=True)

    def _store(self,task,item,path):
        """Into the library through Lite's upload, so the file is named, indexed and unpacked the same way."""
        try:
            with path.open('rb') as stream:result = self.lib.uploads.receive(stream,path.stat().st_size,item['name'])
        except (CleanError,ArchiveError,OSError,ValueError) as exc:
            task['failed'].append(f'{item["name"]}：{exc}');return
        finally:
            path.unlink(missing_ok=True)
        if 'books' in result:
            task['books'].extend(result['books'])
            task['notes'].append(f'{item["name"]}：取出 {len(result["books"])} 本小说'+(f'，另有 {result["other_files"]} 个其他文件没有导入' if result.get('other_files') else '')
                                 +(f'；{len(result["failed"])} 处没能取出' if result.get('failed') else ''))
        else:task['books'].append(result)
