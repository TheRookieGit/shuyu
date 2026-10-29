"""Books to read that are not downloaded yet, and a download address for any book.

Connected to the wish hub (心愿中枢, the my-collections site), the wishes are the hub's novels instead: they are kept
here in hub_wishes, matched against the shelf exactly as before, and every match is reported back so the hub shows
the book as owned. A match the hub marks 「不是这个」 is not made again. The wishes kept here before connecting go up
to the hub once and stay as they were, for going back. Download addresses of books (book_links) never leave here.
The hub's original screenshots of a wish come along and can be added or removed here too, only while connected."""
from datetime import datetime, timezone
import json
from pathlib import PurePosixPath
import re
import threading
import time
import urllib.parse
import uuid

from catalog_organizer import normalized_name
from collectionkit.hub import Hub, HubError

KINDS = {'novel':'小说文件','folder':'文件夹','zip':'ZIP 压缩包'}
WEB = 'https://therookiegit.github.io/shuyu/'  # Where the hub's 「查看」 opens a matched book.
PULL_SECONDS = 60


def clean_url(value):
    url = str(value or '').strip()
    if len(url)>2048 or (url and not re.match(r'^https?://\S+$',url,re.I)):
        raise ValueError('下载地址应以 http:// 或 https:// 开头。')
    return url


def text(value,limit):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',str(value or '')).strip()[:limit]


def iso(seconds):
    return datetime.fromtimestamp(seconds,timezone.utc).isoformat().replace('+00:00','Z')


def seconds(value,default):
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
    except ValueError:return default


