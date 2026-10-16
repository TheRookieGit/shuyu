"""Isolated fixture library for DOM integration tests; never uses the real book root."""
import sys
import threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_library import LibraryTests
from server import Handler, ThreadingHTTPServer
from ai_cleaner import ClaudeClient

case=LibraryTests()
case.setUp()
# Synthetic source and provider: UI tests exercise the real job/version HTTP routes,
# without sending any text to a remote service or touching the user's library.
(case.root/'未整理'/'《春日》作者：小林.txt').write_text('第一章 回家\n她走进图□馆。\n作者有话说：谢谢支持！\n第二章 出发\n故事继续。',encoding='utf-8')
case.scan()
case.lib.cleaner.configure({'api_key':'synthetic-ui-key'})
def synthetic_provider(segment,*args,**kwargs):
    edits=[]
    for before,after,category in [('作者有话说：谢谢支持！','','author_note'),('图□馆','图书馆','censored')]:
        if before in segment:edits.append(dict(before=before,after=after,category=category,confidence=.98,reason='合成测试建议'))
    return {'edits':edits},{'input_tokens':123,'output_tokens':45}
provider=patch.object(ClaudeClient,'analyze',side_effect=synthetic_provider)
provider.start()
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
server.library=case.lib
def shutdown_on_stdin():
    sys.stdin.readline()
    server.shutdown()
threading.Thread(target=shutdown_on_stdin,daemon=True).start()
print(f'READY http://127.0.0.1:{server.server_port}',flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
    provider.stop()
    case.tearDown()
