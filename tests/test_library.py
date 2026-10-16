import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile

from server import Library, Handler, ThreadingHTTPServer, UserError, decode_text


class LibraryFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'books'
        self.root.mkdir()
        self.data = Path(self.temp.name) / 'data'
        (self.root / '第一组').mkdir()
        self.text = '第一章 开始\n' + ('春日的故事。\n' * 5000) + '\n第二章 继续\n最后的故事。'
        (self.root / '第一组' / '《春日》作者：小林.txt').write_bytes(self.text.encode('gb18030'))
        (self.root / '第二本.txt').write_text('序章\n你好世界',encoding='utf-8-sig')
        (self.root / 'draft.txt.downloading').write_bytes(b'incomplete')
        (self.root / '.DS_Store').write_bytes(b'junk')
        (self.root / 'note.txt.bak').write_bytes(b'backup')
        (self.root / '画集').mkdir()
        for name in ['10.jpg','2.jpg','1.jpg']:
            (self.root / '画集' / name).write_bytes(b'image')
        with zipfile.ZipFile(self.root / '合集.zip','w') as zf:
            zf.writestr('书/故事.txt','第一章 开端\n压缩包里的文字。'.encode('utf-8'))
            zf.writestr('图/1.jpg',b'image')
            zf.writestr('__MACOSX/._junk',b'junk')
        self.make_epub(self.root / '春日.epub')
        with zipfile.ZipFile(self.root / '笔记.docx','w') as zf:
            zf.writestr('word/document.xml','<w:document xmlns:w="word"><w:body><w:p><w:r><w:t>第一段</w:t></w:r></w:p><w:p><w:r><w:t>第二段</w:t></w:r></w:p></w:body></w:document>')
        (self.root/'sample.pdf').write_bytes(b'%PDF-1.4\nfixture')
        self.lib = Library(self.data,str(self.root))
        self.scan()

    def tearDown(self):
        self.temp.cleanup()

    def scan(self):
        self.assertTrue(self.lib.start_scan())
        deadline=time.time()+5
        while self.lib.scan_state['running'] and time.time()<deadline:
            time.sleep(.01)
        self.assertFalse(self.lib.scan_state['running'])
        self.assertEqual(self.lib.scan_state['error'],'')

    def book(self, ext):
        return next(b for b in self.lib.listing({'limit':100})['items'] if b['ext']==ext)

    @staticmethod
    def make_epub(path):
        with zipfile.ZipFile(path,'w') as zf:
            zf.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
            zf.writestr('OEBPS/content.opf','<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="b" href="b.xhtml" media-type="application/xhtml+xml"/><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
            zf.writestr('OEBPS/a.xhtml','<html xmlns="http://www.w3.org/1999/xhtml"><head><title>不应进入正文</title><style>hidden</style></head><body><h1>第一章</h1><p>第一部分。</p></body></html>')
            zf.writestr('OEBPS/b.xhtml','<html><body><h1>第二章</h1><p>第二部分。</p><script>evil()</script></body></html>')

class LibraryTests(LibraryFixture, unittest.TestCase):
    def test_scan_formats_and_ignore_unfinished(self):
        stats=self.lib.stats()
        self.assertEqual(stats['total'],7)
        self.assertEqual(stats['kinds']['comic'],1)
        album=self.book('folder')
        self.assertEqual(album['image_count'],3)
        self.assertEqual([e['name'] for e in self.lib.album_entries(self.lib.path(self.lib.book(album['id'])))],['1.jpg','2.jpg','10.jpg'])

    def test_search_tags_folder_and_literal_wildcards(self):
        result=self.lib.listing({'q':'春日 小林'})
        self.assertEqual(result['total'],1)
        book=result['items'][0]
        self.assertEqual(book['title'],'春日')
        self.assertEqual(book['author'],'小林')
        self.lib.update(book['id'],{'tags':'假期, 喜欢','favorite':True})
        self.assertEqual(self.lib.listing({'q':'假期','view':'favorites'})['total'],1)
        self.assertEqual(self.lib.listing({'folder':'第一组'})['total'],1)
        self.assertEqual(self.lib.listing({'q':'%'})['total'],0)

    def test_decode_chapters_paging_and_reopen(self):
        book=self.lib.listing({'q':'小林'})['items'][0]
        first=self.lib.content(book['id'],start=0)
        second=self.lib.content(book['id'],start=first['end'])
        self.assertEqual(first['text']+second['text'],self.text[:second['end']])
        self.assertEqual(len(first['chapters']),2)
        self.lib.progress(book['id'],'',second['start'],.3)
        resumed=self.lib.content(book['id'],start=-1)
        self.assertEqual(resumed['start'],second['start'])
        self.assertEqual(self.lib.book(book['id'])['status'],'reading')
        self.assertEqual(decode_text('中文'.encode('utf-16')),'中文')

    def test_archive_and_epub_and_docx(self):
        book=self.lib.book(self.book('.zip')['id'])
        entries=self.lib.archive_entries(self.lib.path(book))
        self.assertEqual(len(entries),2)
        text_entry=next(e for e in entries if e['ext']=='.txt')
        self.assertIn('压缩包里的文字',self.lib.content(book['id'],text_entry['key'])['text'])
        result=self.lib.content(self.book('.epub')['id'])
        self.assertLess(result['text'].index('第一部分'),result['text'].index('第二部分'))
        self.assertEqual(len(result['chapters']),2)
        self.assertNotIn('hidden',result['text'])
        self.assertNotIn('evil',result['text'])
        self.assertNotIn('不应进入正文',result['text'])
        self.assertIn('第一段\n第二段',self.lib.content(self.book('.docx')['id'])['text'])

    def test_scanning_preserves_data_and_recovers_missing(self):
        book=self.lib.listing({'q':'小林'})['items'][0]
        self.lib.update(book['id'],{'favorite':True,'tags':'保留','notes':'一些想法'})
        self.lib.progress(book['id'],'',100,.2)
        self.scan()
        updated=self.lib.book(book['id'])
        self.assertEqual((updated['favorite'],updated['tags'],updated['progress']),(1,'保留',.2))
        path=self.lib.path(updated)
        path.rename(path.with_suffix('.hidden'))
        self.scan()
        self.assertEqual(self.lib.listing({'q':'小林'})['total'],0)
        path.with_suffix('.hidden').rename(path)
        self.scan()
        self.assertEqual(self.lib.book(book['id'])['favorite'],1)
        # Offline scans leave the last known library intact.
        offline=self.root.with_name('offline')
        self.root.rename(offline)
        self.lib.start_scan()
        while self.lib.scan_state['running']:time.sleep(.01)
        self.assertTrue(self.lib.scan_state['error'])
        self.assertEqual(self.lib.stats()['total'],7)
        offline.rename(self.root)

    def test_progress_separate_archive_entries(self):
        book=self.book('.zip')
        self.lib.progress(book['id'],'one.txt',120,.1)
        self.lib.progress(book['id'],'two.txt',500,.4)
        with self.lib.db() as db:
            rows=db.execute('SELECT entry,position FROM reading WHERE book_id=? ORDER BY entry',(book['id'],)).fetchall()
        self.assertEqual([tuple(r) for r in rows],[('one.txt',120),('two.txt',500)])

    def test_archive_limits_and_no_traversal(self):
        book=self.lib.book(self.book('.zip')['id'])
        with self.assertRaises(UserError): self.lib.archive_bytes(self.lib.path(book),'../../outside.txt')
        album=self.lib.book(self.book('folder')['id'])
        with self.assertRaises(UserError): self.lib.raw(album,'../../outside.jpg')


class HTTPTests(LibraryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.library=self.lib
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        super().tearDown()

    def get(self,path,headers=None):
        return urllib.request.urlopen(urllib.request.Request(self.base+path,headers=headers or {}))

    def post(self,path,data,headers=None):
        return urllib.request.urlopen(urllib.request.Request(self.base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json',**(headers or {})}))

    def test_http_listing_static_ranges_and_origin(self):
        with self.get('/') as response:
            self.assertIn('书屿',response.read().decode())
            self.assertIn("script-src 'self'",response.headers['Content-Security-Policy'])
        with self.get('/api/books?q=%E6%98%A5%E6%97%A5') as response:
            self.assertEqual(json.load(response)['total'],2)
        book=self.book('.pdf')
        with self.get('/api/raw?id='+book['id'],{'Range':'bytes=0-3'}) as response:
            self.assertEqual(response.status,206)
            self.assertEqual(response.read(),b'%PDF')
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post('/api/update',{'id':book['id'],'favorite':True},{'Origin':'https://evil.example'})
        self.assertEqual(ctx.exception.code,403)
        ctx.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get('/api/stats',{'Host':'evil.example'})
        self.assertEqual(ctx.exception.code,403)
        ctx.exception.close()

    def test_http_backup_restore(self):
        book=self.book('.epub')
        with self.post('/api/update',{'id':book['id'],'favorite':True,'tags':'周末','notes':'想读'}):pass
        with self.post('/api/progress',{'id':book['id'],'position':5,'progress':.25}):pass
        with self.get('/api/export') as response:backup=json.load(response)
        with self.post('/api/update',{'id':book['id'],'favorite':False,'tags':'修改'}):pass
        with self.post('/api/import',{'backup':backup}) as response:self.assertEqual(json.load(response)['restored'],1)
        self.assertEqual(self.lib.book(book['id'])['tags'],'周末')
        self.assertEqual(self.lib.book(book['id'])['favorite'],1)


if __name__=='__main__':
    unittest.main()
