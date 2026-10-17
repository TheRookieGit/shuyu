"""Publication metadata and reversible grouping of duplicate source records."""
import calendar
from collections import defaultdict
from datetime import date
import json
import re
import threading
import time
import unicodedata

PUBLICATION_STATES = ['连载','断更','完结']


def six_months_ago(today=None):
    today = today or date.today()
    month = today.year*12+today.month-1-6
    year, month = divmod(month,12)
    return date(year,month+1,min(today.day,calendar.monthrange(year,month+1)[1]))


def publication_status(state, last_update='', today=None):
    if state == '完结':return state
    if state in {'连载','断更'} and last_update:
        return '断更' if date.fromisoformat(last_update)<=six_months_ago(today) else '连载'
    return state if state=='连载' else ''


def infer_publication(name, text=''):
    # Never infer the author's update date from a local file/archive timestamp.
    from book_classification import introduction
    header=introduction(text)
    metadata=name+'\n'+header
    complete=re.search(r'(?<!未)(?:已完结|全文完结|正文完结|状态[：: ]*完结|进度[：: ]*完结)|[【\[(（]完结[】\])）]|完结(?=\.[^.]+$)',metadata)
    ending=re.search(r'(?im)^\s*(?:全文完(?:结)?|正文完(?:结)?|the end)[。.!！\s]*$',text[-6000:])
    serial=re.search(r'连载(?:中)?|未完结|尚未完结',metadata)
    # Prefer an explicit book status over a loose "完结" inside its title.
    state='完结' if complete or ending else '连载' if serial else ''
    match=re.search(r'(?:最后更新(?:时间)?|最新更新(?:时间)?|更新时间)[：:\s]*(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})',metadata)
    last=''
    if match:
        try:
            value=date(*map(int,match.groups()))
            if value<=date.today():last=value.isoformat()
        except ValueError:pass
    return {'state':state,'last_update':last,'evidence':'文件名/正文的明确完结或连载标记' if state else ''}


def normalized_name(value):
    value=unicodedata.normalize('NFKC',value).casefold()
    value=re.sub(r'^(?:\((?:1v\d+|nph?|高h|gb|bg|bl|gl|abo)\)\s*)+','',value)
    value=re.sub(r'[\[(（【](?:已?完结|连载中?|未完结|正文完|副本|复制|\d+)[\])）】]','',value)
    value=re.sub(r'(?:副本|复制|已完结|连载中|未完结)$','',value)
    return re.sub(r'[\W_]+','',value)


