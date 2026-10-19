"""爱妻的书屿 — a local catalog of novels and comics, without reading-history tracking."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from archive_engine import ArchiveEngine, ArchiveError, ARCHIVES, BOOKS as DOCUMENTS, IMAGES, member_key, source_chain
from platform_utils import reveal_in_explorer
from ai_cleaner import Cleaner, CleanError
from book_classification import Classifier, TAXONOMY
from app_runtime import bind_server, runtime_identity
from catalog_organizer import CatalogOrganizer, PUBLICATION_STATES, six_months_ago
from remote_access import RemoteAccess, SESSION_DAYS, LOCAL_HOSTS, is_loopback, lan_addresses
from wishlist import Wishlist
from auto_clean import AutoCleaner

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
    def __init__(self,data_dir,root=None,interval=300,background=False):
        # background: after each scan, also count new books locally and queue them for automatic cleanup.
        self.background = background
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
            CREATE TABLE IF NOT EXISTS book_versions (
              id TEXT PRIMARY KEY,book_id TEXT NOT NULL,number INTEGER NOT NULL,label TEXT NOT NULL,
              path TEXT NOT NULL,ext TEXT NOT NULL,size INTEGER NOT NULL,created REAL NOT NULL,
              parent_id TEXT NOT NULL,edit_count INTEGER NOT NULL,model TEXT NOT NULL,
              UNIQUE(book_id,number));
            CREATE INDEX IF NOT EXISTS versions_book ON book_versions(book_id);
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
        self.cleaner = Cleaner(self)
        self.classifier = Classifier(self)
        self.organizer = CatalogOrganizer(self)
        self.organizer.refresh()
        self.access = RemoteAccess(self)
        self.wishlist = Wishlist(self)
        self.auto = AutoCleaner(self)

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
                           and not (Path(current)/name).resolve().is_relative_to(self.data_dir)
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
            self.scan_state['phase'] = '合并重复书目'
            self.organizer.refresh()
            self.wishlist.match()
            if self.background:
                self.after_scan()
            self.scan_state['last_scan'] = time.time()
        except Exception as exc:
            self.scan_state['error'] = str(exc)
        finally:
            self.scan_state.update(running=False,phase='')
            self.scan_lock.release()

    def after_scan(self):
        with self.db() as db:
            uncounted = db.execute('''SELECT 1 FROM books b WHERE b.root=? AND b.missing=0 AND b.kind<>'archive'
              AND NOT EXISTS (SELECT 1 FROM book_text_metrics m WHERE m.book_id=b.id AND m.version_id='') LIMIT 1''',(self.root,)).fetchone()
        if uncounted:
            # Local only, never Claude: new books get word counts and classification without a manual click.
            self.classifier.start()
        self.auto.enqueue()

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
        book['versions'] = self.versions(uid)
        book['version_count'] = 1+len(book['versions'])
        book['classification'] = self.classifier.classification(uid)
        book['text_metrics'] = self.classifier.metrics(uid)
        book['copies'] = self.organizer.copies(uid)
        book['publication'] = self.organizer.publication(uid)
        book['download_url'] = self.wishlist.link(uid)
        book['auto_clean'] = self.auto.record(uid)
        return book

    def versions(self,uid):
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT id,number,label,ext,size,created,parent_id,edit_count,model,(SELECT word_count FROM book_text_metrics WHERE book_id=book_versions.book_id AND version_id=book_versions.id) word_count FROM book_versions WHERE book_id=? ORDER BY number DESC",(uid,))]

    def version_path(self,book,version_id=''):
        if not version_id:return self.external_path(book)
        with self.db() as db:
            row=db.execute('SELECT path FROM book_versions WHERE id=? AND book_id=?',(version_id,book['id'])).fetchone()
        if not row:raise UserError('这个版本不属于当前书目。')
        path=(self.data_dir/row['path']).resolve()
        if not path.is_relative_to(self.data_dir/'versions'/book['id']) or not path.is_file():
            raise UserError('版本文件不存在或无法访问。')
        return path

    def add_version(self,book,uid,path,parent_id,edit_count,model,label=''):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            number=db.execute('SELECT coalesce(max(number),0)+1 FROM book_versions WHERE book_id=?',(book['id'],)).fetchone()[0]
            label=re.sub(r'[\x00-\x1f]','',label.strip())[:80] or f'AI 清理版 {number}'
            db.execute('INSERT INTO book_versions VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (uid,book['id'],number,label,path.relative_to(self.data_dir).as_posix(),path.suffix.lower(),path.stat().st_size,time.time(),parent_id,edit_count,model))
        self.classifier.measure(book['id'],uid)
        return next(v for v in self.versions(book['id']) if v['id']==uid)|{'book_id':book['id']}

    def path(self,book):
        path = Path(book['path']).resolve()
        if not path.is_relative_to(Path(book['root']).resolve()):
            raise UserError('文件不在书库内。')
        if not path.exists():
            raise UserError('原文件暂时无法访问，请检查磁盘连接。')
        return path

    def listing(self,query):
        where,params = ['root=?','missing=0',"kind<>'archive'",self.organizer.visible_sql()],[self.root]
        for word in query.get('q','').strip().split():
            where.append("id IN (SELECT coalesce(d.canonical_id,s.id) FROM books s LEFT JOIN book_duplicates d ON d.book_id=s.id WHERE s.root=? AND s.missing=0 AND (s.title || ' ' || s.author LIKE ? ESCAPE '\\'))")
            params.extend([self.root,'%'+word.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'])
        def selections(field,choices):
            values=list(dict.fromkeys(v for v in str(query.get(field,'')).split(',') if v))
            if not set(values)<=set(choices):raise UserError('无效的筛选选项。')
            return values
        kinds=selections('kind',['text','epub','pdf','comic','other'])
        if kinds:
            where.append('id IN (SELECT coalesce(d.canonical_id,s.id) FROM books s LEFT JOIN book_duplicates d ON d.book_id=s.id WHERE s.root=? AND s.missing=0 AND s.kind IN ('+','.join('?' for _ in kinds)+'))');params.extend([self.root,*kinds])
        classification_where=[]
        for field,choices in [('level',TAXONOMY['levels']),('relationship',TAXONOMY['relationships']),('pairing',TAXONOMY['pairings'])]:
            values=selections(field,choices)
            if values:
                classification_where.append(f'{field} IN ('+','.join('?' for _ in values)+')');params.extend(values)
        tags=selections('tag',TAXONOMY['tags'])
        if tags:
            classification_where.append("EXISTS (SELECT 1 FROM json_each(book_classification.tags) WHERE value IN ("+','.join('?' for _ in tags)+'))');params.extend(tags)
        if classification_where:where.append('id IN (SELECT book_id FROM book_classification WHERE '+' AND '.join(classification_where)+')')
        if query.get('classification_status')=='pending':
            where.append("id NOT IN (SELECT book_id FROM book_classification WHERE status='classified')")
        publication=selections('publication',PUBLICATION_STATES)
        if publication:
            where.append('id IN (SELECT id FROM ('+self.organizer.publication_projection()+') WHERE pub_status IN ('+','.join('?' for _ in publication)+'))')
            params.extend([six_months_ago().isoformat(),self.root,*publication])
        order = {'added':'added DESC,mtime DESC','title':'title COLLATE NOCASE','size':'size DESC',
                 'words':"(SELECT word_count FROM book_text_metrics WHERE book_id=books.id AND version_id='') DESC"}.get(query.get('sort'),'mtime DESC')
        page = max(1,int(query.get('page',1)))
        limit = min(100,max(1,int(query.get('limit',48))))
        clause = ' AND '.join(where)
        with self.db() as db:
            total = db.execute('SELECT count(*) FROM books WHERE '+clause,params).fetchone()[0]
            rows = db.execute(f'SELECT {CATALOG_FIELDS},(SELECT count(*)+1 FROM book_versions WHERE book_id=books.id) AS version_count,(SELECT count(*)+1 FROM book_duplicates WHERE canonical_id=books.id) AS copy_count FROM books WHERE '+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?',params+[limit,(page-1)*limit]).fetchall()
            ids=[r['id'] for r in rows];classification={};metrics={}
            if ids:
                slots=','.join('?' for _ in ids)
                classification={r['book_id']:dict(r) for r in db.execute(f'SELECT book_id,level,relationship,pairing,tags,status,reviewed FROM book_classification WHERE book_id IN ({slots})',ids)}
                metrics={r['book_id']:dict(r) for r in db.execute(f"SELECT book_id,word_count,source_signature FROM book_text_metrics WHERE version_id='' AND book_id IN ({slots})",ids)}
        items=[]
        for row in rows:
            item=dict(row);value=classification.get(row['id'],{'level':'','relationship':'','pairing':'','tags':'[]','status':'pending','reviewed':False})
            value['tags']=json.loads(value['tags']);item['classification']=value;metric=metrics.get(row['id'])
            item['word_count']=metric['word_count'] if metric and metric['source_signature']==self.classifier.signature(row) else None
            item['publication']=self.organizer.publication(row['id']);items.append(item)
        return {'items':items,'total':total,'page':page,'pages':(total+limit-1)//limit}

    def issues(self):
        with self.db() as db:
            rows = db.execute("SELECT rel,issues FROM archive_catalog WHERE root=? AND present=1 AND issues<>'[]'",(self.root,)).fetchall()
        return [{**issue,'source':row['rel']} for row in rows for issue in json.loads(row['issues'])]

    def stats(self):
        with self.db() as db:
            rows = db.execute("SELECT kind,count(*) n FROM books WHERE root=? AND missing=0 AND kind<>'archive' AND "+self.organizer.visible_sql()+" GROUP BY kind",(self.root,)).fetchall()
            sources=db.execute("SELECT count(*) FROM books WHERE root=? AND missing=0 AND kind<>'archive'",(self.root,)).fetchone()[0]
        kinds = {r['kind']:r['n'] for r in rows}
        return {'total':sum(kinds.values()),'sources':sources,'merged':sources-sum(kinds.values()),'kinds':kinds,'root':self.root,'scan':dict(self.scan_state),
                'issues':len(self.issues()),'password_count':len(self.passwords),'interval':self.interval}

    def external_path(self,book):
        path = self.path(book)
        chain = source_chain(book)
        return self.engine.materialize(path,chain,book['name']) if chain else path

    def open_external(self,uid,version_id=''):
        book = self.book(uid)
        path = self.version_path(book,version_id)
        if not path.is_dir() and path.suffix.lower() not in BOOKS:
            raise UserError('此文件不支持使用系统应用打开。')
        if os.name=='nt':
            os.startfile(str(path))
        else:
            subprocess.Popen(['open' if __import__('sys').platform=='darwin' else 'xdg-open',str(path)])

    def reveal(self,uid,original=False,version_id=''):
        book = self.book(uid)
        path = self.path(book) if original else self.version_path(book,version_id)
        reveal_in_explorer(path)
        return {'ok':True,'path':str(path),'extracted':bool(source_chain(book)) and not original and not version_id}

    def download(self,uid,version_id=''):
        """Return (path,name,temporary) for sending the selected copy to another computer."""
        book = self.book(uid)
        path = self.version_path(book,version_id)
        if not path.is_dir():
            name = path.name if version_id or source_chain(book) else book['name']
            return path,Path(name).name,False
        # Image folders are sent as one uncompressed ZIP so the browser receives a single file.
        folder = self.data_dir/'downloads'
        folder.mkdir(exist_ok=True)
        target = folder/f'{hashlib.sha256(str(path).encode()).hexdigest()[:16]}-{time.time_ns()}.zip'
        with zipfile.ZipFile(target,'w',zipfile.ZIP_STORED) as archive:
            for item in sorted(path.rglob('*')):
                if item.is_file():archive.write(item,item.relative_to(path).as_posix())
        return target,path.name+'.zip',True

    def settings(self,data):
        if self.classifier.progress()['running']:
            raise UserError('分类和字数统计正在进行，请完成或停止后再切换书库。')
        if self.cleaner.active or self.auto.running:
            raise UserError('AI 任务正在处理，请完成或停止后再切换书库。')
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
    STATIC = {'/':'index.html','/app.js':'app.js','/ai.js':'ai.js','/classification.js':'classification.js','/wishlist.js':'wishlist.js','/style.css':'style.css','/favicon.svg':'favicon.svg'}
    PUBLIC_API = {'/api/session','/api/login','/api/download'}
    # These act on the Windows machine itself, so other computers may not call them.
    LOCAL_ONLY = {'/api/runtime/reload','/api/app/show','/api/open','/api/reveal','/api/access/password'}
    FORWARDED = ('X-Forwarded-For','Forwarded','Cf-Connecting-Ip','X-Real-Ip','Tailscale-User-Login')

    @property
    def lib(self):
        return self.server.library

    def log_message(self,*args):
        pass

    def same_origin(self):
        origin = self.headers.get('Origin')
        return not origin or urllib.parse.urlsplit(origin).netloc==self.headers.get('Host','')

    def allowed_origin(self):
        origin = self.headers.get('Origin','')
        return origin if origin and origin in getattr(self.server,'allowed_origins',()) else ''

    def is_local(self):
        # Tunnels connect from loopback too, so a proxied or cross-site request is never trusted as local.
        return (is_loopback(self.client_address[0]) and self.same_origin() and not any(self.headers.get(h) for h in self.FORWARDED)
                and self.headers.get('Host','') in {f'{h}:{self.server.server_port}' for h in LOCAL_HOSTS})

    def client_ip(self):
        # Behind a local tunnel every visitor arrives from loopback; the tunnel names the real address.
        forwarded = self.headers.get('Cf-Connecting-Ip') or self.headers.get('X-Forwarded-For','').split(',')[0].strip()
        return forwarded if forwarded and is_loopback(self.client_address[0]) else self.client_address[0]

    def token(self):
        scheme,_,value = self.headers.get('Authorization','').partition(' ')
        return value.strip() if scheme.lower()=='bearer' else ''

    def authorized(self):
        return self.is_local() or self.lib.access.valid(self.token())

    def guard(self,path):
        """Send a refusal and return False when this request may not reach the route."""
        if not self.same_origin() and not self.allowed_origin():
            self.json({'error':'不允许跨站请求。'},403);return False
        if path in self.LOCAL_ONLY and not self.is_local():
            self.json({'error':'此操作只能在存放书库的 Windows 电脑上进行。'},403);return False
        if path.startswith('/api/') and path not in self.PUBLIC_API and not self.authorized():
            self.json({'error':'请先登录。','login':True},401);return False
        return True

    def end_headers(self):
        origin = self.allowed_origin()
        if origin:
            self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Vary','Origin')
            self.send_header('Access-Control-Expose-Headers','Content-Disposition')
        super().end_headers()

    def do_OPTIONS(self):
        if not self.allowed_origin():
            return self.json({'error':'不允许跨站请求。'},403)
        self.send_response(204)
        self.send_header('Access-Control-Allow-Methods','GET, POST')
        self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type')
        self.send_header('Access-Control-Max-Age','600')
        self.send_header('Content-Length','0')
        self.end_headers()

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

    def json(self,data,status=200,extra=None):
        self.send_content(status,'application/json; charset=utf-8',json.dumps(data,ensure_ascii=False).encode(),extra)

    def send_file(self,path,name):
        fallback = re.sub(r'[^A-Za-z0-9._-]','_',name) or 'book'
        self.send_response(200)
        self.send_header('Content-Type',mimetypes.guess_type(name)[0] or 'application/octet-stream')
        self.send_header('Content-Length',str(path.stat().st_size))
        self.send_header('Content-Disposition',f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{urllib.parse.quote(name)}")
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        with open(path,'rb') as source:
            shutil.copyfileobj(source,self.wfile,1024*1024)

    def session(self):
        local = self.is_local()
        info = {'local':local,'authenticated':bool(self.authorized()),'password_set':self.lib.access.password_set}
        if local:
            info['origins'] = sorted(getattr(self.server,'allowed_origins',()))
            if getattr(self.server,'lan',False):
                info['addresses'] = [f'http://{a}:{self.server.server_port}' for a in lan_addresses()]
        return info

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if not self.guard(url.path):return
        query = {k:v[-1] for k,v in urllib.parse.parse_qs(url.query).items()}
        try:
            if url.path=='/api/runtime' and hasattr(self.server,'runtime'):
                return self.json({**self.server.runtime,'root':self.lib.root})
            if url.path=='/api/session':return self.json(self.session())
            if url.path=='/api/stats':return self.json(self.lib.stats())
            if url.path=='/api/books':return self.json(self.lib.listing(query))
            if url.path=='/api/book':return self.json(self.lib.book(query['id']))
            if url.path=='/api/issues':return self.json({'items':self.lib.issues()})
            if url.path=='/api/ai/config':return self.json(self.lib.cleaner.config())
            if url.path=='/api/ai/terminal':return self.json(self.lib.cleaner.terminal_status())
            if url.path=='/api/classification':return self.json(self.lib.classifier.summary())
            if url.path=='/api/facets':return self.json(self.lib.organizer.facets())
            if url.path=='/api/wishlist':return self.json(self.lib.wishlist.items(query.get('q','')))
            if url.path=='/api/auto-clean':return self.json(self.lib.auto.status())
            if url.path=='/api/clean/job':return self.json(self.lib.cleaner.public(query['job_id'],query.get('page',1)))
            if url.path=='/api/download':
                try:book_id,version_id = self.lib.access.redeem(query.get('ticket'))
                except PermissionError as exc:return self.json({'error':str(exc)},403)
                path,name,temporary = self.lib.download(book_id,version_id)
                try:return self.send_file(path,name)
                finally:
                    if temporary:path.unlink(missing_ok=True)
            if url.path in self.STATIC:
                path = BASE/'web'/self.STATIC[url.path]
                return self.send_content(200,(mimetypes.guess_type(path.name)[0] or 'text/plain')+'; charset=utf-8',path.read_bytes(),
                    {'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'none'; object-src 'none'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"})
            return self.json({'error':'Not found'},404)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        except (UserError,ArchiveError,CleanError,OSError,ValueError,KeyError) as exc:self.json({'error':str(exc)},400)

    def do_POST(self):
        if not self.guard(self.path):return
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self.json({'error':'Expected JSON'},415)
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0<length<=1024*1024:raise UserError('无效的请求长度。')
            data = json.loads(self.rfile.read(length))
            if self.path=='/api/login':
                try:token = self.lib.access.login(data.get('password'),self.client_ip())
                except PermissionError as exc:return self.json({'error':str(exc)},403)
                return self.json({'ok':True,'token':token,'days':SESSION_DAYS})
            if self.path=='/api/logout':
                self.lib.access.logout(self.token())
                return self.json({'ok':True})
            if self.path=='/api/download/ticket':
                self.lib.book(data['id'])
                return self.json({'url':'/api/download?'+urllib.parse.urlencode({'ticket':self.lib.access.ticket(data['id'],str(data.get('version_id','')))})})
            if self.path=='/api/access/password':
                self.lib.access.set_password(data.get('password'))
                return self.json({'ok':True})
            if self.path=='/api/app/show':
                if not callable(getattr(self.server,'on_show',None)):return self.json({'ok':False})
                self.server.on_show()
                return self.json({'ok':True})
            if self.path=='/api/runtime/reload' and hasattr(self.server,'runtime'):
                if data.get('instance')!=self.server.runtime['instance']:
                    return self.json({'error':'后台服务已变化，请重新启动。'},409)
                if self.lib.cleaner.active or self.lib.auto.running or self.lib.classifier.progress()['running']:
                    return self.json({'error':'AI 或分类任务正在进行，请完成或停止后再重新启动爱妻的书屿。'},409)
                self.json({'ok':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            if self.path=='/api/ai/config':
                config = self.lib.cleaner.configure(data)
                self.lib.auto.blocked = ''
                self.lib.auto.wake.set()
                return self.json(config)
            if self.path=='/api/wishlist/save':return self.json(self.lib.wishlist.save(data))
            if self.path=='/api/wishlist/delete':return self.json(self.lib.wishlist.delete(str(data.get('id',''))))
            if self.path=='/api/book/link':return self.json(self.lib.wishlist.set_link(data['id'],data.get('url','')))
            if self.path=='/api/auto-clean/config':return self.json(self.lib.auto.configure(data))
            if self.path=='/api/auto-clean/retry':return self.json(self.lib.auto.retry())
            if self.path=='/api/classification/start':return self.json(self.lib.classifier.start(bool(data.get('force')),str(data.get('mode','missing'))))
            if self.path=='/api/classification/stop':
                self.lib.classifier.stop.set();return self.json({'ok':True})
            if self.path=='/api/classification/save':return self.json(self.lib.classifier.save(data))
            if self.path=='/api/publication/save':return self.json(self.lib.organizer.save_publication(data))
            if self.path=='/api/duplicates/refresh':return self.json(self.lib.organizer.refresh())
            if self.path=='/api/classification/suggest':return self.json(self.lib.classifier.suggest(data))
            if self.path=='/api/text-metrics':return self.json(self.lib.classifier.measure(data['id'],str(data.get('version_id','')),bool(data.get('force'))))
            if self.path=='/api/ai/test':return self.json(self.lib.cleaner.test_connection())
            if self.path=='/api/clean/prepare':return self.json(self.lib.cleaner.prepare(data))
            if self.path=='/api/clean/start':return self.json(self.lib.cleaner.start(data['job_id']))
            if self.path=='/api/clean/cancel':return self.json(self.lib.cleaner.cancel(data['job_id']))
            if self.path=='/api/clean/save':return self.json(self.lib.cleaner.save(data))
            if self.path=='/api/scan':return self.json({'started':self.lib.start_scan(force=True)})
            if self.path=='/api/settings':
                self.lib.settings(data)
                return self.json({'ok':True})
            if self.path=='/api/reveal':return self.json(self.lib.reveal(data['id'],bool(data.get('original')),str(data.get('version_id',''))))
            if self.path=='/api/open':
                self.lib.open_external(data['id'],str(data.get('version_id','')))
                return self.json({'ok':True})
            return self.json({'error':'Not found'},404)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        except (UserError,ArchiveError,CleanError,OSError,ValueError,KeyError,TypeError) as exc:self.json({'error':str(exc)},400)


def default_data_dir():
    # The packaged app keeps data per Windows user, so replacing the app folder keeps the library.
    if getattr(sys,'frozen',False):
        return Path(os.environ.get('LOCALAPPDATA') or Path.home())/'AiqiShuyu'
    return BASE/'.data'


def build_parser():
    parser = argparse.ArgumentParser(description='爱妻的书屿 · 小说目录')
    parser.add_argument('--root',help='书库目录')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--data-dir',default=str(default_data_dir()))
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--lan',action='store_true',help='同时监听局域网，其他电脑可直接用 IP 地址登录访问')
    parser.add_argument('--allow-origin',action='append',default=[],help='允许调用后台的网页来源，例如 https://用户名.github.io（可重复）')
    return parser


def start(args):
    """Bind and load the library; None when a matching instance already serves this port."""
    identity = runtime_identity(BASE,args.data_dir,'0.0.0.0' if args.lan else '127.0.0.1')
    server = bind_server(args.port,Handler,identity,args.root)
    if server is None:
        return None
    try:
        lib = Library(args.data_dir,args.root,background=True)
    except BaseException:
        server.server_close()
        raise
    server.library = lib
    server.lan = args.lan
    server.allowed_origins = {o.rstrip('/') for o in args.allow_origin}
    lib.start_scan()
    lib.auto.start()
    threading.Thread(target=lib.auto_scan,daemon=True).start()
    return server


def main():
    args = build_parser().parse_args()
    server = start(args)
    url = f'http://127.0.0.1:{args.port}'
    if server is None:
        if not args.no_browser:webbrowser.open(url)
        return
    lib = server.library
    print(f'爱妻的书屿已启动：{url}\n书库：{lib.root}\n自动扫描所有子目录和嵌套压缩包。关闭此窗口即可停止。',flush=True)
    if server.allowed_origins:print('允许网页版访问：'+'，'.join(sorted(server.allowed_origins)),flush=True)
    if args.lan:
        addresses = '\n'.join(f'  http://{a}:{args.port}' for a in lan_addresses()) or '  （未检测到局域网地址）'
        print(f'局域网地址：\n{addresses}',flush=True)
    if not lib.access.password_set:print('尚未设置访问密码：请在本机页面「书库设置 → 网页版访问」中设置后，网页版才能登录。',flush=True)
    if not args.no_browser:threading.Timer(.6,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
