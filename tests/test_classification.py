import json
import time
import unittest
from unittest.mock import Mock, patch

from ai_cleaner import ClaudeClient
from book_classification import count_words, infer, introduction, read_text, TAXONOMY
from claude_terminal import ClaudeTerminal
from tests.test_library import LibraryFixture, packed


class TaxonomyRulesTests(unittest.TestCase):
    def test_explicit_categories_pairings_and_multiple_tags(self):
        value=infer('合成书（高H BG 1v2 ABO 末日）.txt','')
        self.assertEqual((value['level'],value['relationship'],value['pairing']),('肉文','BG','np'))
        self.assertEqual(value['tags'],['末日','ABO']);self.assertEqual(value['status'],'classified')
        self.assertEqual(infer('BL 1v2.txt','')['relationship'],'BL')
        self.assertEqual(infer('BL 1v2.txt','')['pairing'],'')
        self.assertEqual(infer('GL R18 校园.txt','')['relationship'],'GL')
        self.assertEqual(infer('GB 女攻男受 现代言情.txt','')['relationship'],'GB')
        self.assertEqual(infer('大女主 全文清水 无CP 兽世.txt','')['relationship'],'大女主')
        self.assertEqual(infer('大女主 BG 1v1.txt','')['relationship'],'BG')
        self.assertEqual(infer('无肉文 BG 1v1.txt','')['level'],'清水')
        self.assertEqual(infer('【清水】BG 1v1 合成故事.txt','')['level'],'清水')
        self.assertEqual(infer('BG 1v2.txt','','简介：女主的故事。本文BG1v1，无CP党慎入。')['pairing'],'np')
        self.assertEqual(infer('故事.txt','','简介：女主的故事。本文BG1v1，无CP党慎入。')['relationship'],'BG')

    def test_absence_is_not_clean_and_directory_does_not_imply_adult_or_bg(self):
        value=infer('普通小说.txt','po18合集/故事.txt','第一章\n普通的合成故事。')
        self.assertEqual(value['level'],'');self.assertEqual(value['relationship'],'')
        self.assertEqual(infer('1v2 NP.txt','')['relationship'],'')
        self.assertEqual(infer('BL.txt','BG合集/BL.txt')['relationship'],'BL')
        self.assertEqual(infer('没有标记.txt','耽美合集/没有标记.txt')['relationship'],'BL')
        self.assertEqual(infer('故事 高H 全文清水.txt','')['status'],'conflict')

    def test_other_book_advertisements_and_story_dialogue_do_not_contaminate_intro(self):
        text='本书简介：一位女主经营餐馆。\n带带预收《别的书》\nBL ABO 星际 校园 末日\n内容标签：美食\n第一章 回家\n她说喜欢校园文和BL小说。'
        value=infer('餐馆.txt','',text)
        self.assertEqual(value['tags'],[]);self.assertEqual(value['relationship'],'')
        self.assertNotIn('BL ABO',introduction(text));self.assertIn('内容标签',introduction(text))
        self.assertEqual(infer('正文.txt','','文案：普通故事\n1  ☪ 开场\n女主无CP 校园')['relationship'],'')

    def test_character_count_and_html_docx_reading_are_explicit(self):
        self.assertEqual(count_words('中文，ABC 123！\n_'),8)
        self.assertEqual(read_text('<html><head><title>不计</title></head><body><p>计入</p><script>不计</script></body></html>'.encode(),'.html'),'\n计入')
        docx=packed({'word/document.xml':'<w:document xmlns:w="test"><w:body><w:p><w:r><w:t>正文</w:t></w:r></w:p></w:body></w:document>'.encode()})
        self.assertEqual(read_text(docx,'.docx'),'正文')
        with self.assertRaises(ValueError):read_text(b'%PDF fake','.pdf')


