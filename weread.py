"""Send finished novels to the reader's WeRead (微信读书) shelf, the way its web upload page does.

WeRead has no public upload API. This follows weread.qq.com/web/upload: sign in by scanning a QR code with WeChat,
fetch temporary Tencent COS credentials, upload the file to COS, then ask WeRead to import it."""
from __future__ import annotations

from email.utils import parsedate_to_datetime
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import json
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from ai_cleaner import CleanError, protect_secret
from lite_upload import clean_name
from qr_code import matrix

WEREAD = 'https://weread.qq.com'
SETTING = 'weread'
AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'
# WeRead answers these when the sign-in has lapsed; its page then renews the session once and retries.
EXPIRED = {-2012,-2010}
RENEWED = -12013
ACCEPTED = {'.txt','.md','.epub'}
PART = 8*1024*1024
LOGIN_SECONDS = 300
IMPORT_SECONDS = 300
POLL = 2


class WeReadError(Exception):
    def __init__(self,message,login=False):
        super().__init__(message)
        self.login = login


def fetch(request,timeout):
    """(status, headers, body) for one request; tests replace this."""
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response:
            return response.status,response.headers,response.read()
    except urllib.error.HTTPError as exc:
        return exc.code,exc.headers,exc.read()
    except (urllib.error.URLError,TimeoutError,OSError) as exc:
        raise WeReadError('连不上微信读书，请检查 Windows 电脑的网络后再试。') from exc


def cos_authorization(secret_id,secret_key,method,path,params,headers,now=None):
    """Tencent COS request signature (XML API, q-sign-algorithm=sha1)."""
    now = int(now or time.time())
    key_time = f'{now-60};{now+3600}'
    encode = lambda items:sorted((urllib.parse.quote(k.lower(),safe=''),urllib.parse.quote(v,safe='')) for k,v in items.items())
    query,signed = encode(params),encode(headers)
    text = f'{method.lower()}\n{path}\n'+'&'.join(f'{k}={v}' for k,v in query)+'\n'+'&'.join(f'{k}={v}' for k,v in signed)+'\n'
    sign_key = hmac.new(secret_key.encode(),key_time.encode(),hashlib.sha1).hexdigest()
    string = f'sha1\n{key_time}\n{hashlib.sha1(text.encode()).hexdigest()}\n'
    signature = hmac.new(sign_key.encode(),string.encode(),hashlib.sha1).hexdigest()
    return (f'q-sign-algorithm=sha1&q-ak={secret_id}&q-sign-time={key_time}&q-key-time={key_time}'
            f'&q-header-list={";".join(k for k,_ in signed)}&q-url-param-list={";".join(k for k,_ in query)}&q-signature={signature}')