class CatalogOrganizer:
    def __init__(self,lib):
        self.lib=lib;self.lock=threading.RLock();self.cache={}
        with lib.db() as db:db.executescript('''
          CREATE TABLE IF NOT EXISTS book_duplicates (
            book_id TEXT PRIMARY KEY,canonical_id TEXT NOT NULL,root TEXT NOT NULL,reason TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS duplicate_canonical ON book_duplicates(canonical_id);
          CREATE INDEX IF NOT EXISTS duplicate_root ON book_duplicates(root);
          CREATE TABLE IF NOT EXISTS book_publication (
            book_id TEXT PRIMARY KEY,state TEXT NOT NULL,last_update TEXT NOT NULL,
            evidence TEXT NOT NULL,source_url TEXT NOT NULL,reviewed INTEGER NOT NULL,
            source_signature TEXT NOT NULL,updated REAL NOT NULL);
        ''')

    @staticmethod
    def visible_sql(alias='books'):
        return f'NOT EXISTS (SELECT 1 FROM book_duplicates d WHERE d.book_id={alias}.id)'

    @staticmethod
    def status_sql(alias='p'):
        return f"CASE WHEN {alias}.state='完结' THEN '完结' WHEN {alias}.state IN ('连载','断更') AND {alias}.last_update<>'' THEN CASE WHEN {alias}.last_update<=? THEN '断更' ELSE '连载' END WHEN {alias}.state='连载' THEN '连载' ELSE '' END"

    def canonical(self,uid):
        with self.lib.db() as db:
            row=db.execute('SELECT canonical_id FROM book_duplicates WHERE book_id=?',(uid,)).fetchone()
        return row[0] if row else uid

    def ids(self,uid):
        canonical=self.canonical(uid)
        with self.lib.db() as db:
            return [r[0] for r in db.execute('''SELECT id FROM books WHERE id=? AND root=? AND missing=0
              UNION ALL SELECT b.id FROM book_duplicates d CROSS JOIN books b ON b.id=d.book_id
              WHERE d.canonical_id=? AND b.root=? AND b.missing=0''',(canonical,self.lib.root,canonical,self.lib.root))]

    def copies(self,uid):
        ids=self.ids(uid)
        if not ids:return []
        with self.lib.db() as db:
            rows=db.execute(f"SELECT b.id,b.name,b.ext,b.rel,b.size,m.word_count,(SELECT count(*) FROM book_versions v WHERE v.book_id=b.id) cleaned_versions FROM books b LEFT JOIN book_text_metrics m ON m.book_id=b.id AND m.version_id='' WHERE b.id IN ({','.join('?' for _ in ids)}) ORDER BY coalesce(m.word_count,-1) DESC,b.size DESC,b.id",ids).fetchall()
        return [dict(r) for r in rows]

    def publication(self,uid):
        ids=self.ids(uid)
        with self.lib.db() as db:
            rows=db.execute(f"SELECT * FROM book_publication WHERE book_id IN ({','.join('?' for _ in ids)}) ORDER BY reviewed DESC,CASE WHEN state='完结' THEN 0 ELSE 1 END,last_update DESC,updated DESC",ids).fetchall() if ids else []
        if not rows:return {'state':'','status':'','last_update':'','evidence':'','source_url':'','reviewed':False}
        result=dict(rows[0]);result['status']=publication_status(result['state'],result['last_update']);result['reviewed']=bool(result['reviewed'])
        return result

    def save_publication(self,data):
        self.lib.book(data['id'])
        state=str(data.get('state',''));last=str(data.get('last_update','')).strip()
        if state not in ['',*PUBLICATION_STATES]:raise ValueError('无效的连载状态。')
        if last:
            try:value=date.fromisoformat(last)
            except ValueError:raise ValueError('最后更新时间应为 YYYY-MM-DD。')
            if value>date.today():raise ValueError('作者更新时间不能晚于今天。')
            last=value.isoformat()
        if state=='断更' and (not last or date.fromisoformat(last)>six_months_ago()):
            raise ValueError('断更需要作者最后更新时间，且距今至少六个月。')
        url=str(data.get('source_url','')).strip()
        if url and not re.match(r'^https?://[^\s]+$',url):raise ValueError('来源网址应以 http:// 或 https:// 开头。')
        with self.lib.db() as db:
            for uid in self.ids(data['id']):
                db.execute('INSERT OR REPLACE INTO book_publication VALUES (?,?,?,?,?,?,?,?)',(uid,state,last,'人工核验',url,1,'',time.time()))
        self.cache.clear()
        return self.publication(data['id'])

    def record_publication(self,db,book,text='',value=None):
        value=value if value is not None else infer_publication(book['name'],text)
        if not value['state']:return
        db.execute('''INSERT INTO book_publication VALUES (?,?,?,?,?,?,?,?)
          ON CONFLICT(book_id) DO UPDATE SET state=excluded.state,last_update=excluded.last_update,
          evidence=excluded.evidence,source_signature=excluded.source_signature,updated=excluded.updated
          WHERE book_publication.reviewed=0''',
          (book['id'],value['state'],value['last_update'],value['evidence'],'',0,self.lib.classifier.signature(book),time.time()))

    def refresh(self):
        """No source I/O: use existing valid content hashes and trusted title/author pairs."""
        with self.lock:
            root=self.lib.root
            with self.lib.db() as db:
                rows=[dict(r) for r in db.execute("SELECT b.id,b.name,b.title,b.author,b.kind,b.size,b.mtime,m.content_hash,m.source_signature,m.word_count,c.reviewed FROM books b LEFT JOIN book_text_metrics m ON m.book_id=b.id AND m.version_id='' LEFT JOIN book_classification c ON c.book_id=b.id WHERE b.root=? AND b.missing=0 AND b.kind<>'archive'",(root,))]
            parent={r['id']:r['id'] for r in rows};reasons={};hashes={};names={}
            def find(uid):
                while parent[uid]!=uid:parent[uid]=parent[parent[uid]];uid=parent[uid]
                return uid
            def union(a,b,reason):
                a,b=find(a),find(b)
                if a!=b:parent[b]=a
                reasons[reason]=reasons.get(reason,0)+1
            for row in rows:
                uid=row['id']
                if row['kind']=='comic':continue
                valid=row['source_signature']==self.lib.classifier.signature(row)
                digest=row['content_hash'] if valid and row['word_count'] else ''
                if digest:
                    if digest in hashes:union(uid,hashes[digest],'全文相同')
                    else:hashes[digest]=uid
                title=normalized_name(row['title']);author=normalized_name(row['author'])
                if len(title)>=2 and author and author not in {'未知','佚名','未知作者','作者不详'}:
                    key=(title,author)
                    if key in names:union(uid,names[key],'同书名同作者')
                    else:names[key]=uid
            groups=defaultdict(list)
            for row in rows:groups[find(row['id'])].append(row)
            changes=[]
            for group in groups.values():
                if len(group)<2:continue
                canonical=max(group,key=lambda r:(bool(r['reviewed']),r['word_count'] if r['word_count'] is not None and r['source_signature']==self.lib.classifier.signature(r) else -1,r['size'],r['id']))['id']
                for row in group:
                    if row['id']!=canonical:changes.append((row['id'],canonical,root,'全文指纹或同书名同作者'))
            with self.lib.db() as db:
                db.execute('DELETE FROM book_duplicates WHERE root=?',(root,))
                db.executemany('INSERT OR REPLACE INTO book_duplicates VALUES (?,?,?,?)',changes)
                known={r['book_id']:dict(r) for r in db.execute('SELECT book_id,reviewed,source_signature FROM book_publication')}
                for row in rows:
                    previous=known.get(row['id'])
                    if previous and previous['reviewed']:continue
                    if not previous or previous['source_signature']!=self.lib.classifier.signature(row):
                        db.execute('DELETE FROM book_publication WHERE book_id=? AND reviewed=0',(row['id'],))
                        self.record_publication(db,row)
            self.cache.clear()
            return {'sources':len(rows),'books':len(groups),'merged':len(changes)}

    def facets(self):
        key=(self.lib.root,date.today().isoformat())
        if key in self.cache:return self.cache[key]
        visible=self.visible_sql('b');root=self.lib.root
        result={k:{} for k in ['kind','level','relationship','pairing','tag','publication']}
        with self.lib.db() as db:
            base=f" FROM books b LEFT JOIN book_classification c ON c.book_id=b.id WHERE b.root=? AND b.missing=0 AND b.kind<>'archive' AND {visible}"
            for field in ['kind','level','relationship','pairing']:
                if field=='kind':continue
                column='b.kind' if field=='kind' else 'c.'+field
                result[field]={r[0]:r[1] for r in db.execute(f'SELECT {column},count(*)'+base+f' GROUP BY {column}',(root,)) if r[0]}
            result['kind']={r[0]:r[1] for r in db.execute("SELECT b.kind,count(DISTINCT coalesce(d.canonical_id,b.id)) FROM books b LEFT JOIN book_duplicates d ON d.book_id=b.id WHERE b.root=? AND b.missing=0 AND b.kind<>'archive' GROUP BY b.kind",(root,))}
            result['tag']={r[0]:r[1] for r in db.execute('SELECT j.value,count(*) FROM books b JOIN book_classification c ON c.book_id=b.id JOIN json_each(c.tags) j WHERE b.root=? AND b.missing=0 AND '+visible+' GROUP BY j.value',(root,))}
        # One SQL projection avoids a per-book query for the large real library.
        with self.lib.db() as db:
            sql=self.publication_projection()
            result['publication']={r[0]:r[1] for r in db.execute('SELECT pub_status,count(*) FROM ('+sql+") WHERE pub_status<>'' GROUP BY pub_status",(six_months_ago().isoformat(),root))}
        self.cache[key]=result
        return result

    def publication_projection(self):
        return f'''SELECT b.id,{self.status_sql('p')} pub_status FROM books b
          LEFT JOIN (SELECT CAST(coalesce(d.canonical_id,bp.book_id) AS TEXT) id,bp.state,bp.last_update,
          row_number() OVER (PARTITION BY coalesce(d.canonical_id,bp.book_id)
          ORDER BY bp.reviewed DESC,CASE WHEN bp.state='完结' THEN 0 ELSE 1 END,bp.last_update DESC,bp.updated DESC) priority
          FROM book_publication bp LEFT JOIN book_duplicates d ON d.book_id=bp.book_id) p ON p.id=b.id AND p.priority=1
          WHERE b.root=? AND b.missing=0 AND {self.visible_sql('b')}'''
