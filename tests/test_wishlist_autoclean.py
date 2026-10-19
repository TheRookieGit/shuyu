import os
import unittest
from unittest.mock import patch

from ai_cleaner import ClaudeClient
from tests.test_ai_cleaner import TEXT, suggestions
from tests.test_library import LibraryFixture


class WishlistTests(LibraryFixture,unittest.TestCase):
    def test_wishes_match_downloaded_novels_zips_and_folders(self):
        wishes=self.lib.wishlist
        novel=wishes.save({'title':'新书','author':'某某','kind':'novel','url':'https://example.com/new','note':'朋友推荐'})
        bundle=wishes.save({'title':'合集','kind':'zip'})
        folder=wishes.save({'title':'漫画','kind':'folder'})
        other=wishes.save({'title':'新书','author':'别人','kind':'novel'})
        self.assertFalse(novel['downloaded'])
        self.assertTrue(wishes.item(bundle['id'])['downloaded'],'books inside 合集.zip')
        self.assertTrue(wishes.item(folder['id'])['downloaded'],'image folder 漫画')
        self.assertEqual(wishes.items()['pending'],2)
        self.assertEqual(len(wishes.items('朋友')['items']),1)
        (self.root/'《新书》作者：某某.txt').write_text('新下载的小说。',encoding='utf-8')
        self.scan()
        book=self.book('新书')
        self.assertEqual(wishes.item(novel['id'])['book_id'],book['id'])
        self.assertFalse(wishes.item(other['id'])['downloaded'],'a different author is not the same book')
        self.assertEqual(self.lib.book(book['id'])['download_url'],'https://example.com/new')

    def test_download_address_validation_and_shared_by_copies(self):
        uid=self.book('夜航')['id']
        self.lib.wishlist.set_link(uid,'https://example.com/a')
        self.assertEqual(self.lib.book(uid)['download_url'],'https://example.com/a')
        for bad in ['javascript:alert(1)','ftp://example.com','https://exa mple.com']:
            with self.assertRaises(ValueError):self.lib.wishlist.set_link(uid,bad)
        with self.assertRaises(ValueError):self.lib.wishlist.save({'title':'x','kind':'exe'})
        with self.assertRaises(ValueError):self.lib.wishlist.save({'title':' '})
        self.lib.wishlist.set_link(uid,'')
        self.assertEqual(self.lib.book(uid)['download_url'],'')


class AutoCleanTests(LibraryFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.env=patch.dict(os.environ,{'ANTHROPIC_API_KEY':''});self.env.start()
        self.provider=patch.object(ClaudeClient,'analyze',side_effect=suggestions);self.provider.start()
        self.auto=self.lib.auto
        self.thread=patch.object(self.auto,'start');self.thread.start()

    def tearDown(self):
        self.thread.stop();self.provider.stop();self.env.stop();super().tearDown()

    def run_queue(self):
        while self.auto._step():pass

    def test_only_new_novels_are_cleaned_into_a_default_selection_version(self):
        self.assertEqual(self.auto.enqueue(),0,'first run only records the baseline')
        self.assertEqual(self.auto.enqueue(),0,'existing books are never queued')
        source=self.root/'新到小说.txt';source.write_bytes(TEXT.encode('gb18030'))
        (self.root/'没有标记.txt').write_text('第一章\n平静的故事。',encoding='utf-8')
        self.scan();self.assertEqual(self.auto.enqueue(),2)
        self.run_queue()
        self.assertIn('Claude AI 设置',self.auto.status()['blocked'],'waits for Claude settings instead of failing')
        self.lib.cleaner.configure({'api_key':'synthetic-test-key'})
        self.run_queue()
        book=self.lib.book(self.book('新到小说')['id'])
        record=book['auto_clean'];self.assertEqual(record['status'],'saved')
        self.assertIn('另有 1 项',record['message'])
        self.assertEqual(len(book['versions']),1);version=book['versions'][0]
        self.assertEqual((version['label'],version['edit_count']),('自动清理版',1))
        cleaned=self.lib.version_path(book,version['id']).read_text(encoding='utf-8')
        self.assertNotIn('作者有话说',cleaned);self.assertIn('图□馆',cleaned,'guesses are not applied without review')
        self.assertEqual(source.read_bytes(),TEXT.encode('gb18030'),'original untouched')
        self.assertEqual(self.lib.book(self.book('没有标记')['id'])['auto_clean']['status'],'clean')
        self.scan();self.assertEqual(self.auto.enqueue(),0,'each new book is processed once')
        self.assertEqual(self.auto.status()['counts'],{'saved':1,'clean':1})

    def test_disabled_and_failed_books(self):
        self.auto.enqueue();self.auto.configure({'enabled':False})
        (self.root/'关闭时下载.txt').write_bytes(TEXT.encode('utf-8'));self.scan()
        self.assertEqual(self.auto.enqueue(),0)
        self.auto.configure({'enabled':True})
        self.scan();self.assertEqual(self.auto.enqueue(),0,'books added while off are not cleaned in a batch')
        self.lib.cleaner.configure({'api_key':'synthetic-test-key'})
        (self.root/'坏编码.txt').write_bytes(b'\xff\xfe\x00');self.scan();self.auto.enqueue();self.run_queue()
        self.assertEqual(self.auto.status()['counts'],{'failed':1})
        self.assertEqual(self.auto.retry()['counts'],{'queued':1})


if __name__=='__main__':unittest.main()
