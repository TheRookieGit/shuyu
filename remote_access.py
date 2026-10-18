"""Password login for the hosted web page and other computers; the Windows machine itself stays trusted."""
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time

SESSION_DAYS = 30
ITERATIONS = 600_000
LOCAL_HOSTS = {'127.0.0.1', 'localhost', '[::1]'}


def is_loopback(address):
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def host_name(host):
    """Host header without its port, keeping IPv6 brackets."""
    if host.startswith('['):
        return host.split(']')[0]+']'
    return host.rsplit(':', 1)[0] if ':' in host else host


def lan_addresses():
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # UDP connect sends nothing; it only selects the outgoing interface.
            probe.connect(('192.0.2.1', 9))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in addresses if not is_loopback(a))


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS)
    return {'salt': salt.hex(), 'hash': digest.hex(), 'iterations': ITERATIONS}


class RemoteAccess:
    def __init__(self, library):
        self.lib = library
        self.failures = {}
        self.tickets = {}
        self.lock = threading.Lock()
        with self.lib.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY,created REAL NOT NULL,expires REAL NOT NULL)')
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))

    def _record(self):
        with self.lib.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key='remote_password'").fetchone()
        return json.loads(row[0]) if row else None

    @property
    def password_set(self):
        return self._record() is not None

    def set_password(self, password):
        password = str(password or '')
        if len(password) < 6:
            raise ValueError('访问密码至少需要 6 个字符。')
        if len(password) > 200:
            raise ValueError('访问密码过长。')
        with self.lib.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('remote_password', json.dumps(hash_password(password))))
            # A new password signs out every other computer.
            db.execute('DELETE FROM sessions')

    def login(self, password, client):
        with self.lock:
            count, until = self.failures.get(client, (0, 0))
            if until > time.time():
                raise PermissionError(f'尝试次数过多，请 {int(until-time.time())+1} 秒后再试。')
        record = self._record()
        if not record:
            raise PermissionError('还没有设置访问密码，请先在 Windows 电脑上打开书屿，在「书库设置」中设置。')
        expected = bytes.fromhex(record['hash'])
        actual = hashlib.pbkdf2_hmac('sha256', str(password or '').encode('utf-8'), bytes.fromhex(record['salt']), record['iterations'])
        if not hmac.compare_digest(expected, actual):
            with self.lock:
                count += 1
                self.failures[client] = (count, time.time()+min(300, 2**(count-4)*30) if count >= 5 else 0)
            raise PermissionError('密码不正确。')
        with self.lock:
            self.failures.pop(client, None)
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.lib.db() as db:
            db.execute('INSERT INTO sessions VALUES (?,?,?)', (self._digest(token), now, now+SESSION_DAYS*86400))
        return token

    def logout(self, token):
        if token:
            with self.lib.db() as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (self._digest(token),))

    def valid(self, token):
        if not token:
            return False
        with self.lib.db() as db:
            row = db.execute('SELECT expires FROM sessions WHERE token_hash=?', (self._digest(token),)).fetchone()
        return bool(row) and row[0] > time.time()

    def ticket(self, book_id, version_id=''):
        """A short-lived single-use link, because a browser download cannot carry the login header."""
        now = time.time()
        ticket = secrets.token_urlsafe(24)
        with self.lock:
            self.tickets = {k: v for k, v in self.tickets.items() if v[2] > now}
            self.tickets[ticket] = (book_id, version_id, now+120)
        return ticket

    def redeem(self, ticket):
        with self.lock:
            entry = self.tickets.pop(ticket or '', None)
        if not entry or entry[2] < time.time():
            raise PermissionError('下载链接已失效，请重新点击下载。')
        return entry[0], entry[1]

    @staticmethod
    def _digest(token):
        return hashlib.sha256(token.encode('ascii', 'ignore')).hexdigest()
