import json
import os
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request

from ai_cleaner import ClaudeClient, CleanError, Document
from auto_clean import AutoCleaner
from lite_upload import UPLOAD_FOLDER
from server import Handler, ThreadingHTTPServer
from tests.test_library import LibraryFixture, packed


TEXT='第一章 回家\n她走进图□馆。\n作者有话说：谢谢支持！\n第二章 出发\n'


def suggestions(segment,*args):
    edits=[dict(before=b,after=a,category=c,confidence=.98,reason='合成测试建议') for b,a,c in
           [('作者有话说：谢谢支持！','','author_note'),('图□馆','图书馆','censored')] if b in segment]
    return {'edits':edits},{}


class LiteUploadTests(LibraryFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.env=patch.dict(os.environ,{'ANTHROPIC_API_KEY':''});self.env.start()
        self.scan();self.lib.cleaner.configure({'api_key':'synthetic-test-key'})

    def tearDown(self):
        deadline=time.time()+5
        while self.lib.cleaner.active and time.time()<deadline:time.sleep(.01)
        self.env.stop();super().tearDown()

    def clean(self,book_id,version_id=''):
        job=self.lib.cleaner.prepare({'id':book_id,'version_id':version_id})
        with patch.object(ClaudeClient,'analyze',side_effect=suggestions):
            self.lib.cleaner.start(job['id']);deadline=time.time()+5
            while self.lib.cleaner.active and time.time()<deadline:time.sleep(.01)
        job=self.lib.cleaner.public(job['id'])
        return self.lib.cleaner.save({'job_id':job['id'],'selected':[p['id'] for p in job['patches']]})

    def test_upload_is_named_indexed_at_once_and_keeps_every_version(self):
        raw=TEXT.encode('gb18030')
        book=self.lib.uploads.save(raw,'whatever.TXT',' 春夜/喜雨 ','杜甫')
        path=self.root/UPLOAD_FOLDER/'《春夜喜雨》作者：杜甫【lite上传】.txt'
        self.assertEqual(path.read_bytes(),raw);self.assertEqual((book['title'],book['author']),('春夜喜雨','杜甫'))
        self.assertIn(book['id'],[b['id'] for b in self.lib.listing({'q':'春夜喜雨'})['items']],'listed without waiting for a scan')
        self.assertEqual(self.lib.classifier.metrics(book['id'])['word_count'] is not None,True)
        again=self.lib.uploads.save(raw,'x.txt','春夜喜雨','杜甫')
        self.assertTrue((self.root/UPLOAD_FOLDER/'《春夜喜雨》作者：杜甫【lite上传】（2）.txt').is_file());self.assertNotEqual(again['id'],book['id'])
        untitled=self.lib.uploads.save('正文'.encode(),'x.md','无名','')
        self.assertEqual(untitled['name'],'《无名》【lite上传】.md');self.assertEqual(untitled['author'],'')
        first=self.clean(book['id']);second=self.clean(book['id'],first['id'])
        self.scan()
        self.assertEqual(self.lib.book(book['id'])['rel'],UPLOAD_FOLDER+'/'+path.name,'a later scan keeps the same book')
        self.assertEqual([v['id'] for v in self.lib.versions(book['id'])],[second['id'],first['id']])
        self.assertEqual(path.read_bytes(),raw,'original upload never changed')
        downloaded=[self.lib.download(book['id'],v)[0] for v in ('',first['id'],second['id'])]
        self.assertEqual(downloaded[0],path)
        self.assertNotIn('作者有话说',Document(downloaded[1]).text);self.assertIn('图书馆',Document(downloaded[1]).text)

    def test_title_and_author_are_read_from_the_file_unless_edited(self):
        heading='书名：《长河落日》\n作者：大漠\n\n第一章\n正文。\n'.encode('gb18030')
        book=self.lib.uploads.save(heading,'新建文本文档.txt')
        self.assertEqual((book['title'],book['author']),('长河落日','大漠'))
        self.assertEqual(book['name'],'《长河落日》作者：大漠【lite上传】.txt')
        named=self.lib.uploads.save(heading,'【精校】《孤烟》作者：边塞.txt')
        self.assertEqual((named['title'],named['author']),('孤烟','边塞'),'a file name that names the book wins')
        edited=self.lib.uploads.save(heading,'新建文本文档.txt','长河',author='')
        self.assertEqual(edited['name'],'《长河》【lite上传】.txt','an edited field is kept as typed, even empty')
        plain=self.lib.uploads.save('第一章\n正文。'.encode(),'001 春水.txt')
        self.assertEqual((plain['title'],plain['author']),('001 春水',''))
        container=b'<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>'
        opf='<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>大江东去</dc:title><dc:creator>东坡</dc:creator></metadata><manifest><item id="c" href="c.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c"/></spine></package>'.encode()
        chapter='<html xmlns="http://www.w3.org/1999/xhtml"><body><p>正文。</p></body></html>'.encode()
        epub=self.lib.uploads.save(packed({'mimetype':b'application/epub+zip','META-INF/container.xml':container,'OEBPS/content.opf':opf,'OEBPS/c.xhtml':chapter}),'book.epub')
        self.assertEqual(epub['name'],'《大江东去》作者：东坡【lite上传】.epub')

    def test_automatic_cleanup_leaves_uploads_to_lite(self):
        self.lib.auto._store({'enabled':True,'since':0})
        self.lib.uploads.save(TEXT.encode(),'a.txt','上传的书','')
        (self.root/'新书.txt').write_text(TEXT,encoding='utf-8');self.scan()
        with patch.object(AutoCleaner,'start'):self.lib.auto.enqueue()
        with self.lib.db() as db:queued={r[0] for r in db.execute('SELECT b.title FROM auto_clean a JOIN books b ON b.id=a.book_id')}
        self.assertIn('新书',queued);self.assertNotIn('上传的书',queued)

    def test_bad_uploads_write_nothing(self):
        for raw,name,title in [(b'x','a.pdf','书'),(b'','a.txt','书'),(TEXT.encode(),'a.txt',' 《》 '),(b'not a zip','a.epub','书')]:
            with self.assertRaises(CleanError):self.lib.uploads.save(raw,name,title)
        self.assertEqual(list((self.root/UPLOAD_FOLDER).glob('*')) if (self.root/UPLOAD_FOLDER).exists() else [],[])
        self.assertEqual(list((self.data/'uploads').glob('*')),[],'temporary copies removed')

    def test_http_upload_route(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.library=self.lib
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def upload(raw,**query):
            request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/lite/upload?'+urllib.parse.urlencode(query),data=raw,headers={'Content-Type':'application/octet-stream'})
            with urllib.request.urlopen(request) as response:return json.load(response)
        try:
            book=upload(TEXT.encode(),name='《夜雨》作者：某人.txt',title='夜雨',author='某人')
            self.assertEqual(book['name'],'《夜雨》作者：某人【lite上传】.txt')
            with self.assertRaises(urllib.error.HTTPError) as caught:upload(b'x',name='a.exe',title='书')
            detected=upload('书名：秋声\n作者：欧阳\n正文。'.encode(),name='a.txt',author='')
            self.assertEqual(detected['name'],'《秋声》【lite上传】.txt','missing title detected, blank author kept blank')
            with caught.exception as error:self.assertEqual(error.code,400);self.assertIn('TXT',json.load(error)['error'])
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
