import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from app_runtime import AppHTTPServer, bind_server, code_fingerprint, runtime_identity
from server import Handler


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        (self.base/'server.py').write_text('old code', encoding='utf-8')
        (self.base/'web').mkdir()
        (self.base/'web'/'app.js').write_text('old frontend', encoding='utf-8')
        self.identity = runtime_identity(self.base, self.base/'data')
        self.server = AppHTTPServer(('127.0.0.1', 0), Handler)
        self.server.runtime = self.identity
        self.progress = {'running': False}
        self.server.library = SimpleNamespace(root=str(self.base/'books'),
            cleaner=SimpleNamespace(active=False),auto=SimpleNamespace(running=False),
            classifier=SimpleNamespace(progress=lambda: self.progress))
        self.port = self.server.server_port
        def serve():
            try:
                self.server.serve_forever(poll_interval=.01)
            finally:
                self.server.server_close()
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def test_same_build_reuses_existing_server_and_health_identifies_loaded_code(self):
        with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/api/runtime') as response:
            health = json.load(response)
        self.assertEqual(health['fingerprint'], self.identity['fingerprint'])
        self.assertEqual(health['root'], self.server.library.root)
        self.assertIsNone(bind_server(self.port, Handler, runtime_identity(self.base,self.base/'data')))
        self.assertTrue(self.thread.is_alive())

    def test_updated_build_replaces_server_on_same_port(self):
        (self.base/'server.py').write_text('new code', encoding='utf-8')
        new_identity = runtime_identity(self.base,self.base/'data')
        with patch('app_runtime.print'):
            replacement = bind_server(self.port, Handler, new_identity)
        try:
            self.assertEqual(replacement.server_port,self.port)
            self.assertEqual(replacement.runtime['fingerprint'],new_identity['fingerprint'])
            self.thread.join(timeout=5)
            self.assertFalse(self.thread.is_alive())
        finally:
            replacement.server_close()

    def test_active_job_blocks_reload_without_stopping_server(self):
        (self.base/'server.py').write_text('new code', encoding='utf-8')
        for active in ['cleaner','classifier']:
            with self.subTest(active=active):
                self.server.library.cleaner.active = active=='cleaner'
                self.progress['running'] = active=='classifier'
                with self.assertRaisesRegex(SystemExit,'任务正在进行'):
                    bind_server(self.port, Handler, runtime_identity(self.base,self.base/'data'))
                self.assertTrue(self.thread.is_alive())

    def test_different_data_directory_is_never_replaced(self):
        with self.assertRaisesRegex(SystemExit,'另一份书库'):
            bind_server(self.port, Handler, runtime_identity(self.base,self.base/'other-data'))
        self.assertTrue(self.thread.is_alive())

    def test_legacy_server_gives_explicit_restart_error(self):
        del self.server.runtime
        with self.assertRaisesRegex(SystemExit,'旧版爱妻的书屿'):
            bind_server(self.port, Handler, self.identity)
        self.assertTrue(self.thread.is_alive())

    def test_reload_requires_matching_instance(self):
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/api/runtime/reload',
            data=b'{"instance":"stale"}',headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request)
        self.assertEqual(ctx.exception.code,409)
        ctx.exception.close()
        self.assertTrue(self.thread.is_alive())

    def test_fingerprint_includes_frontend_and_new_modules(self):
        first = code_fingerprint(self.base)
        (self.base/'web'/'app.js').write_text('new frontend',encoding='utf-8')
        second = code_fingerprint(self.base)
        self.assertNotEqual(first,second)
        (self.base/'extra.py').write_text('new module',encoding='utf-8')
        self.assertNotEqual(second,code_fingerprint(self.base))



class UpdaterWaitTests(unittest.TestCase):
    def test_scans_delay_an_update_only_for_a_while_but_ai_tasks_block_it(self):
        import desktop_app
        lib=SimpleNamespace(cleaner=SimpleNamespace(active=False),auto=SimpleNamespace(running=False),
            classifier=SimpleNamespace(progress=lambda:{'running':False}),scan_state={'running':True})
        updater=desktop_app.Updater(SimpleNamespace(library=lib),'.',print,lambda:None)
        self.assertTrue(updater.busy(),'a scan that just started is waited for')
        updater.waiting_since-=desktop_app.SCAN_WAIT+1
        self.assertFalse(updater.busy(),'back-to-back scans no longer hold the update')
        lib.cleaner.active=True
        self.assertTrue(updater.busy(),'an AI cleanup is never interrupted')


if __name__=='__main__':unittest.main()
