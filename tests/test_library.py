import ctypes
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request
import zipfile

from archive_engine import ArchiveEngine, ArchiveError, member_key, source_chain, zip_label
from platform_utils import reveal_in_explorer
from server import Library, Handler, ThreadingHTTPServer

TOOL = Path(__file__).resolve().parents[1]/'.data/tools/7zip-26.04/7z.exe'
TOOL = str(TOOL) if TOOL.is_file() else shutil.which('7z')


def packed(files):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as zf:
        for name,content in files.items():zf.writestr(name,content)
    return stream.getvalue()


class LibraryFixture:
    def setUp(self):
        self.tool_patch=patch.object(ArchiveEngine,'ensure_tool',return_value=TOOL)
        self.tool_patch.start()
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/'books'
        self.root.mkdir()
        self.data=Path(self.temp.name)/'data'
        (self.root/'未整理').mkdir()
        (self.root/'未整理'/'《春日》作者：小林.txt').write_text('这是一本小说。',encoding='utf-8')
        (self.root/'春日.epub').write_bytes(b'epub fixture')
        (self.root/'sample.pdf').write_bytes(b'%PDF-1.4')
        (self.root/'漫画').mkdir()
        for name in ['1.jpg','2.jpg']:(self.root/'漫画'/name).write_bytes(b'image')
        innermost=packed({'书/《夜航》作者：小舟.txt':'嵌套三层里的小说。'.encode()})
        inner=packed({'深一层.zip':innermost,'普通故事.pdf':b'%PDF-1.4'})
        (self.root/'合集.zip').write_bytes(packed({'第一层/第二层.zip':inner,'《故事》作者：山川.txt':'包内小说。'.encode(),'封面.jpg':b'image'}))
        (self.root/'漫画.zip').write_bytes(packed({'1.jpg':b'image','2.jpg':b'image'}))
        (self.root/'损坏.zip').write_bytes(b'not a zip')
        self.lib=Library(self.data,str(self.root))
        self.scan()

    def tearDown(self):
        self.tool_patch.stop()
        self.temp.cleanup()

    def scan(self,force=False):
        self.assertTrue(self.lib.start_scan(force))
        deadline=time.time()+10
        while self.lib.scan_state['running'] and time.time()<deadline:time.sleep(.01)
        self.assertFalse(self.lib.scan_state['running'])
        self.assertEqual(self.lib.scan_state['error'],'')

    def book(self,title):
        return next(item for item in self.lib.listing({'limit':100})['items'] if item['title']==title)


