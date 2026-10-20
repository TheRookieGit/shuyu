from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request
from xml.etree import ElementTree as ET
import zipfile

from ai_cleaner import CUSTOM, SYSTEM, Cleaner, CleanError, ClaudeClient, Document, MODEL, decode_text, plan_chunks, protect_secret, system_prompt, validate_edits
from auto_clean import OPTIONS as AUTO_OPTIONS
from server import Handler, Library, UserError
from tests.test_library import LibraryFixture, packed


TEXT='第一章 回家\r\n她走进图□馆。\r\n作者有话说：谢谢支持！\r\n第二章 出发\r\n故事继续。\r\n'


def edit(before,after='',category='author_note',confidence=.98):
    return dict(before=before,after=after,category=category,confidence=confidence,reason='合成测试建议')


def suggestions(segment,*args):
    edits=[]
    if '作者有话说：谢谢支持！' in segment:edits.append(edit('作者有话说：谢谢支持！'))
    if '图□馆' in segment:edits.append(edit('图□馆','图书馆','censored'))
    return {'edits':edits},{'input_tokens':123,'output_tokens':45}


class CleanerTests(LibraryFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.env=patch.dict(os.environ,{'ANTHROPIC_API_KEY':''});self.env.start()
        self.source=self.root/'合成测试.txt';self.source.write_bytes(TEXT.encode('gb18030'))
        self.scan();self.uid=self.book('合成测试')['id'];self.ai=self.lib.cleaner
        self.ai.configure({'api_key':'synthetic-test-key'})

    def tearDown(self):
        deadline=time.time()+5
        while self.ai.active and time.time()<deadline:time.sleep(.01)
        self.env.stop();super().tearDown()

    def complete(self,job,analyze=suggestions):
        with patch.object(ClaudeClient,'analyze',side_effect=analyze) as mocked:
            self.ai.start(job['id']);deadline=time.time()+5
            while self.ai.active and time.time()<deadline:time.sleep(.01)
            self.assertIsNone(self.ai.active)
        result=self.ai.public(job['id']);self.assertEqual(result['status'],'ready',result['error'])
        return result,mocked

    def test_encrypted_key_and_model_are_not_exposed_as_plaintext(self):
        config=self.ai.config();self.assertEqual(config['model'],MODEL);self.assertTrue(config['key_set'])
        self.assertNotIn('key',config);self.assertNotIn('secret',config)
        with self.lib.db() as db:raw=db.execute("SELECT value FROM settings WHERE key='claude_config'").fetchone()[0]
        self.assertNotIn('synthetic-test-key',raw)
        self.assertEqual(self.ai.key(),'synthetic-test-key')
        self.assertEqual(protect_secret(protect_secret('中文 secret'),True),'中文 secret')
        with self.assertRaises(CleanError):self.ai.configure({'base_url':'http://example.org/v1'})
        self.ai.configure({'clear_key':True});self.assertFalse(self.ai.config()['key_set'])

    def test_review_save_versions_grouping_parent_and_exact_location(self):
        original=self.source.read_bytes();count=self.lib.stats()['total']
        with patch.object(ClaudeClient,'analyze') as remote:
            job=self.ai.prepare({'id':self.uid});remote.assert_not_called()
        self.assertEqual(job['status'],'prepared');self.assertEqual(self.lib.versions(self.uid),[])
        job,remote=self.complete(job);self.assertEqual(job['patch_total'],2)
        author=next(p for p in job['patches'] if p['category']=='author_note')
        mask=next(p for p in job['patches'] if p['category']=='censored')
        self.assertEqual(job['defaults'],[author['id']]);self.assertFalse(mask['selected'])
        self.assertEqual(self.source.read_bytes(),original)
        first=self.ai.save({'job_id':job['id'],'selected':[author['id']],'label':'只去作者话'})
        self.assertEqual(first,self.ai.save({'job_id':job['id'],'selected':[author['id']]}))
        second=self.ai.save({'job_id':job['id'],'selected':[author['id'],mask['id']]})
        book=self.lib.book(self.uid);p1=self.lib.version_path(book,first['id']);p2=self.lib.version_path(book,second['id'])
        self.assertNotEqual(p1,p2);self.assertIn('图□馆',p1.read_text(encoding='utf-8'))
        self.assertIn('图书馆',p2.read_text(encoding='utf-8'));self.assertNotIn('作者有话说',p1.read_text(encoding='utf-8'))
        self.assertIn(b'\r\n',p1.read_bytes());self.assertEqual(self.source.read_bytes(),original)
        previous=p1.read_bytes();nested=self.ai.prepare({'id':self.uid,'version_id':first['id']})
        nested,_=self.complete(nested);third=self.ai.save({'job_id':nested['id'],'selected':[p['id'] for p in nested['patches']]})
        self.assertEqual(third['parent_id'],first['id']);self.assertEqual(p1.read_bytes(),previous)
        with patch('server.reveal_in_explorer') as reveal:
            self.lib.reveal(self.uid,version_id=second['id']);self.assertEqual(reveal.call_args.args[0],p2)
        with self.assertRaises(UserError):self.lib.version_path(self.lib.book(self.book('夜航')['id']),first['id'])
        self.scan();self.assertEqual(self.lib.stats()['total'],count)
        self.assertEqual(self.lib.book(self.uid)['version_count'],4)
        self.assertEqual(self.book('合成测试')['version_count'],4)

    def test_stale_source_and_invalid_selection_cannot_save(self):
        job,_=self.complete(self.ai.prepare({'id':self.uid}))
        with self.assertRaises(CleanError):self.ai.save({'job_id':job['id'],'selected':['forged']})
        self.source.write_text('源文件已被其他程序修改',encoding='utf-8')
        with self.assertRaisesRegex(CleanError,'输入版本已变化'):self.ai.save({'job_id':job['id'],'selected':[]})
        self.assertEqual(self.lib.versions(self.uid),[])

    def test_nested_archive_is_unchanged_and_versions_are_excluded_even_inside_library(self):
        original=(self.root/'合集.zip').read_bytes();uid=self.book('夜航')['id']
        job,_=self.complete(self.ai.prepare({'id':uid,'mode':'full'}),lambda *args:({'edits':[]},{}))
        v=self.ai.save({'job_id':job['id'],'selected':[]})
        self.assertEqual(self.lib.version_path(self.lib.book(uid),v['id']).read_text(encoding='utf-8'),'嵌套三层里的小说。')
        self.assertEqual((self.root/'合集.zip').read_bytes(),original)
        inside=Library(self.root/'程序数据',str(self.root));self.lib=inside;self.scan()
        self.ai=inside.cleaner;self.ai.configure({'api_key':'synthetic-test-key'})
        job,_=self.complete(self.ai.prepare({'id':self.book('合成测试')['id']}))
        self.ai.save({'job_id':job['id'],'selected':job['defaults']})
        count=self.lib.stats()['total'];self.scan();self.assertEqual(self.lib.stats()['total'],count)
        self.assertFalse(any('source'==b['title'] or '清理版' in b['title'] for b in self.lib.listing({'limit':100})['items']))

    def test_invalid_model_edits_are_rejected_and_guesses_unselected(self):
        text='第一章 回家\n正文图□馆。\n作者有话说：谢谢！\n重复 重复'
        edits=[edit('第一章 回家'),edit('不存在'),edit('重复'),edit('正文图□馆。','改写整章','author_note'),
               edit('作者有话说：谢谢！'),edit('谢谢！'),edit('图□馆','图书馆','censored'),edit('正文','猜测','censored')]
        valid,rejected=validate_edits(text,{'start':0,'end':len(text)},{'edits':edits},{'author_note','censored'})
        self.assertEqual(rejected,6);self.assertEqual(len(valid),2)
        self.assertTrue(valid[0]['selected']);self.assertFalse(valid[1]['selected'])
        with self.assertRaises(CleanError):validate_edits(text,{'start':0,'end':3},[],{'garbled'})
        self.assertEqual(plan_chunks('普通的正常小说。','focused',{'author_note','garbled','censored'}),[])
        self.assertTrue(all(c['end']-c['start']<=5000 for c in plan_chunks('甲'*12000,'full',{'garbled'})))

    def test_pause_resume_preserves_completed_segments_and_restart_state(self):
        self.source.write_text('第一章\n'+'甲'*12000,encoding='utf-8')
        job=self.ai.prepare({'id':self.uid,'mode':'full'});entered=threading.Event();release=threading.Event()
        def blocked(*args):entered.set();release.wait(3);return {'edits':[]},{}
        with patch.object(ClaudeClient,'analyze',side_effect=blocked):
            self.ai.start(job['id']);self.assertTrue(entered.wait(3))
            try:
                with self.assertRaises(CleanError):self.ai.configure({})
                self.ai.cancel(job['id'])
            finally:release.set()
            deadline=time.time()+3
            while self.ai.active and time.time()<deadline:time.sleep(.01)
        paused=self.ai.public(job['id']);self.assertEqual(paused['status'],'paused');self.assertEqual(paused['progress'],1)
        self.ai=Cleaner(self.lib);restored=self.ai.public(job['id']);self.assertEqual(restored['progress'],1)
        complete,remote=self.complete(restored,lambda *args:({'edits':[]},{}))
        self.assertEqual(remote.call_count,complete['chunks']-1)

    def test_failed_request_can_resume_and_does_not_write_source(self):
        original=self.source.read_bytes();job=self.ai.prepare({'id':self.uid})
        with patch.object(ClaudeClient,'analyze',side_effect=CleanError('合成接口失败')):
            self.ai.start(job['id']);deadline=time.time()+3
            while self.ai.active and time.time()<deadline:time.sleep(.01)
        self.assertEqual(self.ai.public(job['id'])['status'],'failed');self.assertEqual(self.source.read_bytes(),original)
        self.assertEqual(self.lib.versions(self.uid),[])
        job,_=self.complete(job);self.assertEqual(job['patch_total'],2)

    def test_epub_changes_text_across_markup_and_preserves_resources(self):
        source=self.root/'测试电子书.epub'
        container=b'<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>'
        opf=b'<package><manifest><item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/></spine></package>'
        chapter='<html xmlns="http://www.w3.org/1999/xhtml"><head><title>书</title></head><body><h1>第一章 回家</h1><p>她走进<b>图□</b>馆。</p><p>作者有话说：谢谢支持！</p></body></html>'.encode()
        source.write_bytes(packed({'mimetype':b'application/epub+zip','META-INF/container.xml':container,'content.opf':opf,'chapter.xhtml':chapter,'cover.jpg':b'original cover bytes'}))
        original=source.read_bytes();self.scan();uid=self.book('测试电子书')['id']
        job,_=self.complete(self.ai.prepare({'id':uid}));self.assertEqual(job['patch_total'],2)
        version=self.ai.save({'job_id':job['id'],'selected':[p['id'] for p in job['patches']]})
        target=self.lib.version_path(self.lib.book(uid),version['id'])
        self.assertEqual(source.read_bytes(),original)
        with zipfile.ZipFile(target) as zf:
            text=''.join(ET.fromstring(zf.read('chapter.xhtml')).itertext())
            self.assertIn('图书馆',text);self.assertNotIn('作者有话说',text);self.assertIn('第一章 回家',text)
            self.assertEqual(zf.read('cover.jpg'),b'original cover bytes');self.assertEqual(zf.read('content.opf'),opf)
        self.assertIn('图书馆',Document(target).text)
        with self.assertRaises(CleanError):Document(source).write_copy(source,[])
        with self.assertRaises(FileExistsError):Document(source).write_copy(target,[])
        self.assertEqual(decode_text(TEXT.encode('gb18030'))[0],TEXT)
        with self.assertRaises(CleanError):decode_text(b'\xff\xfe\x00')

    def test_http_routes_serve_ai_script_hide_key_and_group_versions(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.library=self.lib
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def get(path):
            with urllib.request.urlopen(base+path) as response:return response.read()
        def post(path,data):
            with urllib.request.urlopen(urllib.request.Request(base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})) as response:return json.load(response)
        try:
            self.assertIn(b'openCleanup',get('/ai.js'));self.assertNotIn(b'synthetic-test-key',get('/api/ai/config'))
            self.assertIn(b'lite.js',get('/lite/'));self.assertIn(b'lite.js',get('/lite'));self.assertIn(b'/api/clean/prepare',get('/lite/lite.js'))
            job=post('/api/clean/prepare',{'id':self.uid});job,_=self.complete(job)
            version=post('/api/clean/save',{'job_id':job['id'],'selected':job['defaults']})
            book=json.loads(get('/api/book?id='+self.uid));self.assertEqual(book['versions'][0]['id'],version['id'])
            self.assertEqual(book['version_count'],2)
        finally:server.shutdown();server.server_close();thread.join()

    def test_custom_instruction_checks_whole_text_and_is_never_automatic(self):
        with self.assertRaises(CleanError):self.ai.prepare({'id':self.uid,'categories':['custom']})
        with self.assertRaises(CleanError):self.ai.prepare({'id':self.uid,'categories':['custom'],'instruction':'改'*1001})
        original=self.source.read_bytes()
        job=self.ai.prepare({'id':self.uid,'categories':['custom'],'instruction':'  把「她」改成「小林」 '})
        self.assertEqual(job['options']['instruction'],'把「她」改成「小林」');self.assertEqual(job['chunks'],1)
        self.assertEqual(plan_chunks('普通的正常小说。','focused',{CUSTOM}),[{'start':0,'end':8}])
        calls=[]
        def custom(segment,*args):calls.append(args);return {'edits':[edit('她走进','小林走进','custom',.95)]},{}
        job,_=self.complete(job,custom)
        self.assertEqual(calls[0][2:],(['custom'],'把「她」改成「小林」'))
        self.assertEqual(job['defaults'],[job['patches'][0]['id']])
        version=self.ai.save({'job_id':job['id'],'selected':job['defaults']})
        self.assertIn('小林走进图□馆',Document(self.lib.version_path(self.lib.book(self.uid),version['id'])).text)
        self.assertEqual(self.source.read_bytes(),original)
        self.assertEqual(self.ai.prepare({'id':self.uid,'instruction':'不该带上'})['options']['instruction'],'')
        self.assertNotIn(CUSTOM,AUTO_OPTIONS['categories'])


class ClaudeContractTests(unittest.TestCase):
    def test_messages_contract_refusal_and_truncation_using_local_fake_provider(self):
        captured=[];reply={'stop_reason':'end_turn','content':[{'type':'text','text':'{"edits":[]}'}],'usage':{'input_tokens':1}}
        class Fake(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                captured.append((self.path,dict(self.headers),json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                payload=json.dumps(reply).encode();self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        server=ThreadingHTTPServer(('127.0.0.1',0),Fake);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=ClaudeClient({'base_url':f'http://127.0.0.1:{server.server_port}/v1','model':MODEL},'fake-contract-key')
            self.assertEqual(client.analyze('合成文字',categories=['garbled'])[0],{'edits':[]})
            path,headers,body=captured[0];self.assertEqual(path,'/v1/messages');self.assertEqual({k.lower():v for k,v in headers.items()}['x-api-key'],'fake-contract-key')
            self.assertEqual(body['model'],'claude-haiku-5-5');self.assertEqual(body['output_config']['format']['type'],'json_schema')
            self.assertEqual(body['thinking'],{'type':'disabled'});self.assertNotIn('temperature',body)
            self.assertEqual(json.loads(body['messages'][0]['content'])['segment'],'合成文字');self.assertEqual(body['system'],SYSTEM)
            client.analyze('合成文字',categories=['custom'],instruction='统一人名');self.assertTrue(captured[1][2]['system'].endswith('统一人名'))
            self.assertEqual(system_prompt('统一人名'),captured[1][2]['system'])
            for stop in ['refusal','max_tokens']:
                reply['stop_reason']=stop
                with self.assertRaises(CleanError):client.analyze('合成文字')
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
