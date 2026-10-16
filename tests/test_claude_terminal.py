import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from ai_cleaner import CleanError, ClaudeClient, MODEL
from claude_terminal import ClaudeTerminal, find_command
from tests.test_library import LibraryFixture


FAKE = '''import json, os, sys
from pathlib import Path
sys.stdin.reconfigure(encoding='utf-8');sys.stdout.reconfigure(encoding='utf-8')
root=Path(__file__).parent
control=json.loads((root/'control.json').read_text(encoding='utf-8'))
args=sys.argv[1:]
if args[-2:]==['auth','status']:
    print(json.dumps({'loggedIn':control.get('loggedIn',True),'authMethod':control.get('authMethod','claude.ai'),
        'apiProvider':control.get('apiProvider','firstParty'),'subscriptionType':control.get('subscriptionType','pro'),
        'email':'must-not-leak@example.test','orgId':'private-org'}))
    sys.exit(0)
payload=sys.stdin.read()
with (root/'calls.jsonl').open('a',encoding='utf-8') as stream:
    stream.write(json.dumps({'args':args,'input':payload,'cwd':str(Path.cwd()),
        'env':{k:os.environ.get(k) for k in ['ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','ANTHROPIC_BASE_URL',
            'CLAUDE_CODE_USE_BEDROCK','CLAUDE_CODE_API_KEY','CLAUDE_CODE_OAUTH_TOKEN']}},ensure_ascii=False)+'\\n')
mode=control.get('mode','success')
if mode=='limit':
    print(json.dumps({'subtype':'error_during_execution','is_error':True,'result':"You've hit your limit"}));sys.exit(1)
if mode=='failed':
    print('private-key-and-novel-must-not-leak',file=sys.stderr);sys.exit(42)
if mode=='bad':
    print('not JSON');sys.exit(0)
print(json.dumps({'subtype':'success','is_error':False,'structured_output':{'edits':control.get('edits',[])},
    'usage':{'input_tokens':10,'output_tokens':5},'total_cost_usd':99},ensure_ascii=False))
'''


def fake_terminal(case):
    temporary=tempfile.TemporaryDirectory(prefix='claude test 中文 ')
    case.addCleanup(temporary.cleanup)
    case.fake_root=Path(temporary.name);case.control=case.fake_root/'control.json'
    script=case.fake_root/'fake_cli.py';script.write_text(FAKE,encoding='utf-8')
    case.control.write_text('{}',encoding='utf-8')
    mocked=patch('claude_terminal.find_command',return_value=[sys.executable,str(script)])
    mocked.start();case.addCleanup(mocked.stop)
    case.client=ClaudeTerminal({'model':MODEL},case.fake_root/'work')


class TerminalContractTests(unittest.TestCase):
    def setUp(self):fake_terminal(self)

    def test_subscription_status_hides_account_details_and_rejects_api_or_missing_login(self):
        status=self.client.status();self.assertTrue(status['ready']);self.assertEqual(status['subscription_type'],'pro')
        self.assertNotIn('email',status);self.assertNotIn('orgId',status)
        for changes in [{'loggedIn':False},{'authMethod':'api_key'},{'apiProvider':'bedrock'},{'subscriptionType':'free'}]:
            self.control.write_text(json.dumps(changes),encoding='utf-8')
            self.assertFalse(self.client.status()['ready'])
            with self.assertRaises(CleanError):self.client.analyze('合成输入')
        self.assertFalse((self.fake_root/'calls.jsonl').exists(),'must not send text without subscription login')

    def test_native_argv_unicode_stdin_no_tools_and_no_api_credentials(self):
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'must-not-use','ANTHROPIC_AUTH_TOKEN':'must-not-use',
                'ANTHROPIC_BASE_URL':'https://example.invalid','CLAUDE_CODE_USE_BEDROCK':'1',
                'CLAUDE_CODE_API_KEY':'must-not-use','CLAUDE_CODE_OAUTH_TOKEN':'synthetic-subscription-token'}):
            result,usage=self.client.analyze('作者有话说：合成测试。',context_before='上文',categories=['author_note'])
            self.assertEqual(os.environ['ANTHROPIC_API_KEY'],'must-not-use','parent environment unchanged')
        self.assertEqual(result,{'edits':[]});self.assertEqual(usage['input_tokens'],10)
        call=json.loads((self.fake_root/'calls.jsonl').read_text(encoding='utf-8').splitlines()[0])
        args=call['args'];self.assertEqual(json.loads(call['input'])['segment'],'作者有话说：合成测试。')
        self.assertNotIn('作者有话说：合成测试。',args,'novel text only on stdin')
        self.assertEqual(args[args.index('--tools')+1],'');self.assertIn('--no-session-persistence',args)
        self.assertIn('--safe-mode',args);self.assertIn('--restricted',args);self.assertNotIn('--bare',args)
        self.assertEqual(args[args.index('--model')+1],MODEL)
        self.assertEqual(json.loads(args[args.index('--settings')+1]),{'forceLoginMethod':'claudeai'})
        self.assertEqual(call['cwd'],str(self.fake_root/'work'))
        for key,value in call['env'].items():
            self.assertEqual(value,'synthetic-subscription-token' if key=='CLAUDE_CODE_OAUTH_TOKEN' else None)

    def test_limit_malformed_output_and_process_failure_do_not_leak_content(self):
        for mode in ['limit','failed','bad']:
            self.control.write_text(json.dumps({'mode':mode}),encoding='utf-8')
            with self.assertRaises(CleanError) as caught:self.client.analyze('合成测试')
            self.assertNotIn('private-key-and-novel',str(caught.exception))
            if mode=='limit':self.assertIn('不会切换到 API',str(caught.exception))
        with patch('claude_terminal.subprocess.run',side_effect=subprocess.TimeoutExpired('claude',20)):
            status=self.client.status();self.assertFalse(status['ready']);self.assertIn('超时',status['message'])

    def test_npm_shim_resolves_native_binary_and_unknown_command_is_not_executed(self):
        shim=self.fake_root/'npm/claude.cmd';shim.parent.mkdir();shim.write_text('@echo off',encoding='ascii')
        native=shim.parent/'node_modules/@anthropic-ai/claude-code/bin/claude.exe';native.parent.mkdir(parents=True);native.write_bytes(b'fixture')
        self.assertEqual(find_command(str(shim)),[str(native)])
        self.assertEqual(find_command(str(shim)+' --arbitrary-command'),[])
        self.assertEqual(find_command(str(self.fake_root/'absent.exe')),[])


