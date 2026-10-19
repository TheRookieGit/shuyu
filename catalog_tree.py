"""How many books each folder and archive of the library holds, as a lazily browsed tree."""
import json
from pathlib import PurePosixPath
import threading

from archive_engine import ARCHIVES

BOOK_LIMIT = 200


def segments(book):
    """Folders, archives and folders inside archives leading to the book; the last item is the book itself."""
    parts = [p for p in (book['container'] or book['rel'].split('::')[0]).split('/') if p]
    for member in json.loads(book['chain'] or '[]'):
        parts.extend(p for p in member.replace('\\','/').split('/') if p)
    return parts


class CatalogTree:
    def __init__(self,lib):
        self.lib = lib
        self.lock = threading.Lock()
        self.cache = None

    def invalidate(self):
        self.cache = None

    def _build(self):
        root = self.lib.root
        with self.lib.db() as db:
            rows = db.execute("SELECT id,rel,container,chain,kind FROM books WHERE root=? AND missing=0 AND kind<>'archive'",(root,)).fetchall()
            canonical = dict(db.execute('SELECT book_id,canonical_id FROM book_duplicates WHERE root=?',(root,)).fetchall())
        tree = {'children':{},'books':[],'ids':[],'comics':0}
        for row in rows:
            parts = segments(row)
            node = tree
            for depth,name in enumerate(parts):
                node['ids'].append(row['id'])
                node['comics'] += row['kind']=='comic'
                if depth==len(parts)-1:
                    node['books'].append(row['id'])
                    break
                kind = 'archive' if PurePosixPath(name).suffix.lower() in ARCHIVES else 'folder'
                node = node['children'].setdefault(name,{'kind':kind,'children':{},'books':[],'ids':[],'comics':0})
        return {'root':root,'tree':tree,'canonical':canonical}

    def _data(self):
        with self.lock:
            if not self.cache or self.cache['root']!=self.lib.root:
                self.cache = self._build()
            return self.cache

    @staticmethod
    def _counts(node,canonical):
        total = len(node['ids'])
        return {'total':total,'novels':total-node['comics'],'comics':node['comics'],
                'unique':len({canonical.get(uid,uid) for uid in node['ids']})}

    def node(self,path):
        if not isinstance(path,list) or not all(isinstance(p,str) for p in path):
            raise ValueError('无效的文件夹路径。')
        data = self._data()
        node = data['tree']
        for name in path:
            node = node['children'].get(name)
            if node is None:raise ValueError('这个文件夹已不在书库中，请重新扫描。')
        children = [{'name':name,'kind':child['kind'],'has_children':bool(child['children'] or child['books']),
                     **self._counts(child,data['canonical'])} for name,child in node['children'].items()]
        children.sort(key=lambda c:(-c['total'],c['name']))
        direct = node['books'][:BOOK_LIMIT]
        books = []
        if direct:
            with self.lib.db() as db:
                found = {r['id']:dict(r) for r in db.execute(f"SELECT id,title,author,kind,ext,name FROM books WHERE id IN ({','.join('?' for _ in direct)})",direct)}
            books = [found[uid]|{'canonical':data['canonical'].get(uid,uid)} for uid in direct if uid in found]
            books.sort(key=lambda b:b['name'])
        return {'path':path,'root':data['root'],'counts':self._counts(node,data['canonical']),'children':children,
                'books':books,'direct_total':len(node['books'])}