class ClassificationLibraryTests(LibraryFixture,unittest.TestCase):
    def wait(self):
        deadline=time.time()+20
        while self.lib.classifier.state['running'] and time.time()<deadline:time.sleep(.01)
        self.assertFalse(self.lib.classifier.state['running'])

    def tearDown(self):
        self.lib.classifier.stop.set();self.wait();super().tearDown()

    def test_initial_pass_does_not_call_claude_preserves_files_and_counts_nested_members(self):
        before=(self.root/'合集.zip').read_bytes();count=self.lib.stats()['total']
        with patch.object(ClaudeClient,'generate',side_effect=AssertionError('no API')),patch.object(ClaudeTerminal,'generate',side_effect=AssertionError('no subscription')):
            self.assertTrue(self.lib.classifier.start()['started']);self.wait()
        self.assertEqual(self.lib.classifier.state['done'],8)
        self.assertEqual(self.lib.classifier.metrics(self.book('夜航')['id'])['word_count'],8)
        self.assertEqual(self.lib.classifier.metrics(self.book('sample')['id'])['word_count'],None)
        self.assertEqual((self.root/'合集.zip').read_bytes(),before);self.assertEqual(self.lib.stats()['total'],count)
        self.lib.classifier.start();self.wait();self.assertEqual(self.lib.classifier.state['total'],0,'unchanged results reused')

    def test_manual_taxonomy_survives_rescan_and_offline_refresh_and_filters_match(self):
        uid=self.book('夜航')['id'];classifier=self.lib.classifier
        saved=classifier.save({'id':uid,'level':'肉文','relationship':'BG','pairing':'np','tags':['末日','ABO']})
        self.assertEqual(saved['label'],'肉文 BG np 末日 ABO');self.assertTrue(saved['reviewed'])
        classifier.start(force=True);self.wait();self.scan()
        self.assertEqual(classifier.classification(uid)['label'],saved['label'])
        self.assertEqual(classifier.metrics(uid)['word_count'],8)
        found=self.lib.listing({'level':'肉文','relationship':'BG','pairing':'np','tag':'ABO'})
        self.assertEqual([b['id'] for b in found['items']],[uid])
        self.assertEqual(self.lib.listing({'tag':'校园'})['total'],0)
        self.assertEqual(self.lib.listing({'classification_status':'pending'})['total'],7)
        with self.assertRaises(ValueError):classifier.save({'id':uid,'relationship':'BL','pairing':'np'})
        with self.assertRaises(ValueError):classifier.save({'id':uid,'tags':['不在约定中']})

    def test_cleaned_version_has_own_count_and_classification_is_shared(self):
        uid=self.lib.listing({'q':'小林'})['items'][0]['id'];book=self.lib.book(uid);original=self.lib.external_path(book).read_bytes()
        classifier=self.lib.classifier;classifier.save({'id':uid,'level':'清水','relationship':'BG','pairing':'1v1','tags':[]})
        classifier.measure(uid);self.assertEqual(classifier.metrics(uid)['word_count'],6)
        folder=self.data/'versions'/uid/('a'*32);folder.mkdir(parents=True)
        target=folder/'副本.txt';target.write_text('另存副本只有这里。',encoding='utf-8')
        version=self.lib.add_version(book,'a'*32,target,'',1,'synthetic-model')
        self.assertEqual(version['word_count'],8);self.assertEqual(classifier.metrics(uid)['word_count'],6)
        self.assertEqual(self.lib.book(uid)['classification']['level'],'清水')
        self.assertEqual(self.lib.external_path(book).read_bytes(),original)

    def test_ai_suggestion_requires_explicit_action_validates_and_does_not_save(self):
        uid=self.book('夜航')['id'];classifier=self.lib.classifier
        result={'level':'','relationship':'BL','pairing':'np','tags':['ABO'],'confidence':.7,'reason':'合成建议，仅供确认'}
        client=Mock();client.generate.return_value=(result,{'input_tokens':10})
        with patch.object(self.lib.cleaner,'client',return_value=client):suggestion=classifier.suggest({'id':uid})
        self.assertEqual(suggestion['pairing'],'');self.assertEqual(suggestion['relationship'],'BL')
        self.assertEqual(classifier.classification(uid)['status'],'pending','suggestions never silently apply')
        payload,system,schema=client.generate.call_args.args
        self.assertFalse(payload['is_full_text']);self.assertEqual(schema['properties']['tags']['items']['enum'],TAXONOMY['tags'])

    def test_metadata_only_refinement_keeps_full_scan_count_and_positive_rating(self):
        path=self.root/'合成校正 BG 1v2.txt';path.write_text('本书简介：合成测试\n第一章\n合成正文。',encoding='utf-8');self.scan()
        uid=self.book('合成校正 BG 1v2')['id'];book=self.lib.book(uid);classifier=self.lib.classifier
        classifier._group([book]);before=classifier.metrics(uid)
        with self.lib.db() as db:
            evidence=[{'field':'level','value':'肉文','source':'全文特征','reason':'合成的全文检出记录','confidence':.83}]
            db.execute("UPDATE book_classification SET level=?,relationship='BL',pairing='',evidence=? WHERE book_id=?",('肉文',json.dumps(evidence,ensure_ascii=False),uid))
        classifier.start(mode='metadata');self.wait();after=classifier.classification(uid)
        self.assertEqual((after['level'],after['relationship'],after['pairing']),('肉文','BG','np'))
        self.assertEqual(classifier.metrics(uid)['word_count'],before['word_count'])

    def test_source_change_invalidates_count_in_details_and_shelf(self):
        uid=self.book('夜航')['id'];classifier=self.lib.classifier;classifier.measure(uid)
        with self.lib.db() as db:db.execute('UPDATE books SET mtime=mtime+1 WHERE id=?',(uid,))
        self.assertIsNone(classifier.metrics(uid)['word_count'])
        self.assertIsNone(self.lib.listing({'q':'小舟'})['items'][0]['word_count'])


if __name__=='__main__':unittest.main()