class TerminalLibraryTests(LibraryFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();fake_terminal(self)
        self.source=self.root/'合成命令行测试.txt';self.source.write_text('第一章 回家\n作者有话说：合成测试。\n正文继续。',encoding='utf-8')
        self.scan();self.uid=self.book('合成命令行测试')['id'];self.ai=self.lib.cleaner
        self.ai.configure({'transport':'terminal','clear_key':True})

    def finish(self,job):
        self.ai.start(job['id']);deadline=time.time()+8
        while self.ai.active and time.time()<deadline:time.sleep(.01)
        self.assertIsNone(self.ai.active)
        return self.ai.public(job['id'])

    def test_terminal_mode_without_key_uses_same_preview_save_and_book_grouping(self):
        self.control.write_text(json.dumps({'edits':[{'before':'作者有话说：合成测试。','after':'','category':'author_note',
            'confidence':.99,'reason':'删除合成作者话'}]},ensure_ascii=False),encoding='utf-8')
        original=self.source.read_bytes();count=self.lib.stats()['total']
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':''}):
            config=self.ai.config();self.assertFalse(config['key_set']);self.assertTrue(config['can_start'])
            job=self.ai.prepare({'id':self.uid});self.assertEqual(job['transport'],'terminal')
            with patch.object(ClaudeClient,'analyze') as api:
                job=self.finish(job);api.assert_not_called()
            self.assertEqual(job['status'],'ready',job['error']);self.assertEqual(job['patch_total'],1)
            v=self.ai.save({'job_id':job['id'],'selected':job['defaults']})
            target=self.lib.version_path(self.lib.book(self.uid),v['id'])
            self.assertNotIn('作者有话说',target.read_text(encoding='utf-8'))
        self.assertEqual(self.source.read_bytes(),original);self.scan()
        self.assertEqual(self.lib.stats()['total'],count);self.assertEqual(self.lib.book(self.uid)['version_count'],2)

    def test_limit_stops_task_and_retry_does_not_fallback_to_api(self):
        self.ai.configure({'api_key':'synthetic-unused-key'});original=self.source.read_bytes()
        job=self.ai.prepare({'id':self.uid});self.control.write_text('{"mode":"limit"}',encoding='utf-8')
        with patch.object(ClaudeClient,'analyze') as api:
            job=self.finish(job);api.assert_not_called()
        self.assertEqual(job['status'],'failed');self.assertEqual(job['progress'],0)
        self.assertIn('不会切换到 API',job['error']);self.assertEqual(self.source.read_bytes(),original)
        self.assertEqual(self.lib.versions(self.uid),[])
        self.control.write_text('{}',encoding='utf-8');job=self.finish(job);self.assertEqual(job['status'],'ready')
        self.ai.configure({'transport':'api'})
        self.assertEqual(self.ai.config()['transport'],'api');self.assertEqual(self.ai.key(),'synthetic-unused-key')

    def test_missing_subscription_blocks_start_before_sending_text(self):
        job=self.ai.prepare({'id':self.uid});self.control.write_text('{"authMethod":"api_key"}',encoding='utf-8')
        with patch.object(ClaudeClient,'analyze') as api:
            with self.assertRaises(CleanError):self.ai.start(job['id'])
            api.assert_not_called()
        self.assertIsNone(self.ai.active);self.assertEqual(self.ai.public(job['id'])['status'],'prepared')
        self.assertFalse((self.fake_root/'calls.jsonl').exists())


if __name__=='__main__':unittest.main()
