"""Isolated fixture library for DOM integration tests; never uses the real book root."""
import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_library import LibraryTests
from server import Handler, ThreadingHTTPServer

case=LibraryTests()
case.setUp()
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
    case.tearDown()