class LibraryTests(LibraryFixture,unittest.TestCase):
    def test_recursive_catalog_contains_books_and_no_placeholder_archives(self):
        self.assertEqual(self.lib.stats()['total'],8)
        self.assertEqual(self.lib.listing({'q':'小舟'})['items'][0]['title'],'夜航')
        book=self.book('夜航')
        self.assertEqual(len(json.loads(book['chain'])),3)
        self.assertEqual(self.book('普通故事')['kind'],'pdf')
        self.assertFalse(any(b['kind']=='archive' for b in self.lib.listing({'limit':100})['items']))
        self.assertEqual(len(self.lib.issues()),1)
        self.assertIn(self.lib.issues()[0]['code'],{'damaged','format','engine'})
        self.assertNotIn('folders',self.lib.stats())

    def test_no_reading_metadata_or_activities(self):
        book=self.book('故事')
        forbidden={'favorite','status','tags','notes','progress','last_read','last_entry'}
        self.assertFalse(forbidden.intersection(book))
        with self.lib.db() as db:
            fields={r['name'] for r in db.execute('PRAGMA table_info(books)')}
            self.assertFalse(fields.intersection(forbidden))
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='reading'").fetchone())
            before=dict(db.execute('SELECT * FROM books WHERE id=?',(book['id'],)).fetchone())
        with patch('server.os.startfile',create=True):self.lib.open_external(book['id'])
        with self.lib.db() as db:
            after=dict(db.execute('SELECT * FROM books WHERE id=?',(book['id'],)).fetchone())
        self.assertEqual(before,after)

    def test_reveal_extracts_exact_novel_and_separates_original_archive(self):
        book=self.book('夜航')
        original=(self.root/'合集.zip').read_bytes()
        with patch('server.reveal_in_explorer') as reveal:
            result=self.lib.reveal(book['id'])
            target=Path(reveal.call_args.args[0])
            self.assertTrue(result['extracted'])
            self.assertTrue(target.is_relative_to(self.data/'extracted'))
            self.assertEqual(target.name,'《夜航》作者：小舟.txt')
            self.assertEqual(target.read_text(encoding='utf-8'),'嵌套三层里的小说。')
            self.lib.reveal(book['id'],original=True)
            self.assertEqual(reveal.call_args.args[0],self.root/'合集.zip')
        self.assertEqual((self.root/'合集.zip').read_bytes(),original)

    def test_nested_cache_reused_and_source_change_refreshes(self):
        book=self.lib.book(self.book('夜航')['id'])
        target=self.lib.external_path(book)
        with patch.object(self.lib.engine,'extract',side_effect=AssertionError('unnecessary extraction')):
            self.assertEqual(self.lib.external_path(book),target)
            with patch.object(self.lib.engine,'catalog',side_effect=AssertionError('unchanged archive scanned')):
                self.scan()
        with zipfile.ZipFile(self.root/'合集.zip','a') as zf:zf.writestr('新加入.txt',b'new')
        self.scan()
        self.assertEqual(self.lib.stats()['total'],9)
        self.assertEqual(self.book('新加入')['kind'],'text')

    def test_archive_paths_do_not_escape_the_cache(self):
        with zipfile.ZipFile(self.root/'合集.zip','a') as zf:zf.writestr('../../escaped.txt',b'safe')
        self.scan()
        target=self.lib.external_path(self.lib.book(self.book('escaped')['id']))
        self.assertTrue(target.is_relative_to(self.data/'extracted'))
        self.assertFalse((self.root.parent/'escaped.txt').exists())

    def test_mixed_folder_and_offline_scan(self):
        (self.root/'未整理'/'cover.jpg').write_bytes(b'cover')
        self.scan()
        self.assertEqual(self.lib.stats()['total'],8)
        self.assertEqual(self.lib.listing({'q':'未整理'})['total'],0)
        offline=self.root.with_name('offline');self.root.rename(offline)
        self.lib.start_scan()
        while self.lib.scan_state['running']:time.sleep(.01)
        self.assertTrue(self.lib.scan_state['error'])
        self.assertEqual(self.lib.stats()['total'],8)
        offline.rename(self.root)

    def test_old_schema_is_compatible_but_history_is_never_used(self):
        book=self.book('故事')
        with self.lib.db() as db:
            db.execute("ALTER TABLE books ADD COLUMN tags TEXT DEFAULT ''")
            db.execute("ALTER TABLE books ADD COLUMN last_read REAL DEFAULT 0")
            db.execute('UPDATE books SET tags=?,last_read=? WHERE id=?',('旧标签',123,book['id']))
        self.lib=Library(self.data)
        self.scan()
        self.assertNotIn('tags',self.lib.book(book['id']))
        self.assertEqual(self.lib.listing({'q':'旧标签'})['total'],0)
        with patch('server.os.startfile',create=True):self.lib.open_external(book['id'])
        with self.lib.db() as db:self.assertEqual(db.execute('SELECT last_read FROM books WHERE id=?',(book['id'],)).fetchone()[0],123)

    def test_legacy_zip_utf8_filename_without_flag_is_repaired(self):
        raw='《旧编码》作者：小舟.txt'.encode('utf-8')
        placeholder='x'*len(raw)
        content=packed({placeholder:b'legacy'}).replace(placeholder.encode(),raw)
        (self.root/'旧编码.zip').write_bytes(content)
        self.scan()
        book=self.lib.book(self.book('旧编码')['id'])
        self.assertEqual(book['author'],'小舟')
        self.assertEqual(self.lib.external_path(book).read_bytes(),b'legacy')

    @unittest.skipUnless(TOOL,'7-Zip required for encrypted fixtures')
    def test_password_protected_7z_is_unpacked_after_password_setting(self):
        source=Path(self.temp.name)/'secret.txt';source.write_bytes(b'secret book')
        archive=self.root/'encrypted.7z'
        subprocess.run([TOOL,'a',str(archive),str(source),'-pnovel-password','-mhe=on','-y'],check=True,capture_output=True)
        self.scan()
        self.assertTrue(any(i['code']=='password' for i in self.lib.issues()))
        self.lib.settings({'passwords':['wrong','novel-password']})
        deadline=time.time()+10
        while self.lib.scan_state['running'] and time.time()<deadline:time.sleep(.01)
        self.assertEqual(self.lib.scan_state['error'],'')
        book=self.lib.book(self.book('secret')['id'])
        self.assertEqual(self.lib.external_path(book).read_bytes(),b'secret book')
        self.assertFalse(any(i['code']=='password' for i in self.lib.issues()))

    @unittest.skipUnless(os.name=='nt','Windows Shell API')
    def test_windows_reveal_uses_full_item_pidl_for_unicode_spaces_and_commas(self):
        target=self.root/'含 空格,逗号.txt';target.write_text('test',encoding='utf-8')
        shell,ole=Mock(),Mock()
        ole.CoInitialize.return_value=0
        def parse(name,context,pointer,attributes,out):pointer._obj.value=12345;return 0
        shell.SHParseDisplayName.side_effect=parse
        shell.SHOpenFolderAndSelectItems.return_value=0
        with patch('platform_utils.ctypes.OleDLL',side_effect=[shell,ole]):reveal_in_explorer(target)
        self.assertEqual(shell.SHParseDisplayName.call_args.args[0],str(target.resolve()))
        self.assertEqual(shell.SHOpenFolderAndSelectItems.call_args.args[1:],(0,None,0))
        ole.CoTaskMemFree.assert_called_once();ole.CoUninitialize.assert_called_once()

    @unittest.skipUnless(TOOL,'7-Zip required for AES ZIP fixtures')
    def test_aes_zip_member_is_automatically_unpacked(self):
        source=Path(self.temp.name)/'AES-novel.txt';source.write_bytes(b'AES book')
        archive=self.root/'aes.zip'
        subprocess.run([TOOL,'a',str(archive),str(source),'-pzip-secret','-mem=AES256','-y'],check=True,capture_output=True)
        self.lib=Library(self.data)
        with self.lib.db() as db:db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('archive_passwords',json.dumps(['zip-secret'])))
        self.lib.engine=ArchiveEngine(self.data,self.lib.passwords)
        self.scan()
        self.assertEqual(self.lib.external_path(self.lib.book(self.book('AES-novel')['id'])).read_bytes(),b'AES book')


