from datetime import date
import hashlib
import json
from pathlib import Path
import unittest

from catalog_organizer import infer_publication, publication_status, six_months_ago
from tests.test_library import LibraryFixture


class PublicationTests(unittest.TestCase):
    def test_calendar_six_month_boundary_and_completed_books(self):
        today=date(2026,8,31)
        self.assertEqual(six_months_ago(today),date(2026,2,28))
        self.assertEqual(publication_status('连载','2026-02-28',today),'断更')
        self.assertEqual(publication_status('连载','2026-03-01',today),'连载')
        self.assertEqual(publication_status('连载','',today),'连载')
        self.assertEqual(publication_status('完结','2000-01-01',today),'完结')
        self.assertEqual(publication_status('断更','2026-08-01',today),'连载')

    def test_local_evidence_not_file_age_or_another_book_recommendation(self):
        self.assertEqual(infer_publication('故事【完结】.txt')['state'],'完结')
        value=infer_publication('故事（未完结）.txt','状态：连载\n更新时间：2020年1月2日\n第一章 开始')
        self.assertEqual(value['state'],'连载');self.assertEqual(value['last_update'],'2020-01-02')
        self.assertEqual(infer_publication('故事.txt','预收推荐《完结作品》\n状态：完结\n第一章 开始')['state'],'')
        self.assertEqual(infer_publication('故事.txt','故事结束。\n全文完\n')['state'],'完结')
        self.assertEqual(infer_publication('故事.txt','去年没更新过。')['state'],'')


class OrganizerTests(LibraryFixture,unittest.TestCase):
    def metric(self,uid):
        book=self.lib.book(uid);raw=self.lib.external_path(book).read_bytes()
        with self.lib.db() as db:db.execute('INSERT OR REPLACE INTO book_text_metrics VALUES (?,?,?,?,?,?,?)',(uid,'',len(raw),self.lib.classifier.signature(book),hashlib.sha256(raw).hexdigest(),0,''))

    def test_same_content_and_same_title_author_merge_without_deleting_sources(self):
        folder=self.root/'another';folder.mkdir()
        path=folder/'《春日》作者：小林【完结】.txt';path.write_text('这是更完整的小说文本。全文完',encoding='utf-8')
        # Same author normalized from file name excludes the bracket metadata.
        copy=folder/'另一名字.txt';copy.write_text('包内小说。',encoding='utf-8')
        self.scan()
        ids=[r['id'] for r in self.lib.listing({'limit':100})['items']]
        for uid in ids:
            if self.lib.book(uid)['ext']=='.txt':self.metric(uid)
        result=self.lib.organizer.refresh()
        self.assertEqual(result['merged'],2)
        canonical=self.lib.listing({'q':'小林'})['items'][0]
        detail=self.lib.book(canonical['id'])
        self.assertEqual(len(detail['copies']),2)
        self.assertTrue(path.exists());self.assertTrue(copy.exists())
        self.assertEqual(detail['publication']['status'],'完结')
        self.assertEqual(self.lib.stats()['sources'],10);self.assertEqual(self.lib.stats()['total'],8)
        self.assertEqual(self.lib.listing({'publication':'完结'})['total'],1)
        self.assertEqual(self.lib.listing({'q':'另一名字'})['total'],1,'search aliases of retained duplicate sources')
        self.assertEqual(self.lib.organizer.facets()['publication']['完结'],1)
        self.lib.classifier.save({'id':canonical['id'],'level':'清水','relationship':'BG','pairing':'1v1','tags':['校园']})
        for source in detail['copies']:self.assertEqual(self.lib.classifier.classification(source['id'])['level'],'清水')

    def test_homonyms_unknown_authors_and_changed_files_do_not_merge_by_title(self):
        (self.root/'《春日》作者：另一位.txt').write_text('不同的故事',encoding='utf-8')
        (self.root/'春日.txt').write_text('作者不明的故事',encoding='utf-8')
        self.scan()
        self.assertEqual(self.lib.listing({'q':'春日'})['total'],4)
        uid=self.book('故事')['id'];self.metric(uid)
        path=self.root/'copy.txt';path.write_bytes('包内小说。'.encode('utf-8'));self.scan()
        copy_id=self.book('copy')['id'];self.metric(copy_id)
        self.assertEqual(self.lib.organizer.refresh()['merged'],1)
        path.write_text('内容已变化，需要重新统计',encoding='utf-8');self.scan()
        self.assertEqual(self.lib.organizer.refresh()['merged'],0)

    def test_manual_publication_and_multi_select_filters(self):
        first=self.book('故事')['id'];second=self.book('夜航')['id']
        self.lib.classifier.save({'id':first,'level':'肉文','relationship':'BL','tags':['ABO']})
        self.lib.classifier.save({'id':second,'level':'清水','relationship':'GL','tags':['末日']})
        self.lib.organizer.save_publication({'id':first,'state':'连载','last_update':'2000-01-01'})
        self.lib.organizer.save_publication({'id':second,'state':'完结'})
        self.assertEqual(self.lib.listing({'level':'肉文,清水','relationship':'BL,GL','tag':'ABO,末日','publication':'断更,完结'})['total'],2)
        self.assertEqual(self.lib.listing({'publication':'连载'})['total'],0)
        self.scan();self.assertEqual(self.lib.organizer.publication(first)['status'],'断更')
        with self.assertRaises(ValueError):self.lib.organizer.save_publication({'id':first,'state':'断更'})
        with self.assertRaises(ValueError):self.lib.organizer.save_publication({'id':first,'state':'连载','last_update':'2999-01-01'})
        self.assertEqual(self.lib.organizer.facets()['publication'],{'完结':1,'断更':1})

    def test_source_changes_invalidate_automatic_status_but_preserve_manual_review(self):
        path=self.root/'未整理'/'《春日》作者：小林.txt'
        path.write_text('状态：完结\n第一章 故事',encoding='utf-8');self.scan()
        book=self.lib.book(self.book('春日')['id'])
        with self.lib.db() as db:self.lib.organizer.record_publication(db,book,path.read_text(encoding='utf-8'))
        self.assertEqual(self.lib.organizer.publication(book['id'])['status'],'完结')
        path.write_text('新的文件，没有可靠状态',encoding='utf-8');self.scan()
        self.assertEqual(self.lib.organizer.publication(book['id'])['status'],'')
        self.lib.organizer.save_publication({'id':book['id'],'state':'完结'})
        path.write_text('内容再次改变但人工确认仍保留',encoding='utf-8');self.scan()
        self.assertEqual(self.lib.organizer.publication(book['id'])['status'],'完结')


if __name__=='__main__':unittest.main()
