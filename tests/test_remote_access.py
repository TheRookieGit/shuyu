import io
import json
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile

from server import Handler, ThreadingHTTPServer
from tests.test_library import LibraryFixture

PAGES = 'https://reader.github.io'


class WebAccessTests(LibraryFixture,unittest.TestCase):
    """The hosted page is simulated with Origin/Host headers; requests never leave loopback."""
    def setUp(self):
        super().setUp()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.library=self.lib;self.server.allowed_origins={PAGES}
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        # What a tunnel such as Tailscale Funnel forwards to the loopback port.
        self.tunnel={'Host':'pc.tailnet.ts.net','X-Forwarded-For':'203.0.113.9','Origin':PAGES}
        self.token=''

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()

    def request(self,path,data=None,remote=True,headers=None,method=None):
        extra=dict(self.tunnel) if remote else {}
        if self.token:extra['Authorization']='Bearer '+self.token
        if data is not None:extra['Content-Type']='application/json'
        request=urllib.request.Request(self.base+path,data=None if data is None else json.dumps(data).encode(),headers={**extra,**(headers or {})},method=method)
        return urllib.request.urlopen(request)

    def status(self,path,data=None,**kwargs):
        try:
            with self.request(path,data,**kwargs) as response:return response.status
        except urllib.error.HTTPError as exc:
            exc.close();return exc.code

    def login(self,password):
        with self.request('/api/login',{'password':password}) as response:
            self.assertEqual(response.headers['Access-Control-Allow-Origin'],PAGES)
            self.token=json.load(response)['token']

    def test_hosted_page_requires_password_and_local_stays_trusted(self):
        self.assertEqual(self.status('/api/stats'),401)
        self.assertEqual(self.status('/api/login',{'password':'whatever'}),403)
        # Only the Windows machine may set the password, even through a tunnel to loopback.
        self.assertEqual(self.status('/api/access/password',{'password':'secret-1'}),403)
        self.assertEqual(self.status('/api/access/password',{'password':'short'},remote=False),400)
        self.assertEqual(self.status('/api/access/password',{'password':'secret-1'},remote=False),200)
        self.assertEqual(self.status('/api/stats',remote=False),200)
        self.assertEqual(self.status('/api/login',{'password':'wrong-one'}),403)
        self.login('secret-1')
        self.assertEqual(self.status('/api/stats'),200)
        with self.request('/api/session') as response:
            self.assertEqual(json.load(response),{'local':False,'authenticated':True,'password_set':True})
        self.assertEqual(self.status('/api/logout',{}),200)
        self.assertEqual(self.status('/api/stats'),401)
        self.login('secret-1')
        # Changing the password signs every hosted page out.
        self.assertEqual(self.status('/api/access/password',{'password':'secret-2'},remote=False),200)
        self.assertEqual(self.status('/api/stats'),401)

    def test_cors_only_for_allowed_origin(self):
        with self.request('/api/stats',method='OPTIONS',headers={'Access-Control-Request-Headers':'authorization'}) as response:
            self.assertEqual(response.status,204)
            self.assertEqual(response.headers['Access-Control-Allow-Origin'],PAGES)
            self.assertIn('Authorization',response.headers['Access-Control-Allow-Headers'])
        evil={'Origin':'https://evil.example'}
        self.assertEqual(self.status('/api/stats',method='OPTIONS',headers=evil),403)
        self.assertEqual(self.status('/api/session',headers=evil),403)
        # An allowed page calling 127.0.0.1 directly still has to log in.
        self.assertEqual(self.status('/api/stats',remote=False,headers={'Origin':PAGES}),401)

    def test_hosted_page_cannot_act_on_windows_desktop(self):
        self.lib.access.set_password('secret-1');self.login('secret-1')
        book=self.book('夜航')
        with patch('server.reveal_in_explorer') as reveal:
            self.assertEqual(self.status('/api/reveal',{'id':book['id']}),403)
            self.assertEqual(self.status('/api/open',{'id':book['id']}),403)
            reveal.assert_not_called()

    def download(self,book_id):
        with self.request('/api/download/ticket',{'id':book_id}) as response:url=json.load(response)['url']
        token,self.token=self.token,''
        try:
            # The browser navigates to the link without the login header or an Origin.
            with self.request(url,remote=False,headers={'Host':'pc.tailnet.ts.net'}) as response:
                return response.read(),response.headers
        finally:self.token=token

    def test_download_tickets_for_archived_book_and_image_folder(self):
        self.lib.access.set_password('secret-1')
        self.assertEqual(self.status('/api/download/ticket',{'id':self.book('夜航')['id']}),401)
        self.login('secret-1')
        body,headers=self.download(self.book('夜航')['id'])
        self.assertEqual(body,'嵌套三层里的小说。'.encode())
        self.assertIn("filename*=UTF-8''",headers['Content-Disposition'])
        folder=next(item for item in self.lib.listing({'limit':100})['items'] if item['ext']=='folder')
        body,_=self.download(folder['id'])
        with zipfile.ZipFile(io.BytesIO(body)) as archive:self.assertEqual(sorted(archive.namelist()),['1.jpg','2.jpg'])
        # The server removes the temporary ZIP just after the response finishes.
        deadline=time.time()+5
        while any((self.data/'downloads').iterdir()) and time.time()<deadline:time.sleep(.05)
        self.assertFalse(any((self.data/'downloads').iterdir()))
        self.assertEqual(self.status('/api/download?ticket=forged',remote=False),403)

    def test_tickets_are_single_use(self):
        self.lib.access.set_password('secret-1');self.login('secret-1')
        ticket=self.lib.access.ticket(self.book('夜航')['id'])
        self.assertEqual(self.status('/api/download?ticket='+ticket,remote=False),200)
        self.assertEqual(self.status('/api/download?ticket='+ticket,remote=False),403)

    def test_sessions_in_use_keep_renewing(self):
        self.lib.access.set_password('secret-1');self.login('secret-1')
        digest=self.lib.access._digest(self.token)
        with self.lib.db() as db:db.execute('UPDATE sessions SET expires=? WHERE token_hash=?',(time.time()+3600,digest))
        self.assertEqual(self.status('/api/books'),200)
        with self.lib.db() as db:expires=db.execute('SELECT expires FROM sessions WHERE token_hash=?',(digest,)).fetchone()[0]
        self.assertGreater(expires,time.time()+300*86400)
        with self.lib.db() as db:db.execute('UPDATE sessions SET expires=? WHERE token_hash=?',(time.time()-1,digest))
        self.assertEqual(self.status('/api/books'),401)

    def test_repeated_wrong_passwords_lock_out(self):
        self.lib.access.set_password('secret-1')
        for _ in range(5):self.assertEqual(self.status('/api/login',{'password':'nope-nope'}),403)
        try:
            self.request('/api/login',{'password':'secret-1'})
            self.fail('expected lockout')
        except urllib.error.HTTPError as exc:
            self.assertIn('尝试次数过多',json.load(exc)['error']);exc.close()


if __name__=='__main__':unittest.main()