class HTTPTests(LibraryFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.library=self.lib
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()

    def get(self,path,headers=None):return urllib.request.urlopen(urllib.request.Request(self.base+path,headers=headers or {}))

    def post(self,path,data,headers=None):return urllib.request.urlopen(urllib.request.Request(self.base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json',**(headers or {})}))

    def test_removed_metadata_routes_and_host_origin_guards(self):
        for route in ['/api/export','/api/content','/api/raw']:
            with self.assertRaises(urllib.error.HTTPError) as ctx:self.get(route)
            self.assertEqual(ctx.exception.code,404);ctx.exception.close()
        for route in ['/api/update','/api/import','/api/progress']:
            with self.assertRaises(urllib.error.HTTPError) as ctx:self.post(route,{})
            self.assertEqual(ctx.exception.code,404);ctx.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.post('/api/scan',{}, {'Origin':'https://evil.example'})
        self.assertEqual(ctx.exception.code,403);ctx.exception.close()
        # A rebinding page never counts as the local machine and has no login token.
        with self.assertRaises(urllib.error.HTTPError) as ctx:self.get('/api/stats',{'Host':'evil.example'})
        self.assertEqual(ctx.exception.code,401);ctx.exception.close()

    def test_http_reveal_and_failure_details(self):
        book=self.book('夜航')
        with patch('server.reveal_in_explorer') as reveal:
            with self.post('/api/reveal',{'id':book['id']}) as response:result=json.load(response)
            self.assertEqual(Path(result['path']).name,'《夜航》作者：小舟.txt')
            self.assertTrue(result['extracted']);reveal.assert_called_once()
        with self.get('/api/issues') as response:issues=json.load(response)['items']
        self.assertEqual(len(issues),1)
        self.assertIn('code',issues[0]);self.assertIn('message',issues[0])
        with self.get('/api/books') as response:
            self.assertFalse({'tags','status','last_read'}.intersection(json.load(response)['items'][0]))


if __name__=='__main__':unittest.main()
