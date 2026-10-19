"""Books to read that are not downloaded yet, and a download address for any book."""
from pathlib import PurePosixPath
import re
import time
import uuid

from catalog_organizer import normalized_name

KINDS = {'novel':'小说文件','folder':'文件夹','zip':'ZIP 压缩包'}


def clean_url(value):
    url = str(value or '').strip()
    if len(url)>2048 or (url and not re.match(r'^https?://\S+$',url,re.I)):
        raise ValueError('下载地址应以 http:// 或 https:// 开头。')
    return url


def text(value,limit):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',str(value or '')).strip()[:limit]


class Wishlist:
    def __init__(self,lib):
        self.lib = lib
        with lib.db() as db:db.executescript('''
          CREATE TABLE IF NOT EXISTS wishlist (
            id TEXT PRIMARY KEY,root TEXT NOT NULL,title TEXT NOT NULL,author TEXT NOT NULL,kind TEXT NOT NULL,
            url TEXT NOT NULL,note TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,book_id TEXT NOT NULL DEFAULT '');
          CREATE INDEX IF NOT EXISTS wishlist_root ON wishlist(root,book_id);
          CREATE TABLE IF NOT EXISTS book_links (book_id TEXT PRIMARY KEY,url TEXT NOT NULL,updated REAL NOT NULL);
        ''')

    def items(self,q=''):
        with self.lib.db() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM wishlist WHERE root=? ORDER BY book_id<>'',created DESC",(self.lib.root,))]
        words = [w.casefold() for w in str(q).split()]
        rows = [r for r in rows if all(w in (r['title']+' '+r['author']+' '+r['note']).casefold() for w in words)]
        for row in rows:row['downloaded'] = bool(row['book_id'])
        return {'items':rows,'pending':sum(not r['downloaded'] for r in rows),'kinds':KINDS}

    def save(self,data):
        title = text(data.get('title'),200)
        if not title:raise ValueError('请填写书名或文件夹名。')
        kind = str(data.get('kind','novel'))
        if kind not in KINDS:raise ValueError('请选择小说文件、文件夹或 ZIP 压缩包。')
        values = (title,text(data.get('author'),100),kind,clean_url(data.get('url')),text(data.get('note'),1000))
        uid = str(data.get('id') or '')
        now = time.time()
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
        with self.lib.db() as db:
            row = db.execute('SELECT * FROM wishlist WHERE id=? AND root=?',(uid,self.lib.root)).fetchone()
        if not row:raise ValueError('没有找到这条想看记录。')
        return dict(row)|{'downloaded':bool(row['book_id'])}

    def delete(self,uid):
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
        """Mark wishes as downloaded once a book with the same name appears on the shelf."""
        root = self.lib.root
        with self.lib.db() as db:
            wishes = [dict(r) for r in db.execute("SELECT * FROM wishlist WHERE root=? AND book_id=''",(root,))]
        matched = 0
        for wish in wishes:
            key = normalized_name(wish['title'])
            if len(key)<2:continue
            pattern = '%'+wish['title'].replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            with self.lib.db() as db:
                rows = [dict(r) for r in db.execute("SELECT id,title,author,kind,rel,container FROM books WHERE root=? AND missing=0 AND kind<>'archive' AND (title LIKE ? ESCAPE '\\' OR rel LIKE ? ESCAPE '\\') LIMIT 500",(root,pattern,pattern))]
            author = normalized_name(wish['author'])
            found = next((r for r in rows if key in self.keys(r) and not (author and r['author'] and normalized_name(r['author'])!=author)),None)
            if not found:continue
            book_id = self.lib.organizer.canonical(found['id'])
            with self.lib.db() as db:
                db.execute('UPDATE wishlist SET book_id=?,updated=? WHERE id=?',(book_id,time.time(),wish['id']))
            if wish['url'] and not self.link(book_id):self.set_link(book_id,wish['url'])
            matched += 1
        return matched