class Wishlist:
    def __init__(self,lib,hub=None):
        self.lib = lib
        self.hub = hub or Hub('shuyu')
        self.pulled = 0
        self.pulling = threading.Lock()
        with lib.db() as db:db.executescript('''
          CREATE TABLE IF NOT EXISTS wishlist (
            id TEXT PRIMARY KEY,root TEXT NOT NULL,title TEXT NOT NULL,author TEXT NOT NULL,kind TEXT NOT NULL,
            url TEXT NOT NULL,note TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,book_id TEXT NOT NULL DEFAULT '');
          CREATE INDEX IF NOT EXISTS wishlist_root ON wishlist(root,book_id);
          CREATE TABLE IF NOT EXISTS book_links (book_id TEXT PRIMARY KEY,url TEXT NOT NULL,updated REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS hub_wishes (
            id TEXT PRIMARY KEY,title TEXT NOT NULL,author TEXT NOT NULL,kind TEXT NOT NULL,url TEXT NOT NULL,note TEXT NOT NULL,
            created REAL NOT NULL,updated REAL NOT NULL,data TEXT NOT NULL,root TEXT NOT NULL DEFAULT '',book_id TEXT NOT NULL DEFAULT '',
            rejected TEXT NOT NULL DEFAULT '[]');
        ''')

    # The wishes, from here or from the hub ------------------------------------------------------------------------

    def rows(self):
        """Wishes for the current library; book_id is set only for one on its shelf."""
        root = self.lib.root
        with self.lib.db() as db:
            if not self.hub.paired:
                return [dict(r) for r in db.execute("SELECT * FROM wishlist WHERE root=? ORDER BY book_id<>'',created DESC",(root,))]
            rows = [dict(r) for r in db.execute('SELECT * FROM hub_wishes ORDER BY created DESC')]
        for row in rows:
            if row['root']!=root:row['book_id'] = ''
        return sorted(rows,key=lambda r:r['book_id']!='')

    def items(self,q=''):
        self.refresh_soon()
        words = [w.casefold() for w in str(q).split()]
        rows = [r for r in self.rows() if all(w in (r['title']+' '+r['author']+' '+r['note']).casefold() for w in words)]
        for row in rows:
            row['downloaded'] = bool(row['book_id'])
            row['shots'] = self.shots_of(row)
            for key in ('data','rejected','root'):row.pop(key,None)
        return {'items':rows,'pending':sum(not r['downloaded'] for r in rows),'kinds':KINDS,'hub':self.hub.paired}

    def save(self,data):
        title = text(data.get('title'),200)
        if not title:raise ValueError('请填写书名或文件夹名。')
        kind = str(data.get('kind','novel'))
        if kind not in KINDS:raise ValueError('请选择小说文件、文件夹或 ZIP 压缩包。')
        values = (title,text(data.get('author'),100),kind,clean_url(data.get('url')),text(data.get('note'),1000))
        uid = str(data.get('id') or '')
        now = time.time()
        if self.hub.paired:uid = self.save_to_hub(uid,values,now,data.get('shots'))
        else:
            with self.lib.db() as db:
                if uid:
                    if not db.execute('UPDATE wishlist SET title=?,author=?,kind=?,url=?,note=?,updated=? WHERE id=? AND root=?',(*values,now,uid,self.lib.root)).rowcount:
                        raise ValueError('没有找到这条想看记录。')
                else:
                    uid = uuid.uuid4().hex
                    db.execute('INSERT INTO wishlist VALUES (?,?,?,?,?,?,?,?,?,?)',(uid,self.lib.root,*values,now,now,''))
        self.match()
        return self.item(uid)

    def item(self,uid):
        row = next((r for r in self.rows() if r['id']==uid),None)
        if not row:raise ValueError('没有找到这条想看记录。')
        return {k:v for k,v in row.items() if k not in ('data','rejected','root')}|{'downloaded':bool(row['book_id']),'shots':self.shots_of(row)}

    def delete(self,uid):
        if self.hub.paired:
            with self.lib.db() as db:
                row = db.execute('SELECT data FROM hub_wishes WHERE id=?',(uid,)).fetchone()
                db.execute('DELETE FROM hub_wishes WHERE id=?',(uid,))
            # Deleted in the hub too, as the hub page deletes: a tombstone that every device follows.
            if row:self.hub.put_wish({**json.loads(row['data']),'deleted':True,'updatedAt':iso(time.time())})
            return {'ok':True}
        with self.lib.db() as db:
            db.execute('DELETE FROM wishlist WHERE id=? AND root=?',(uid,self.lib.root))
        return {'ok':True}

    def link(self,uid):
        ids = self.lib.organizer.ids(uid) or [uid]
        with self.lib.db() as db:
            row = db.execute(f"SELECT url FROM book_links WHERE book_id IN ({','.join('?' for _ in ids)}) ORDER BY updated DESC",ids).fetchone()
        return row[0] if row else ''

    def set_link(self,uid,url):
        self.lib.book(uid)
        url = clean_url(url)
        with self.lib.db() as db:
            # Source copies of one book share its download address.
            for copy in self.lib.organizer.ids(uid) or [uid]:
                if url:db.execute('INSERT OR REPLACE INTO book_links VALUES (?,?,?)',(copy,url,time.time()))
                else:db.execute('DELETE FROM book_links WHERE book_id=?',(copy,))
        return {'url':url}

    # Matching -----------------------------------------------------------------------------------------------------

    @staticmethod
    def keys(book):
        """Names a downloaded wish can match: the book title, its ZIP and the folders it sits in."""
        source = PurePosixPath(book['container'] or book['rel'].split('::')[0])
        names = {book['title']}
        if book['container']:names.add(source.stem)
        names.update(source.parent.parts)
        if book['kind']=='comic':names.add(source.name)
        return {normalized_name(n) for n in names if n}

    def match(self):
        """Mark wishes as downloaded once a book with the same name appears on the shelf; tell the hub about each."""
        root = self.lib.root
        paired = self.hub.paired
        with self.lib.db() as db:
            if paired:wishes = [dict(r) for r in db.execute("SELECT * FROM hub_wishes WHERE book_id='' OR root<>?",(root,))]
            else:wishes = [dict(r) for r in db.execute("SELECT * FROM wishlist WHERE root=? AND book_id=''",(root,))]
        matched,reports = 0,[]
        for wish in wishes:
            key = normalized_name(wish['title'])
            if len(key)<2:continue
            pattern = '%'+wish['title'].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            with self.lib.db() as db:
                rows = [dict(r) for r in db.execute("SELECT id,title,author,kind,rel,container FROM books WHERE root=? AND missing=0 AND kind<>'archive' AND (title LIKE ? ESCAPE '\\' OR rel LIKE ? ESCAPE '\\') LIMIT 500",(root,pattern,pattern))]
            author = normalized_name(wish['author'])
            # Books the hub was told are not this wish (「不是这个」) are passed over.
            rejected = set(json.loads(wish.get('rejected') or '[]'))
            found = next((r for r in rows if key in self.keys(r) and not (author and r['author'] and normalized_name(r['author'])!=author)
                          and not (rejected and self.lib.organizer.canonical(r['id']) in rejected)),None)
            if not found:continue
            book_id = self.lib.organizer.canonical(found['id'])
            with self.lib.db() as db:
                if paired:db.execute('UPDATE hub_wishes SET book_id=?,root=? WHERE id=?',(book_id,root,wish['id']))
                else:db.execute('UPDATE wishlist SET book_id=?,updated=? WHERE id=?',(book_id,time.time(),wish['id']))
            if wish['url'] and not self.link(book_id):self.set_link(book_id,wish['url'])
            if paired:reports.append({'itemId':wish['id'],'ref':book_id,'url':WEB+'#book='+urllib.parse.quote(book_id),
                                      'title':found['title'],'ownedAt':iso(time.time())})
            matched += 1
        # Queued while the hub cannot be reached, so a scan never waits on it.
        if reports:self.hub.fulfill(reports)
        return matched

    # The wish hub -------------------------------------------------------------------------------------------------

    @staticmethod
    def shots_of(row):
        try:shots = json.loads(row.get('data') or '{}').get('shots')
        except ValueError:shots = None
        return [s for s in shots if isinstance(s,str)] if isinstance(shots,list) else []

    def shot(self,shot_id):
        """One of the hub's screenshots as a data URL; fetched from the hub the first time."""
        try:data = self.hub.shot(str(shot_id or ''))
        except HubError as exc:raise ValueError(str(exc)) from None
        if not data:raise ValueError('心愿中枢里没有这张截图了。')
        return {'data':data}

    def add_shot(self,data):
        """Keep a screenshot for a wish; its id goes in the wish's shots when the wish is saved."""
        if not self.hub.paired:raise ValueError('连接心愿中枢后才能保存截图。')
        try:return {'id':self.hub.add_shot(data)}
        except HubError as exc:raise ValueError(str(exc)) from None

    def save_to_hub(self,uid,values,now,shots=None):
        title,author,kind,url,note = values
        with self.lib.db() as db:
            row = db.execute('SELECT data FROM hub_wishes WHERE id=?',(uid,)).fetchone() if uid else None
        if uid and not row:raise ValueError('没有找到这条想看记录。')
        item = json.loads(row['data']) if row else {'id':uuid.uuid4().hex,'category':'novel','createdAt':iso(now),'done':False,'doneAt':None,
                                                     'rating':3,'source':'shuyu','aliases':[],'externalIds':{}}
        extra = {k:v for k,v in (item.get('extra') or {}).items() if k!='download'}
        if author:extra['author'] = author
        else:extra.pop('author',None)
        # The address shown here is the hub's first link: kept as it was (with its extraction code) while unchanged,
        # replaced when edited. Links added in the hub stay after it.
        old = [l for l in item.get('links') or [] if l]
        first = old[0] if old and self.url_in(old[0])==url else url
        links = ([first] if first else [])+[l for l in old[1:] if self.url_in(l)!=url]
        item.update(title=title,kind=kind,note=note,extra=extra,links=links[:20],updatedAt=iso(now))
        if isinstance(shots,list):item['shots'] = [str(s)[:64] for s in shots if isinstance(s,str) and s][:30]
        with self.lib.db() as db:self.store(db,item)
        self.hub.put_wish(item)
        return item['id']

    @staticmethod
    def url_in(link):
        found = re.search(r'https?://\S+',str(link or ''))
        return found.group(0) if found else ''

    def store(self,db,item):
        """Keep one of the hub's wishes here, or drop it when deleted; a changed name or kind is matched again."""
        if item.get('deleted') or item.get('category')!='novel':
            db.execute('DELETE FROM hub_wishes WHERE id=?',(item['id'],));return
        extra = item.get('extra') or {}
        links = [l for l in item.get('links') or [] if l]+([extra['download']] if extra.get('download') else [])
        url = next((self.url_in(l) for l in links if self.url_in(l) and len(self.url_in(l))<=2048),'')
        values = (text(item.get('title'),200) or '未命名',text(extra.get('author'),100),item.get('kind') if item.get('kind') in KINDS else 'novel',url,text(item.get('note'),1000))
        now = time.time()
        old = db.execute('SELECT title,author,kind,root,book_id,rejected FROM hub_wishes WHERE id=?',(item['id'],)).fetchone()
        same = old and (old['title'],old['author'],old['kind'])==values[:3]
        db.execute('INSERT OR REPLACE INTO hub_wishes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   (item['id'],*values,seconds(item.get('createdAt'),now),seconds(item.get('updatedAt'),now),json.dumps(item,ensure_ascii=False),
                    old['root'] if same else '',old['book_id'] if same else '',old['rejected'] if old else '[]'))

    def pull(self):
        """Bring in the hub's novel wishes changed since last time and match them; False when not connected or offline."""
        if not self.hub.paired:return False
        with self.pulling:
            with self.lib.db() as db:
                row = db.execute("SELECT value FROM settings WHERE key='hub_cursor'").fetchone()
            try:
                self.hub.flush()
                data = self.hub.wishes(['novel'],row[0] if row else None)
            except HubError:return False
            rejected = {}
            for f in data.get('fulfill') or []:
                if f.get('rejected'):rejected.setdefault(f['itemId'],[]).append(f['ref'])
            with self.lib.db() as db:
                for item in data.get('items') or []:
                    if isinstance(item,dict) and item.get('id'):self.store(db,item)
                db.execute("UPDATE hub_wishes SET rejected='[]'")
                for wish_id,refs in rejected.items():db.execute('UPDATE hub_wishes SET rejected=? WHERE id=?',(json.dumps(refs),wish_id))
                # A match the hub says is wrong is undone, and matched afresh below.
                db.execute("UPDATE hub_wishes SET root='',book_id='' WHERE book_id<>'' AND book_id IN (SELECT value FROM json_each(hub_wishes.rejected))")
                db.execute("INSERT OR REPLACE INTO settings VALUES ('hub_cursor',?)",(data['cursor'],))
            self.pulled = time.time()
        self.match()
        return True

    def refresh(self):
        """After a scan: the hub's latest wishes when it can be reached, and matching either way."""
        if not self.pull():self.match()

    def refresh_soon(self):
        """Opening the wishlist pulls in the background, so the page never waits on the hub."""
        if self.hub.paired and time.time()-self.pulled>PULL_SECONDS and not self.pulling.locked():
            self.pulled = time.time()
            threading.Thread(target=self.pull,daemon=True).start()

    def hub_status(self):
        with self.lib.db() as db:
            count = db.execute('SELECT COUNT(*) FROM hub_wishes').fetchone()[0]
        return self.hub.status()|{'wishes':count if self.hub.paired else 0}

    def pair(self,token):
        """Connect to the hub. The first time, this library's wishes go up, leaving out ones the hub already has."""
        try:self.hub.pair(token)
        except HubError as exc:raise ValueError(str(exc)) from None
        with self.lib.db() as db:
            db.execute('DELETE FROM hub_wishes');db.execute("DELETE FROM settings WHERE key='hub_cursor'")
            uploaded = db.execute("SELECT 1 FROM settings WHERE key='hub_uploaded'").fetchone()
        if not self.pull():raise ValueError('连接上了，但没能读取心愿中枢的想看清单，请稍后再试。')
        if not uploaded:
            with self.lib.db() as db:
                have = {(normalized_name(r['title']),normalized_name(r['author'])) for r in db.execute('SELECT title,author FROM hub_wishes')}
                local = [dict(r) for r in db.execute('SELECT * FROM wishlist ORDER BY created')]
            items = []
            for row in local:
                key = (normalized_name(row['title']),normalized_name(row['author']))
                if key in have:continue
                have.add(key)
                items.append({'id':row['id'],'category':'novel','title':row['title'],'kind':row['kind'],'note':row['note'],
                              'extra':{'author':row['author']} if row['author'] else {},'links':[row['url']] if row['url'] else [],
                              'createdAt':iso(row['created']),'updatedAt':iso(time.time()),'done':False,'doneAt':None,'rating':3,
                              'source':'shuyu','aliases':[],'externalIds':{}})
            with self.lib.db() as db:
                for item in items:self.store(db,item)
                db.execute("INSERT OR REPLACE INTO settings VALUES ('hub_uploaded','1')")
            if items:self.hub.put_wish(items)
            self.match()
        return self.hub_status()

    def unpair(self):
        """Back to this library's own wishlist, as it was before connecting."""
        self.hub.unpair()
        with self.lib.db() as db:
            db.execute('DELETE FROM hub_wishes');db.execute("DELETE FROM settings WHERE key='hub_cursor'")
        return self.hub_status()
