"""书屿 — a local catalog of novels and comics, without reading-history tracking."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from archive_engine import ArchiveEngine, ArchiveError, ARCHIVES, BOOKS as DOCUMENTS, IMAGES, member_key, source_chain
from platform_utils import reveal_in_explorer

BASE = Path(__file__).resolve().parent
DEFAULT_ROOT = r'Z:\老婆小说漫画'
TEXT = {'.txt','.text','.md'}
BOOKS = DOCUMENTS | ARCHIVES
CATALOG_FIELDS = 'id,root,rel,name,title,author,ext,kind,size,mtime,added,image_count,container,entry,chain'


class UserError(Exception):
    pass


def metadata(name):
    stem = Path(name).stem
    author = ''
    match = re.search(r'(?:作者[：:\s]*|\s+[bB][yY]\s*)([^（(\[【]+)',stem)
    if match:
        author = match.group(1).strip(' _-')[:100]
    match = re.search(r'《(.+?)》',stem)
    title = match.group(1) if match else re.sub(r'^[\[【].*?[\]】]\s*','',stem)
    if not match:
        title = re.split(r'作者[：:]|\s+[bB][yY]\s+',title)[0]
    return title.strip() or stem,author


class Library:
    def __init__(self,data_dir,root=None,interval=300):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True,exist_ok=True)
        self.db_path = self.data_dir/'library.sqlite3'
        self.scan_lock = threading.Lock()
        self.interval = interval
        self.scan_state = {'running':False,'seen':0,'error':'','last_scan':None,'skipped':0,'phase':''}
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS books (
              id TEXT PRIMARY KEY,root TEXT NOT NULL,rel TEXT NOT NULL,name TEXT NOT NULL,
              title TEXT NOT NULL,author TEXT NOT NULL,ext TEXT NOT NULL,kind TEXT NOT NULL,
              size INTEGER NOT NULL,mtime REAL NOT NULL,added REAL NOT NULL,seen TEXT NOT NULL,
              missing INTEGER DEFAULT 0,image_count INTEGER DEFAULT 0,
              container TEXT NOT NULL DEFAULT '',entry TEXT NOT NULL DEFAULT '',chain TEXT NOT NULL DEFAULT '[]',
              UNIQUE(root,rel));
            CREATE INDEX IF NOT EXISTS books_root ON books(root,missing);
            CREATE TABLE IF NOT EXISTS archive_catalog (
              root TEXT NOT NULL,rel TEXT NOT NULL,mtime_ns INTEGER NOT NULL,size INTEGER NOT NULL,
              revision TEXT NOT NULL,manifest TEXT NOT NULL,issues TEXT NOT NULL,seen TEXT NOT NULL,
              present INTEGER DEFAULT 1,PRIMARY KEY(root,rel));
            ''')
            # Old databases remain readable; obsolete annotations are never read or updated.
            columns = {r['name'] for r in db.execute('PRAGMA table_info(books)')}
            for column,default in [('container',''),('entry',''),('chain','[]')]:
                if column not in columns:
                    db.execute(f"ALTER TABLE books ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
            if root:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('root',str(Path(root).resolve())))
            elif not db.execute("SELECT 1 FROM settings WHERE key='root'").fetchone():
                db.execute('INSERT INTO settings VALUES (?,?)',('root',DEFAULT_ROOT))
        self.engine = ArchiveEngine(self.data_dir,self.passwords)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.db_path,timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @property
    def root(self):
        with self.db() as db:
            return db.execute("SELECT value FROM settings WHERE key='root'").fetchone()[0]

    @property
    def passwords(self):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key='archive_passwords'").fetchone()
        return json.loads(row[0]) if row else []

    def start_scan(self,force=False):
        if not self.scan_lock.acquire(blocking=False):
            return False
        if force:
            self.engine = ArchiveEngine(self.data_dir,self.passwords)
        self.scan_state.update(running=True,seen=0,error='',skipped=0,phase='扫描本地书目')
        threading.Thread(target=self._scan,args=(force,),daemon=True,name='library-scan').start()
        return True

    def identity(self,root,rel,chain):
        source = rel.casefold()+'\0'+member_key(chain) if chain else rel.casefold()
        return hashlib.sha256((root.casefold()+'\0'+source).encode()).hexdigest()[:24]

    def inventory(self,path,root,stat,generation,force):
        rel = path.relative_to(Path(root)).as_posix()
        revision = self.engine.revision
        with self.db() as db:
            cached = db.execute('SELECT * FROM archive_catalog WHERE root=? AND rel=?',(root,rel)).fetchone()
        if cached and cached['mtime_ns']==stat.st_mtime_ns and cached['size']==stat.st_size and cached['revision']==revision and (not force or cached['issues']=='[]'):
            with self.db() as db:
                db.execute('UPDATE archive_catalog SET seen=?,present=1 WHERE root=? AND rel=?',(generation,root,rel))
            return json.loads(cached['manifest']),True
        manifest,issues = self.engine.catalog(path)
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO archive_catalog VALUES (?,?,?,?,?,?,?,?,1)',
                       (root,rel,stat.st_mtime_ns,stat.st_size,revision,json.dumps(manifest,ensure_ascii=False),json.dumps(issues,ensure_ascii=False),generation))
        return manifest,False

    def _scan(self,force):
        root_string = self.root
        root = Path(root_string)
        generation = str(time.time_ns())
        pending,errors = [],[]
        def flush():
            if not pending:
                return
            with self.db() as db:
                db.executemany('''INSERT INTO books
                (id,root,rel,name,title,author,ext,kind,size,mtime,added,seen,image_count,container,entry,chain)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                rel=excluded.rel,name=excluded.name,title=excluded.title,author=excluded.author,
                ext=excluded.ext,kind=excluded.kind,size=excluded.size,mtime=excluded.mtime,
                seen=excluded.seen,missing=0,image_count=excluded.image_count,
                container=excluded.container,entry=excluded.entry,chain=excluded.chain''',pending)
            pending.clear()
        def add(path,ext,size,modified,image_count=0,leaf=None):
            source_rel = path.relative_to(root).as_posix()
            chain = leaf['chain'] if leaf else []
            container = source_rel if chain else ''
            entry = member_key(chain) if chain else ''
            rel = source_rel+'::'+entry if chain else source_rel
            name = leaf['name'].replace('\\','/').rsplit('/',1)[-1] if leaf else path.name
            title,author = metadata(name)
            kind = leaf.get('kind') if leaf else None
            if ext=='folder':
                title,author,kind = name,'','comic'
            kind = kind or ('text' if ext in TEXT else 'epub' if ext in {'.epub','.eqpub'} else 'pdf' if ext=='.pdf' else 'other')
            pending.append((self.identity(root_string,source_rel,chain),root_string,rel,name,title,author,ext,kind,size,modified,time.time(),generation,image_count,container,entry,json.dumps(chain,ensure_ascii=False)))
            self.scan_state['seen'] += 1
            if len(pending)>=300:
                flush()
        def touch(path,manifest):
            if not manifest:
                return True
            rel = path.relative_to(root).as_posix()
            ids = list(dict.fromkeys(self.identity(root_string,rel,item['chain']) for item in manifest))
            chunks = [ids[i:i+400] for i in range(0,len(ids),400)]
            with self.db() as db:
                count = 0
                for chunk in chunks:
                    clause = ','.join('?' for _ in chunk)
                    count += db.execute(f'SELECT count(*) FROM books WHERE id IN ({clause})',chunk).fetchone()[0]
                if count!=len(ids):
                    return False
                for chunk in chunks:
                    clause = ','.join('?' for _ in chunk)
                    db.execute(f'UPDATE books SET seen=?,missing=0 WHERE id IN ({clause})',[generation,*chunk])
            self.scan_state['seen'] += count
            return True
        try:
            if not root.is_dir():
                raise UserError('书库暂时无法访问，请确认磁盘已连接。')
            for current,dirs,files in os.walk(root,followlinks=False,onerror=errors.append):
                dirs[:] = [name for name in dirs if not name.startswith('.') and name not in {'__MACOSX','$RECYCLE.BIN'}
                           and not (Path(current)/name).is_symlink()
                           and not getattr(Path(current)/name,'is_junction',lambda:False)()]
                images,has_books = [],False
                for name in files:
                    if name.startswith('.') or name.startswith('~$'):
                        continue
                    path = Path(current)/name
                    ext = path.suffix.lower()
                    if ext not in BOOKS and ext not in IMAGES:
                        self.scan_state['skipped'] += 1
                        continue
                    has_books = has_books or ext in BOOKS
                    try:
                        if path.is_symlink():
                            continue
                        stat = path.stat()
                        if ext in IMAGES:
                            images.append(stat)
                        elif ext in ARCHIVES:
                            self.scan_state['phase'] = '自动展开压缩包'
                            manifest,cached = self.inventory(path,root_string,stat,generation,force)
                            if cached and touch(path,manifest):
                                continue
                            for leaf in manifest:
                                add(path,leaf['ext'],leaf['size'],stat.st_mtime,leaf.get('image_count',0),leaf)
                        else:
                            add(path,ext,stat.st_size,stat.st_mtime)
                    except OSError as exc:
                        errors.append(exc)
                if images and not has_books:
                    add(Path(current),'folder',sum(i.st_size for i in images),max(i.st_mtime for i in images),len(images))
            flush()
            if not errors:
                with self.db() as db:
                    db.execute('UPDATE books SET missing=1 WHERE root=? AND seen<>?',(root_string,generation))
                    db.execute('UPDATE archive_catalog SET present=0 WHERE root=? AND seen<>?',(root_string,generation))
            else:
                self.scan_state['error'] = f'有 {len(errors)} 处暂时无法读取，已保留原有索引。'
            self.scan_state['last_scan'] = time.time()
        except Exception as exc:
            self.scan_state['error'] = str(exc)
        finally:
            self.scan_state.update(running=False,phase='')
            self.scan_lock.release()

    def auto_scan(self):
        while True:
            time.sleep(self.interval)
            self.start_scan()

    def book(self,uid):
        with self.db() as db:
            row = db.execute(f'SELECT {CATALOG_FIELDS} FROM books WHERE id=? AND root=? AND kind<>?',(uid,self.root,'archive')).fetchone()
        if not row:
            raise UserError('没有找到这本小说，请重新扫描。')
        book = dict(row)
        book['path'] = str(Path(book['root'])/(book['container'] or book['rel']))
        return book

    def path(self,book):
        path = Path(book['path']).resolve()
        if not path.is_relative_to(Path(book['root']).resolve()):
            raise UserError('文件不在书库内。')
        if not path.exists():
            raise UserError('原文件暂时无法访问，请检查磁盘连接。')
        return path

    def listing(self,query):
        where,params = ['root=?','missing=0',"kind<>'archive'"],[self.root]
        for word in query.get('q','').strip().split():
            where.append("(title || ' ' || author LIKE ? ESCAPE '\\')")
            params.append('%'+word.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
        if query.get('kind'):
            where.append('kind=?');params.append(query['kind'])
        order = {'added':'added DESC,mtime DESC','title':'title COLLATE NOCASE','size':'size DESC'}.get(query.get('sort'),'mtime DESC')
        page = max(1,int(query.get('page',1)))
        limit = min(100,max(1,int(query.get('limit',48))))
        clause = ' AND '.join(where)
        with self.db() as db:
            total = db.execute('SELECT count(*) FROM books WHERE '+clause,params).fetchone()[0]
            rows = db.execute(f'SELECT {CATALOG_FIELDS} FROM books WHERE '+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?',params+[limit,(page-1)*limit]).fetchall()
        return {'items':[dict(row) for row in rows],'total':total,'page':page,'pages':(total+limit-1)//limit}

    def issues(self):
        with self.db() as db:
            rows = db.execute("SELECT rel,issues FROM archive_catalog WHERE root=? AND present=1 AND issues<>'[]'",(self.root,)).fetchall()
        return [{**issue,'source':row['rel']} for row in rows for issue in json.loads(row['issues'])]

    def stats(self):
        with self.db() as db:
            rows = db.execute("SELECT kind,count(*) n FROM books WHERE root=? AND missing=0 AND kind<>'archive' GROUP BY kind",(self.root,)).fetchall()
        kinds = {r['kind']:r['n'] for r in rows}
        return {'total':sum(kinds.values()),'kinds':kinds,'root':self.root,'scan':dict(self.scan_state),
                'issues':len(self.issues()),'password_count':len(self.passwords),'interval':self.interval}

    def external_path(self,book):
        path = self.path(book)
        chain = source_chain(book)
        return self.engine.materialize(path,chain,book['name']) if chain else path

    def open_external(self,uid):
        book = self.book(uid)
        path = self.external_path(book)
        if not path.is_dir() and path.suffix.lower() not in BOOKS:
            raise UserError('此文件不支持使用系统应用打开。')
        if os.name=='nt':
            os.startfile(str(path))
        else:
            subprocess.Popen(['open' if __import__('sys').platform=='darwin' else 'xdg-open',str(path)])

    def reveal(self,uid,original=False):
        book = self.book(uid)
        path = self.path(book) if original else self.external_path(book)
        reveal_in_explorer(path)
        return {'ok':True,'path':str(path),'extracted':bool(source_chain(book)) and not original}

    def settings(self,data):
        if self.scan_state['running']:
            raise UserError('扫描正在进行，请完成后再修改设置。')
        root = Path(str(data.get('root',self.root))).expanduser().resolve()
        if not root.is_dir():
            raise UserError('书库路径不存在或无法访问。')
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('root',str(root)))
            if 'passwords' in data:
                passwords = [str(p)[:200] for p in data['passwords'] if str(p)][:30]
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('archive_passwords',json.dumps(passwords,ensure_ascii=False)))
        self.engine = ArchiveEngine(self.data_dir,self.passwords)
        self.start_scan(force=True)


class Handler(BaseHTTPRequestHandler):
    server_version = 'Shuyu/2.0'

    @property
    def lib(self):
        return self.server.library

    def log_message(self,*args):
        pass

    def allowed(self):
        return self.headers.get('Host','') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}

    def send_content(self,status,content_type,payload,extra=None):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(payload)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Cache-Control','no-store')
        for key,value in (extra or {}).items():
            self.send_header(key,value)
        self.end_headers()
        self.wfile.write(payload)

    def json(self,data,status=200):
        self.send_content(status,'application/json; charset=utf-8',json.dumps(data,ensure_ascii=False).encode())

    def do_GET(self):
        if not self.allowed():
            return self.json({'error':'Invalid host'},403)
        url = urllib.parse.urlsplit(self.path)
        query = {k:v[-1] for k,v in urllib.parse.parse_qs(url.query).items()}
        try:
            if url.path=='/api/stats':return self.json(self.lib.stats())
            if url.path=='/api/books':return self.json(self.lib.listing(query))
            if url.path=='/api/book':return self.json(self.lib.book(query['id']))
            if url.path=='/api/issues':return self.json({'items':self.lib.issues()})
            static={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/favicon.svg':'favicon.svg'}
            if url.path in static:
                path = BASE/'web'/static[url.path]
                return self.send_content(200,(mimetypes.guess_type(path.name)[0] or 'text/plain')+'; charset=utf-8',path.read_bytes(),
                    {'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'none'; object-src 'none'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"})
            return self.json({'error':'Not found'},404)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        except (UserError,ArchiveError,OSError,ValueError,KeyError) as exc:self.json({'error':str(exc)},400)

    def do_POST(self):
        if not self.allowed():return self.json({'error':'Invalid host'},403)
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}:
            return self.json({'error':'不允许跨站请求。'},403)
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self.json({'error':'Expected JSON'},415)
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0<length<=1024*1024:raise UserError('无效的请求长度。')
            data = json.loads(self.rfile.read(length))
            if self.path=='/api/scan':return self.json({'started':self.lib.start_scan(force=True)})
            if self.path=='/api/settings':
                self.lib.settings(data)
                return self.json({'ok':True})
            if self.path=='/api/reveal':return self.json(self.lib.reveal(data['id'],bool(data.get('original'))))
            if self.path=='/api/open':
                self.lib.open_external(data['id'])
                return self.json({'ok':True})
            return self.json({'error':'Not found'},404)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        except (UserError,ArchiveError,OSError,ValueError,KeyError,TypeError) as exc:self.json({'error':str(exc)},400)


def main():
    parser = argparse.ArgumentParser(description='书屿 · 小说目录')
    parser.add_argument('--root',help='书库目录')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--data-dir',default=str(BASE/'.data'))
    parser.add_argument('--no-browser',action='store_true')
    args = parser.parse_args()
    lib = Library(args.data_dir,args.root)
    try:
        server = ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    except OSError:
        import urllib.request
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/api/stats',timeout=2) as response:
                if 'Shuyu/' not in response.headers.get('Server',''):raise OSError('端口被占用。')
            if not args.no_browser:webbrowser.open(f'http://127.0.0.1:{args.port}')
            return
        except Exception:
            raise SystemExit(f'端口 {args.port} 已被占用，可用 --port 指定其他端口。')
    server.library = lib
    lib.start_scan()
    threading.Thread(target=lib.auto_scan,daemon=True).start()
    url = f'http://127.0.0.1:{args.port}'
    print(f'书屿已启动：{url}\n书库：{lib.root}\n自动扫描所有子目录和嵌套压缩包。关闭此窗口即可停止。',flush=True)
    if not args.no_browser:threading.Timer(.6,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