class WeRead:
    def __init__(self,lib):
        self.lib = lib
        self.lock = threading.Lock()
        self.login_state = {'state':'idle'}
        self.attempt = 0
        self.tasks = {}
        values = self._values()
        try:self.cookies = json.loads(protect_secret(values['cookies'],True)) if values.get('cookies') else {}
        except (CleanError,ValueError):self.cookies = {}
        self.name = values.get('name','')
        # WeRead's page sends a browser fingerprint when signing in; one random value stands in for this machine.
        self.fingerprint = values.get('fp') or secrets.token_hex(16)

    def _values(self):
        with self.lib.db() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?',(SETTING,)).fetchone()
        return json.loads(row['value']) if row else {}

    def _save(self):
        values = {'name':self.name,'fp':self.fingerprint}
        if self.cookies:values['cookies'] = protect_secret(json.dumps(self.cookies))
        with self.lib.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(SETTING,json.dumps(values,ensure_ascii=False)))

    @property
    def signed_in(self):
        # The cookies arrive one call before signing in has finished.
        return bool(self.cookies.get('wr_vid') and self.cookies.get('wr_skey')) and self.login_state.get('state')!='checking'

    def status(self):
        login = {k:v for k,v in self.login_state.items() if k!='params'}
        return {'signed_in':self.signed_in,'name':self.name,'login':login}

    def _keep(self,headers):
        """Take the cookies WeRead sets, and drop the ones it clears."""
        changed = False
        for header in (headers.get_all('Set-Cookie') or []) if headers else []:
            jar = SimpleCookie()
            try:jar.load(header)
            except CookieError:continue
            for name,morsel in jar.items():
                expired = morsel['max-age']=='0'
                if morsel['expires']:
                    try:expired = expired or parsedate_to_datetime(morsel['expires']).timestamp()<time.time()
                    except (TypeError,ValueError):pass
                if expired or not morsel.value:changed = self.cookies.pop(name,None) is not None or changed
                elif self.cookies.get(name)!=morsel.value:self.cookies[name] = morsel.value;changed = True
        return changed

    def _call(self,path,data=None,renew=True,check=True):
        headers = {'User-Agent':AGENT,'Accept':'application/json, text/plain, */*','Origin':WEREAD,'Referer':WEREAD+'/web/upload'}
        if self.cookies:headers['Cookie'] = '; '.join(f'{k}={v}' for k,v in self.cookies.items())
        body = None
        if data is not None:
            body = json.dumps(data,ensure_ascii=False).encode();headers['Content-Type'] = 'application/json;charset=UTF-8'
        status,response_headers,raw = fetch(urllib.request.Request(WEREAD+path,data=body,headers=headers,method='POST' if body else 'GET'),90)
        if self._keep(response_headers) and self.signed_in:self._save()
        try:result = json.loads(raw or b'{}')
        except ValueError:raise WeReadError(f'微信读书返回了无法识别的内容（HTTP {status}）。')
        if not isinstance(result,dict):raise WeReadError('微信读书返回了无法识别的内容。')
        code = result.get('errCode',result.get('errcode'))
        if not check:return result
        if code in EXPIRED:
            if renew and self.cookies and self._renew(path):return self._call(path,data,False)
            self.forget()
            raise WeReadError('微信读书的登录已过期，请重新扫码登录。',login=True)
        if code or status>=400:
            raise WeReadError('微信读书：'+str(result.get('errMsg') or result.get('errmsg') or f'请求失败（{code or status}）'))
        return result

    def _renew(self,path):
        try:
            result = self._call('/web/login/renewal',{'rq':urllib.parse.quote(path.split('?')[0],safe=''),'ql':self.cookies.get('wr_ql')=='1'},check=False)
        except WeReadError:
            return False
        return 1 in (result.get('succ'),result.get('success')) or result.get('errCode')==RENEWED

    def forget(self):
        with self.lock:
            self.cookies,self.name = {},''
            self._save()

    # Signing in: a QR code for WeChat, WeRead's long poll for the scan, then its two login calls.
    def login(self,otp=''):
        if otp:
            with self.lock:
                if self.login_state.get('state')!='verify':raise WeReadError('请先扫码。')
                params = {**self.login_state['params'],'otp':str(otp).strip()}
                self.login_state = {'state':'checking'}
            threading.Thread(target=self._finish,args=(params,self.attempt),daemon=True).start()
            return self.status()
        with self.lock:
            self.cookies = {}
            self.attempt += 1;attempt = self.attempt
        uid = self._call('/web/login/getuid',{},check=False).get('uid')
        if not uid:raise WeReadError('微信读书没有给出登录二维码，请稍后再试。')
        link = f'{WEREAD}/web/confirm?'+urllib.parse.urlencode({'pf':2,'uid':uid})
        self.login_state = {'state':'waiting','link':link,'qr':[''.join('1' if v else '0' for v in row) for row in matrix(link)],
                            'expires':time.time()+LOGIN_SECONDS}
        status = self.status()
        threading.Thread(target=self._wait,args=(uid,attempt),daemon=True).start()
        return status

    def _wait(self,uid,attempt):
        deadline = time.time()+LOGIN_SECONDS
        while attempt==self.attempt and time.time()<deadline:
            try:info = self._call('/web/login/getinfo',{'uid':uid},check=False)
            except WeReadError as exc:
                if attempt==self.attempt:self.login_state = {'state':'failed','error':str(exc)}
                return
            if attempt!=self.attempt:return
            if info.get('vid') and info.get('skey') and info.get('code'):
                self.login_state = {'state':'checking'}
                return self._finish({'vid':info['vid'],'skey':info['skey'],'code':info['code'],'fp':self.fingerprint,
                                     'isAutoLogout':0,'uid':uid,'pf':info.get('pf',0)},attempt)
            # WeRead holds this request open for about a minute and answers {"scan":0} when nothing happened.
            if info.get('scan')==1:self.login_state = {**self.login_state,'state':'scanned'}
        if attempt==self.attempt:self.login_state = {'state':'failed','error':'二维码已过期，请重新获取。'}

    def _finish(self,params,attempt):
        try:
            pf = params.pop('pf',0)
            data = self._call('/web/login/weblogin',params,check=False)
            if attempt!=self.attempt:return
            if data.get('needRemoteLoginVerify') or data.get('isVerifyFailed'):
                self.login_state = {'state':'verify','params':{**params,'pf':pf},
                                    'error':'验证码不对，请再输一次。' if data.get('isVerifyFailed') else ''}
                return
            if data.get('verifyCodeExpired'):raise WeReadError('验证码已过期，请重新扫码。')
            if not (data.get('vid') and data.get('accessToken') and data.get('refreshToken')):
                raise WeReadError('微信读书没有完成登录：'+str(data.get('errMsg') or '请重新扫码。'))
            session = self._call('/web/login/session/init',{'vid':data['vid'],'skey':data['accessToken'],'rt':data['refreshToken'],'pf':pf,'ql':False},check=False)
            if session.get('success')!=1:raise WeReadError('微信读书没有完成登录：'+str(session.get('errMsg') or '请重新扫码。'))
            with self.lock:
                # session/init sets these cookies; the tokens themselves stand in if it did not.
                for name,value in [('wr_vid',data['vid']),('wr_skey',data['accessToken']),('wr_rt',data['refreshToken'])]:
                    self.cookies.setdefault(name,str(value))
            # Only shown to tell accounts apart, so signing in still counts when this fails.
            try:self.name = str(self._call('/web/user?'+urllib.parse.urlencode({'userVid':self.cookies['wr_vid']}),renew=False).get('name',''))[:40]
            except WeReadError:pass
            with self.lock:self._save()
            # Signed in only once everything is saved, so the pages never show an account without its name.
            self.login_state = {'state':'idle'}
        except WeReadError as exc:
            if attempt==self.attempt:self.login_state = {'state':'failed','error':str(exc)}

    # Uploading: runs in the background, the page polls the task.
    def upload(self,book_id,version_id=''):
        if not self.signed_in:raise WeReadError('请先扫码登录微信读书。',login=True)
        book = self.lib.book(book_id)
        path = self.lib.version_path(book,version_id)
        ext = path.suffix.lower()
        if path.is_dir() or ext not in ACCEPTED:raise WeReadError('只能把 TXT、MD 或 EPUB 小说传到微信读书。')
        name = (clean_name(book['title'],60) or Path(book['name']).stem)+ext
        task = {'id':uuid.uuid4().hex[:12],'state':'uploading','title':book['title'],'error':'','login':False}
        self.tasks = {k:v for k,v in self.tasks.items() if v['state'] in ('uploading','importing')}
        self.tasks[task['id']] = task
        threading.Thread(target=self._upload,args=(task,path,name),daemon=True).start()
        return task

    def task(self,task_id):
        if task_id not in self.tasks:raise WeReadError('找不到这次上传，可能书屿重新启动过。')
        return self.tasks[task_id]

    def _upload(self,task,path,name):
        try:
            credential = self._call('/web/cos/getcredential?'+urllib.parse.urlencode({'name':name}))
            keys = credential['Response']['Credentials']
            host = f"{credential['bucket']}.cos.{credential['region']}.myqcloud.com"
            key = '/'+credential['ObjectName'].lstrip('/')
            cos = lambda method,params,body=b'',extra=None:self._cos(host,keys,method,key,params,body,extra)
            upload_id = re.search(rb'<UploadId>([^<]+)</UploadId>',cos('POST',{'uploads':''})[1]).group(1).decode()
            parts = []
            with path.open('rb') as source:
                while chunk := source.read(PART):
                    headers,_ = cos('PUT',{'partNumber':str(len(parts)+1),'uploadId':upload_id},chunk)
                    parts.append(headers.get('ETag',''))
            done = '<CompleteMultipartUpload>'+''.join(f'<Part><PartNumber>{n}</PartNumber><ETag>{etag}</ETag></Part>' for n,etag in enumerate(parts,1))+'</CompleteMultipartUpload>'
            cos('POST',{'uploadId':upload_id},done.encode(),{'Content-Type':'application/xml'})
            task['state'] = 'importing'
            # WeRead converts the file after it lands; it answers status 1 while working and 0 with the book id when done.
            deadline = time.time()+IMPORT_SECONDS
            while True:
                result = self._call('/web/cos/notify',{'path':key.lstrip('/'),'name':name})
                if result.get('status')==0 and result.get('bookId'):
                    task.update(state='done',book_id=result['bookId']);return
                if result.get('status')!=1:
                    raise WeReadError('微信读书没能导入这本书：'+str(result.get('errMsg') or result.get('errmsg') or result.get('status')))
                if time.time()>deadline:raise WeReadError('微信读书还在导入，稍后到书架上看看有没有出现。')
                time.sleep(POLL)
        except WeReadError as exc:
            task.update(state='failed',error=str(exc),login=exc.login)
        except (KeyError,TypeError,AttributeError,ValueError,OSError):
            task.update(state='failed',error='上传没有完成：微信读书的上传方式可能变了。')

    def _cos(self,host,keys,method,key,params,body,extra):
        headers = {'Host':host}
        authorization = cos_authorization(keys['TmpSecretId'],keys['TmpSecretKey'],method,key,params,headers)
        query = '&'.join(k if v=='' else f'{k}={urllib.parse.quote(v,safe="")}' for k,v in params.items())
        request = urllib.request.Request(f'https://{host}{urllib.parse.quote(key)}?{query}',data=body,method=method,
            headers={**headers,**(extra or {}),'Authorization':authorization,'x-cos-security-token':keys['Token'],'User-Agent':AGENT})
        status,headers,raw = fetch(request,300)
        if status>=300:
            message = re.search(rb'<Message>([^<]*)</Message>',raw or b'')
            raise WeReadError('传到微信读书的存储没有成功：'+(message.group(1).decode(errors='replace') if message else f'HTTP {status}'))
        return headers,raw
