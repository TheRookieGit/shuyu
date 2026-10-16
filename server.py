"""书屿 — a dependency-free, local-only library and reader."""
from __future__ import annotations

import argparse
import collections
from contextlib import contextmanager
import hashlib
import html
import io
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.etree import ElementTree as ET

BASE = Path(__file__).resolve().parent
DEFAULT_ROOT = r"Z:\老婆小说漫画"
TEXT = {'.txt', '.md', '.text'}
IMAGES = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif', '.bmp'}
ARCHIVES = {'.zip', '.rar', '.7z', '.cbz', '.cbr'}
BOOKS = TEXT | ARCHIVES | {'.epub', '.eqpub', '.pdf', '.docx', '.doc', '.mobi', '.azw3', '.azw', '.rtf', '.html', '.htm', '.fb2'}
MAX_READ = 96 * 1024 * 1024
NATURAL = lambda s: [int(p) if p.isdigit() else p.casefold() for p in re.split(r'(\d+)', s)]


class UserError(Exception):
    pass


def decode_text(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    if b'\x00' in data[:1000]:
        raise UserError('这个文件不像文本，建议使用系统应用打开。')
    for enc in ('utf-8-sig', 'gb18030', 'big5'):
        try:
            return data.decode(enc)
        except UnicodeError:
            pass
    return data.decode('utf-8', errors='replace')


def xml_text(data):
    # stdlib ElementTree does not fetch external entities.
    try:
        root = ET.fromstring(data)
        blocks = []
        def walk(node):
            tag = node.tag.rsplit('}', 1)[-1].lower()
            if tag in {'script', 'style', 'head'}:
                return
            if node.text:
                blocks.append(node.text)
            for child in node:
                walk(child)
                if child.tail:
                    blocks.append(child.tail)
            if tag in {'p', 'div', 'br', 'h1', 'h2', 'h3', 'section', 'li', 'tr'}:
                blocks.append('\n')
        walk(root)
        return re.sub(r'\n[ \t]*\n+', '\n\n', ''.join(blocks)).strip()
    except ET.ParseError:
        source = decode_text(data)
        source = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', source, flags=re.S | re.I)
        source = re.sub(r'</?(?:p|div|br|h[1-6]|section|li)\b[^>]*>', '\n', source, flags=re.I)
        return html.unescape(re.sub('<[^>]+>', '', source)).strip()


def zip_label(info):
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode('cp437').decode('gb18030')
    except (UnicodeError, LookupError):
        return info.filename


def metadata(name):
    stem = Path(name).stem
    author = ''
    match = re.search(r'(?:作者[：:\s]*|\s+[bB][yY]\s*)([^（(\[【]+)', stem)
    if match:
        author = match.group(1).strip(' _-')[:100]
    title_match = re.search(r'《(.+?)》', stem)
    title = title_match.group(1) if title_match else re.sub(r'^[\[【].*?[\]】]\s*', '', stem)
    if not title_match:
        title = re.split(r'作者[：:]|\s+[bB][yY]\s+', title)[0]
    return title.strip() or stem, author


class Library:
    def __init__(self, data_dir, root=None, interval=300):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / 'library.sqlite3'
        self.scan_lock = threading.Lock()
        self.cache_lock = threading.Lock()
        self.content_cache = collections.OrderedDict()
        self.scan_state = {'running': False, 'seen': 0, 'error': '', 'last_scan': None, 'skipped': 0}
        self.interval = interval
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS books (
              id TEXT PRIMARY KEY, root TEXT NOT NULL, rel TEXT NOT NULL, name TEXT NOT NULL,
              title TEXT NOT NULL, author TEXT NOT NULL, ext TEXT NOT NULL, kind TEXT NOT NULL,
              size INTEGER NOT NULL, mtime REAL NOT NULL, added REAL NOT NULL,
              seen TEXT NOT NULL, missing INTEGER DEFAULT 0, image_count INTEGER DEFAULT 0,
              favorite INTEGER DEFAULT 0, status TEXT DEFAULT 'unread', tags TEXT DEFAULT '',
              notes TEXT DEFAULT '', progress REAL DEFAULT 0, last_read REAL DEFAULT 0,
              last_entry TEXT DEFAULT '', UNIQUE(root,rel));
            CREATE INDEX IF NOT EXISTS books_root ON books(root,missing);
            CREATE TABLE IF NOT EXISTS reading (
              book_id TEXT NOT NULL, entry TEXT NOT NULL, position INTEGER DEFAULT 0,
              progress REAL DEFAULT 0, updated REAL NOT NULL, PRIMARY KEY(book_id,entry));
            ''')
            if root:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('root', str(Path(root).resolve())))
            elif not db.execute("SELECT 1 FROM settings WHERE key='root'").fetchone():
                db.execute('INSERT INTO settings VALUES (?,?)', ('root', DEFAULT_ROOT))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @property
    def root(self):
        with self.db() as db:
            return db.execute("SELECT value FROM settings WHERE key='root'").fetchone()[0]

    def start_scan(self):
        if not self.scan_lock.acquire(blocking=False):
            return False
        self.scan_state.update(running=True, seen=0, error='', skipped=0)
        threading.Thread(target=self._scan, daemon=True, name='library-scan').start()
        return True

    def _scan(self):
        root_string = self.root
        root = Path(root_string)
        generation = str(time.time_ns())
        started = time.time()
        pending = []
        traversal_errors = []
        def flush():
            if not pending:
                return
            with self.db() as db:
                db.executemany('''INSERT INTO books
                (id,root,rel,name,title,author,ext,kind,size,mtime,added,seen,image_count)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,title=excluded.title,author=excluded.author,
                ext=excluded.ext,kind=excluded.kind,size=excluded.size,mtime=excluded.mtime,
                seen=excluded.seen,missing=0,image_count=excluded.image_count''', pending)
            pending.clear()
        def add(path, ext, size, modified, count=0):
            rel = path.relative_to(root).as_posix()
            title, author = metadata(path.name)
            if ext == 'folder':
                title, author = path.name, ''
            kind = ('comic' if ext == 'folder' else 'text' if ext in TEXT else
                    'archive' if ext in ARCHIVES else 'epub' if ext in {'.epub', '.eqpub'} else
                    'pdf' if ext == '.pdf' else 'other')
            uid = hashlib.sha256((str(root).casefold() + '\0' + rel.casefold()).encode()).hexdigest()[:24]
            pending.append((uid, root_string, rel, path.name, title, author, ext, kind,
                            size, modified, started, generation, count))
            self.scan_state['seen'] += 1
            if len(pending) >= 160:
                flush()
        try:
            if not root.is_dir():
                raise UserError('书库目录暂时不可访问。请确认 Z 盘已连接，或在设置中更换路径。')
            for current, dirs, files in os.walk(root, followlinks=False, onerror=traversal_errors.append):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in {'__MACOSX', '$RECYCLE.BIN'}
                           and not (Path(current) / d).is_symlink()
                           and not getattr(Path(current) / d, 'is_junction', lambda: False)()]
                images = []
                for name in files:
                    if name.startswith('.') or name.startswith('~$'):
                        continue
                    path = Path(current) / name
                    ext = path.suffix.lower()
                    if ext not in BOOKS and ext not in IMAGES:
                        self.scan_state['skipped'] += 1
                        continue
                    try:
                        if path.is_symlink():
                            continue
                        st = path.stat()
                        if ext in IMAGES:
                            images.append(st)
                        else:
                            add(path, ext, st.st_size, st.st_mtime)
                    except OSError as exc:
                        traversal_errors.append(exc)
                if images:
                    add(Path(current), 'folder', sum(s.st_size for s in images), max(s.st_mtime for s in images), len(images))
            flush()
            # An interrupted/offline traversal must never mark unseen books missing.
            if not traversal_errors:
                with self.db() as db:
                    db.execute('UPDATE books SET missing=1 WHERE root=? AND seen<>?', (root_string, generation))
            else:
                self.scan_state['error'] = f'有 {len(traversal_errors)} 处无法读取，已保留原有记录。'
            self.scan_state['last_scan'] = time.time()
        except (OSError, UserError) as exc:
            self.scan_state['error'] = str(exc)
        except Exception as exc:
            self.scan_state['error'] = f'扫描未完成：{exc}'
        finally:
            self.scan_state['running'] = False
            self.scan_lock.release()

    def auto_scan(self):
        while True:
            time.sleep(self.interval)
            self.start_scan()

    def book(self, uid):
        with self.db() as db:
            row = db.execute('SELECT * FROM books WHERE id=? AND root=?', (uid, self.root)).fetchone()
        if not row:
            raise UserError('没有找到这本书，请刷新书架。')
        result = dict(row)
        result['path'] = str(Path(result['root']) / result['rel'])
        return result

    def path(self, book):
        path = Path(book['path']).resolve()
        root = Path(book['root']).resolve()
        if not path.is_relative_to(root):
            raise UserError('文件不在书库目录内。')
        if not path.exists():
            raise UserError('文件暂时不可访问，请确认磁盘已连接。')
        return path

    def listing(self, query):
        root = self.root
        where, params = ['root=?', 'missing=0'], [root]
        search = query.get('q', '').strip()
        if search:
            for word in search.split():
                where.append("(title || ' ' || author || ' ' || rel || ' ' || tags LIKE ? ESCAPE '\\')")
                params.append('%' + word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        kind = query.get('kind', '')
        if kind:
            where.append('kind=?')
            params.append(kind)
        view = query.get('view', 'all')
        if view == 'favorites':
            where.append('favorite=1')
        elif view == 'recent':
            where.append('last_read>0')
        elif view == 'reading':
            where.append("status='reading'")
        elif view == 'done':
            where.append("status='done'")
        folder = query.get('folder', '')
        if folder:
            escaped = folder.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            where.append("(rel LIKE ? ESCAPE '\\' OR rel=?)")
            params.extend([escaped + '/%', folder])
        order = {'added': 'added DESC, mtime DESC', 'title': 'title COLLATE NOCASE',
                 'recent': 'last_read DESC,mtime DESC', 'size': 'size DESC', 'modified': 'mtime DESC'}.get(query.get('sort'), 'mtime DESC')
        if view == 'recent' and query.get('sort', 'modified') == 'modified':
            order = 'last_read DESC'
        page = max(1, int(query.get('page', 1)))
        limit = min(100, max(1, int(query.get('limit', 48))))
        clause = ' AND '.join(where)
        with self.db() as db:
            total = db.execute('SELECT count(*) FROM books WHERE ' + clause, params).fetchone()[0]
            rows = db.execute('SELECT * FROM books WHERE ' + clause + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', params + [limit, (page-1)*limit]).fetchall()
        return {'items': [dict(r) for r in rows], 'total': total, 'page': page, 'pages': (total+limit-1)//limit}

    def stats(self):
        with self.db() as db:
            rows = db.execute('SELECT kind,count(*) n FROM books WHERE root=? AND missing=0 GROUP BY kind', (self.root,)).fetchall()
            counts = dict(db.execute('''SELECT count(*) total,coalesce(sum(favorite),0) favorites,
              coalesce(sum(status='reading'),0) reading,coalesce(sum(status='done'),0) done,
              coalesce(sum(last_read>0),0) recent FROM books WHERE root=? AND missing=0''', (self.root,)).fetchone())
            folders = db.execute("SELECT rel,ext FROM books WHERE root=? AND missing=0", (self.root,)).fetchall()
            latest = db.execute('SELECT * FROM books WHERE root=? AND missing=0 AND last_read>0 ORDER BY last_read DESC LIMIT 1', (self.root,)).fetchone()
        folder_counts = collections.Counter(r['rel'].split('/')[0] for r in folders if '/' in r['rel'] or r['ext']=='folder')
        return {**counts, 'kinds': {r['kind']:r['n'] for r in rows}, 'folders': sorted(folder_counts.items(), key=lambda x:NATURAL(x[0])),
                'latest': dict(latest) if latest else None, 'root': self.root, 'scan': dict(self.scan_state), 'interval': self.interval}

    def update(self, uid, data):
        self.book(uid)
        allowed = {'favorite', 'status', 'tags', 'notes'}
        values = {k:v for k,v in data.items() if k in allowed}
        if 'status' in values and values['status'] not in {'unread', 'reading', 'done'}:
            raise UserError('无效的阅读状态。')
        if 'favorite' in values:
            values['favorite'] = int(bool(values['favorite']))
        for key in ('tags', 'notes'):
            if key in values:
                values[key] = str(values[key])[:10000 if key == 'notes' else 500]
        if values:
            with self.db() as db:
                db.execute('UPDATE books SET ' + ','.join(k+'=?' for k in values) + ' WHERE id=?', list(values.values())+[uid])
        return self.book(uid)

    def progress(self, uid, entry, position, progress):
        self.book(uid)
        progress = max(0, min(1, float(progress)))
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO reading VALUES (?,?,?,?,?)', (uid, entry, max(0,int(position)),progress,time.time()))
            db.execute("UPDATE books SET last_entry=?,last_read=?,progress=?,status=CASE WHEN status='unread' THEN 'reading' ELSE status END WHERE id=?", (entry,time.time(),progress,uid))

    def archive_entries(self, path):
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                return [{'key':i.filename, 'name':zip_label(i), 'size':i.file_size, 'ext':Path(i.filename).suffix.lower()} for i in zf.infolist()
                        if not i.is_dir() and not i.filename.startswith('__MACOSX/') and not PurePosixPath(i.filename).name.startswith('.')][:30000]
        if not shutil.which('tar'):
            raise UserError('此压缩格式需要 Windows 自带的 tar，或请先解压到书库目录。')
        result = subprocess.run(['tar', '-tf', str(path)], capture_output=True, timeout=45, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:
            raise UserError('压缩包无法读取，可能有密码、损坏，或格式不受支持。可以在文件管理器中打开后解压。')
        names = decode_text(result.stdout).splitlines()
        return [{'key':n, 'name':n, 'size':0, 'ext':Path(n).suffix.lower()} for n in names if not n.endswith('/') and not n.startswith('__MACOSX/')][:30000]

    def archive_bytes(self, path, entry):
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                try:
                    info = zf.getinfo(entry)
                except KeyError:
                    raise UserError('压缩包内没有这个文件。')
                if info.file_size > MAX_READ:
                    raise UserError('包内文件超过 96 MB，请解压后阅读。')
                try:
                    return zf.read(info)
                except (RuntimeError, NotImplementedError, zipfile.BadZipFile):
                    raise UserError('包内文件有密码或使用了不支持的压缩方法，请先解压。')
        entries = self.archive_entries(path)
        if entry not in {e['key'] for e in entries}:
            raise UserError('压缩包内没有这个文件。')
        # Stream one member to stdout: no archive paths are ever extracted to disk.
        proc = subprocess.Popen(['tar','-xOf',str(path),'--',entry], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        timer = threading.Timer(60, proc.kill)
        timer.start()
        try:
            data = proc.stdout.read(MAX_READ+1)
            if len(data) > MAX_READ:
                proc.kill()
                raise UserError('包内文件超过 96 MB，请解压后阅读。')
            if proc.wait() != 0:
                raise UserError('包内文件读取失败，可能有密码或格式不受支持。')
            return data
        finally:
            timer.cancel()
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    def album_entries(self, path):
        return [{'key':p.name,'name':p.name,'size':p.stat().st_size,'ext':p.suffix.lower()} for p in sorted(path.iterdir(),key=lambda p:NATURAL(p.name)) if p.is_file() and not p.is_symlink() and p.suffix.lower() in IMAGES]

    def raw(self, book, entry=''):
        path = self.path(book)
        if book['ext'] == 'folder':
            if entry not in {e['key'] for e in self.album_entries(path)}:
                raise UserError('没有找到这张图片。')
            return path / entry
        if entry and book['ext'] in ARCHIVES:
            return self.archive_bytes(path, entry)
        return path

    def read_bytes(self, source):
        if isinstance(source, bytes):
            return source
        if source.stat().st_size > MAX_READ:
            raise UserError('文件超过 96 MB，请使用系统应用打开。')
        return source.read_bytes()

    def epub(self, data):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            def read(name):
                info = zf.getinfo(name)
                if info.file_size > MAX_READ:
                    raise UserError('电子书章节过大，请使用系统应用打开。')
                return zf.read(name)
            container = ET.fromstring(read('META-INF/container.xml'))
            opf_path = next(n.attrib['full-path'] for n in container.iter() if n.tag.endswith('rootfile'))
            opf = ET.fromstring(read(opf_path))
            base = str(PurePosixPath(opf_path).parent)
            manifest = {n.attrib['id']: n.attrib for n in opf.iter() if n.tag.endswith('}item') or n.tag == 'item'}
            chunks, chapters, offset = [], [], 0
            for node in opf.iter():
                if not node.tag.endswith('itemref'):
                    continue
                item = manifest.get(node.attrib.get('idref'), {})
                if 'html' not in item.get('media-type',''):
                    continue
                href = urllib.parse.unquote(item['href'].split('#')[0])
                member = os.path.normpath(base+'/'+href).replace('\\','/')
                text = xml_text(read(member)).replace('\r\n','\n').replace('\r','\n') + '\n\n'
                if offset + len(text) > MAX_READ:
                    raise UserError('电子书文字过多，请使用系统应用打开。')
                label = next((s.strip() for s in text.splitlines() if s.strip()), '章节')[:80]
                chapters.append({'title':label,'position':offset})
                chunks.append(text)
                offset += len(text)
            if not chunks:
                raise UserError('这本 EPUB 没有可读取的文字章节。')
            return ''.join(chunks), chapters

    def content(self, uid, entry='', start=0, length=14000):
        book = self.book(uid)
        path = self.path(book)
        st = path.stat()
        key = (uid,entry,st.st_mtime_ns,st.st_size)
        with self.cache_lock:
            cached = self.content_cache.get(key)
            if cached:
                self.content_cache.move_to_end(key)
        if not cached:
            source = self.raw(book,entry)
            ext = Path(entry).suffix.lower() if entry else book['ext']
            data = self.read_bytes(source)
            chapters = []
            if ext in {'.epub','.eqpub'}:
                text, chapters = self.epub(data)
            elif ext == '.docx':
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    info = zf.getinfo('word/document.xml')
                    if info.file_size > MAX_READ:
                        raise UserError('文档内容过大，请使用系统应用打开。')
                    text = xml_text(zf.read(info))
            elif ext in {'.html','.htm','.fb2'}:
                text = xml_text(data)
            elif ext in TEXT:
                text = decode_text(data)
            else:
                raise UserError('此格式暂不支持内置文字阅读，请使用系统应用打开。')
            text = text.replace('\r\n','\n').replace('\r','\n').replace('\x00','')
            if not chapters:
                pattern = r'(?m)^[ \t　]*(?:第[零〇一二三四五六七八九十百千万两\d]{1,12}[章节回卷部篇].{0,60}|(?:序章|楔子|尾声|后记|番外|Chapter\s+\d+).{0,60})$'
                chapters = [{'title':m.group().strip(),'position':m.start()} for m in re.finditer(pattern,text,flags=re.I)][:5000]
            cached = (text,chapters)
            with self.cache_lock:
                self.content_cache[key] = cached
                # Bound decoded text cache, including UTF-32 worst-case storage.
                while len(self.content_cache)>5 or sum(len(v[0])*4 for v in self.content_cache.values()) > 192*1024*1024:
                    self.content_cache.popitem(last=False)
        text, chapters = cached
        with self.db() as db:
            saved = db.execute('SELECT * FROM reading WHERE book_id=? AND entry=?',(uid,entry)).fetchone()
        if start < 0:
            start = saved['position'] if saved else 0
        start = min(max(0,start),max(0,len(text)-1))
        end = min(len(text),start+min(24000,max(1000,length)))
        if end<len(text):
            cut = text.rfind('\n',start+max(0,end-start-1000),end)
            if cut>start:
                end=cut+1
        return {'text':text[start:end], 'start':start,'end':end,'total':len(text),'chapters':chapters,'saved':dict(saved) if saved else None}


class Handler(BaseHTTPRequestHandler):
    server_version = 'Shuyu/1.0'

    @property
    def lib(self):
        return self.server.library

    def log_message(self, fmt, *args):
        if args and str(args[1] if len(args)>1 else '').startswith('5'):
            super().log_message(fmt,*args)

    def allowed(self):
        host = self.headers.get('Host','')
        return host in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}

    def send_content_headers(self, status, content_type, length, extra=None):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(length))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Cache-Control','no-store')
        for k,v in (extra or {}).items():
            self.send_header(k,v)
        self.end_headers()

    def json(self, data, status=200):
        payload = json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_content_headers(status,'application/json; charset=utf-8',len(payload))
        self.wfile.write(payload)

    def do_GET(self):
        if not self.allowed():
            return self.json({'error':'Invalid host'},403)
        url = urllib.parse.urlsplit(self.path)
        q = {k:v[-1] for k,v in urllib.parse.parse_qs(url.query).items()}
        try:
            if url.path=='/api/stats':
                return self.json(self.lib.stats())
            if url.path=='/api/books':
                return self.json(self.lib.listing(q))
            if url.path=='/api/book':
                book=self.lib.book(q['id'])
                return self.json(book)
            if url.path=='/api/entries':
                book=self.lib.book(q['id'])
                path=self.lib.path(book)
                entries=self.lib.album_entries(path) if book['ext']=='folder' else self.lib.archive_entries(path)
                entries.sort(key=lambda e:NATURAL(e['name']))
                with self.lib.db() as db:
                    read = {r['entry']:dict(r) for r in db.execute('SELECT * FROM reading WHERE book_id=?',(book['id'],))}
                for e in entries:
                    e['reading']=read.get(e['key'])
                return self.json({'entries':entries})
            if url.path=='/api/content':
                return self.json(self.lib.content(q['id'],q.get('entry',''),int(q.get('start',-1))))
            if url.path=='/api/raw':
                book=self.lib.book(q['id'])
                entry=q.get('entry','')
                source=self.lib.raw(book,entry)
                ext=Path(entry).suffix.lower() if entry else book['ext']
                mime='application/pdf' if ext=='.pdf' else mimetypes.guess_type('file'+ext)[0] if ext in IMAGES else 'application/octet-stream'
                # PDF uses the browser's own viewer, which must not inherit a document sandbox.
                displayable = ext == '.pdf' or ext in IMAGES
                filename = PurePosixPath(entry).name if entry else book['name']
                extra={'Accept-Ranges':'bytes', 'Content-Disposition':
                       ('inline' if displayable else 'attachment') + "; filename*=UTF-8''" + urllib.parse.quote(filename)}
                if not displayable:
                    extra['Content-Security-Policy']="default-src 'none'; sandbox"
                size=len(source) if isinstance(source,bytes) else source.stat().st_size
                start,end,status=0,size-1,200
                range_header=self.headers.get('Range','')
                if range_header and size:
                    match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
                    if not match or not any(match.groups()):
                        return self.json({'error':'Invalid range'},416)
                    a,b=match.groups()
                    if a:
                        start=int(a); end=min(size-1,int(b)) if b else size-1
                    else:
                        start=max(0,size-int(b))
                    if start>=size or end<start:
                        self.send_content_headers(416,'text/plain',0,{'Content-Range':f'bytes */{size}'})
                        return
                    status=206
                    extra['Content-Range']=f'bytes {start}-{end}/{size}'
                self.send_content_headers(status,mime or 'application/octet-stream',max(0,end-start+1),extra)
                if isinstance(source,bytes):
                    self.wfile.write(source[start:end+1])
                else:
                    with source.open('rb') as f:
                        f.seek(start)
                        remaining=end-start+1
                        while remaining>0:
                            chunk=f.read(min(256*1024,remaining))
                            if not chunk: break
                            self.wfile.write(chunk)
                            remaining-=len(chunk)
                return
            if url.path=='/api/export':
                with self.lib.db() as db:
                    books=[dict(r) for r in db.execute("SELECT rel,favorite,status,tags,notes,progress,last_read,last_entry FROM books WHERE root=? AND (favorite=1 OR status<>'unread' OR tags<>'' OR notes<>'')",(self.lib.root,))]
                    reading=[dict(r) for r in db.execute('SELECT b.rel,r.entry,r.position,r.progress,r.updated FROM reading r JOIN books b ON b.id=r.book_id WHERE b.root=?',(self.lib.root,))]
                payload=json.dumps({'version':1,'root':self.lib.root,'books':books,'reading':reading},ensure_ascii=False,indent=2).encode()
                self.send_content_headers(200,'application/json; charset=utf-8',len(payload),{'Content-Disposition':'attachment; filename="shuyu-backup.json"'})
                self.wfile.write(payload)
                return
            static={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/favicon.svg':'favicon.svg'}
            if url.path in static:
                path=BASE/'web'/static[url.path]
                data=path.read_bytes()
                self.send_content_headers(200,(mimetypes.guess_type(path.name)[0] or 'text/plain')+'; charset=utf-8',len(data),{'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self'; object-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"})
                self.wfile.write(data)
                return
            self.json({'error':'Not found'},404)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            pass
        except (UserError,OSError,ValueError,KeyError,zipfile.BadZipFile,ET.ParseError,StopIteration,subprocess.TimeoutExpired) as exc:
            self.json({'error':str(exc) or '文件格式无法解析，请用系统应用打开。'},400)
        except Exception:
            import traceback
            traceback.print_exc()
            self.json({'error':'操作未完成，请稍后重试。'},500)

    def do_POST(self):
        if not self.allowed():
            return self.json({'error':'Invalid host'},403)
        origin=self.headers.get('Origin')
        if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}:
            return self.json({'error':'不允许跨站请求。'},403)
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self.json({'error':'Expected JSON'},415)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>2*1024*1024:
                raise UserError('请求太大。')
            data=json.loads(self.rfile.read(length))
            if self.path=='/api/scan':
                return self.json({'started':self.lib.start_scan()})
            if self.path=='/api/update':
                return self.json(self.lib.update(data['id'],data))
            if self.path=='/api/progress':
                self.lib.progress(data['id'],data.get('entry',''),data.get('position',0),data.get('progress',0))
                return self.json({'ok':True})
            if self.path=='/api/settings':
                if self.lib.scan_state['running']:
                    raise UserError('扫描正在进行，请完成后再更换书库。')
                root=Path(str(data['root'])).expanduser().resolve()
                if not root.is_dir():
                    raise UserError('目录不存在或无法访问，请检查路径。')
                with self.lib.db() as db:
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('root',str(root)))
                self.lib.start_scan()
                return self.json({'ok':True})
            if self.path=='/api/reveal':
                book=self.lib.book(data['id'])
                path=self.lib.path(book)
                if os.name=='nt':
                    # Explorer requires /select,<path> as a single command-line argument.
                    subprocess.Popen(['explorer.exe', '/select,'+str(path)] if path.is_file() else ['explorer.exe',str(path)])
                else:
                    subprocess.Popen(['open' if __import__('sys').platform=='darwin' else 'xdg-open',str(path.parent if path.is_file() else path)])
                return self.json({'ok':True})
            if self.path=='/api/open':
                book=self.lib.book(data['id'])
                path=self.lib.path(book)
                if path.suffix.lower() not in BOOKS:
                    raise UserError('此文件不支持使用系统应用打开。')
                if os.name=='nt':
                    os.startfile(str(path))
                else:
                    subprocess.Popen(['open' if __import__('sys').platform=='darwin' else 'xdg-open',str(path)])
                return self.json({'ok':True})
            if self.path=='/api/import':
                backup=data['backup']
                if backup.get('version')!=1:
                    raise UserError('不支持此备份版本。')
                restored=0
                with self.lib.db() as db:
                    ids={r['rel']:r['id'] for r in db.execute('SELECT id,rel FROM books WHERE root=?',(self.lib.root,))}
                    for item in backup.get('books',[]):
                        uid=ids.get(item.get('rel'))
                        if uid:
                            status=item.get('status','unread')
                            if status not in {'unread','reading','done'}: status='unread'
                            db.execute('UPDATE books SET favorite=?,status=?,tags=?,notes=?,progress=?,last_read=?,last_entry=? WHERE id=?',
                              (int(bool(item.get('favorite'))),status,str(item.get('tags',''))[:500],str(item.get('notes',''))[:10000],max(0,min(1,float(item.get('progress',0)))),float(item.get('last_read',0)),str(item.get('last_entry','')),uid))
                            restored+=1
                    for item in backup.get('reading',[]):
                        if item.get('rel') in ids:
                            db.execute('INSERT OR REPLACE INTO reading VALUES (?,?,?,?,?)',(ids[item['rel']],str(item.get('entry','')),max(0,int(item.get('position',0))),max(0,min(1,float(item.get('progress',0)))),float(item.get('updated',0))))
                return self.json({'restored':restored})
            self.json({'error':'Not found'},404)
        except (UserError,OSError,ValueError,KeyError,TypeError) as exc:
            self.json({'error':str(exc)},400)


def main():
    parser=argparse.ArgumentParser(description='书屿 · 本地小说管理器')
    parser.add_argument('--root',help='书库文件夹（首次默认为 Z:\\老婆小说漫画）')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--data-dir',default=str(BASE/'.data'))
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    lib=Library(args.data_dir,args.root)
    # If this library is already running, bring its browser tab back instead.
    try:
        server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    except OSError:
        import urllib.request
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/api/stats',timeout=2) as response:
                if 'Shuyu/' not in response.headers.get('Server',''): raise OSError('端口被其他程序占用。')
            if not args.no_browser: webbrowser.open(f'http://127.0.0.1:{args.port}')
            return
        except Exception:
            raise SystemExit(f'端口 {args.port} 已被占用。可用 --port 指定其他端口。')
    server.library=lib
    lib.start_scan()
    threading.Thread(target=lib.auto_scan,daemon=True).start()
    url=f'http://127.0.0.1:{args.port}'
    print(f'书屿已启动：{url}\n书库：{lib.root}\n每 5 分钟自动扫描。关闭此窗口即可停止服务。',flush=True)
    if not args.no_browser:
        threading.Timer(.6,lambda:webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=='__main__':
    main()
