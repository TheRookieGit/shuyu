"""Claude cleanup of novels added to the library after it was switched on. Originals are never written."""
import json
import threading
import time

from ai_cleaner import CATEGORIES, SUPPORTED, CleanError
from lite_upload import UPLOAD_FOLDER

OPTIONS = {'mode':'focused','categories':sorted(CATEGORIES),'encoding':'auto'}
LABEL = '自动清理版'
STATES = {'queued':'等待清理','running':'正在清理','saved':'已生成清理版','review':'有建议待确认','clean':'无需清理','failed':'清理失败'}


class AutoCleaner:
    def __init__(self,lib):
        self.lib = lib
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.running = False
        self.blocked = ''
        with lib.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS auto_clean (
              book_id TEXT PRIMARY KEY,root TEXT NOT NULL,status TEXT NOT NULL,job_id TEXT NOT NULL,
              version_id TEXT NOT NULL,message TEXT NOT NULL,updated REAL NOT NULL)''')
            # A task interrupted by a restart starts over; its half-finished job stays reviewable.
            db.execute("UPDATE auto_clean SET status='queued' WHERE status='running'")

    def settings(self):
        with self.lib.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key='auto_clean'").fetchone()
        return {'enabled':True,'since':None}|(json.loads(row[0]) if row else {})

    def _store(self,values):
        with self.lib.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('auto_clean',json.dumps(values)))

    def configure(self,data):
        values = self.settings()
        enabled = bool(data.get('enabled'))
        if enabled and not values['enabled']:
            # Books added while it was off are not cleaned in a surprise batch.
            values['since'] = time.time()
        values['enabled'] = enabled
        self._store(values)
        self.wake.set()
        return self.status()

    def enqueue(self):
        """Queue newly found novels; the first run only records where "new" starts. Lite uploads are processed in Lite."""
        values = self.settings()
        if values['since'] is None:
            values['since'] = time.time()
            self._store(values)
            return 0
        if not values['enabled']:return 0
        extensions = sorted(SUPPORTED)
        with self.lib.db() as db:
            count = db.execute(f'''INSERT OR IGNORE INTO auto_clean
              SELECT b.id,b.root,'queued','','','',? FROM books b
              WHERE b.root=? AND b.missing=0 AND b.added>=? AND b.ext IN ({','.join('?' for _ in extensions)})
              AND {self.lib.organizer.visible_sql('b')} AND b.rel NOT LIKE ?
              AND NOT EXISTS (SELECT 1 FROM book_versions v WHERE v.book_id=b.id)''',
              (time.time(),self.lib.root,values['since'],*extensions,UPLOAD_FOLDER+'/%')).rowcount
        if count:self.start()
        return count

    def start(self):
        with self.lock:
            if not self.thread or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop,daemon=True,name='auto-clean')
                self.thread.start()
        self.wake.set()

    def _loop(self):
        while True:
            while self._step():pass
            # Waiting for Claude settings or a manual cleanup is retried, a missing login less often.
            self.wake.wait(600 if self.blocked else 60)
            self.wake.clear()

    def _next(self):
        with self.lib.db() as db:
            row = db.execute("SELECT book_id FROM auto_clean WHERE root=? AND status='queued' ORDER BY updated,book_id LIMIT 1",(self.lib.root,)).fetchone()
        return row[0] if row else None

    def _record(self,book_id,status,message='',job_id=None,version_id=''):
        with self.lib.db() as db:
            db.execute('UPDATE auto_clean SET status=?,message=?,job_id=coalesce(?,job_id),version_id=?,updated=? WHERE book_id=?',
                       (status,message,job_id,version_id,time.time(),book_id))

    def _step(self):
        if not self.settings()['enabled'] or self.lib.cleaner.active:return False
        book_id = self._next()
        if not book_id:return False
        if not self.lib.cleaner.config()['can_start']:
            self.blocked = '请先在「Claude AI 设置」中填写 API Key 或登录本机 Claude Code。'
            return False
        self.running = True
        try:
            return self._process(book_id)
        finally:
            self.running = False

    def _process(self,book_id):
        cleaner = self.lib.cleaner
        self._record(book_id,'running')
        try:
            job = cleaner.prepare({'id':book_id,'version_id':'',**OPTIONS})
        except Exception as exc:
            self._record(book_id,'failed',str(exc) if isinstance(exc,(CleanError,ValueError,OSError)) else '无法读取这本小说。')
            return True
        if not job['chunks']:
            self._record(book_id,'clean','没有发现作者话、乱码或屏蔽字标记，未生成新版本。',job['id'])
            return True
        try:
            cleaner.start(job['id'])
        except CleanError as exc:
            # A manual cleanup took the slot, or the Claude login expired: try this book again later.
            self._record(book_id,'queued','',job['id'])
            self.blocked = '' if cleaner.active else str(exc)
            return False
        self.blocked = ''
        while (result := cleaner.public(job['id']))['status']=='running':time.sleep(.5)
        if result['status']!='ready':
            self._record(book_id,'failed',result['error'] or '清理没有完成。',job['id'])
            return True
        selected = result['defaults']
        others = result['patch_total']-len(selected)
        pending = f'另有 {others} 项乱码或屏蔽字建议需要人工确认。' if others else ''
        if not selected:
            self._record(book_id,'review' if others else 'clean',pending or 'Claude 没有提出修改建议，未生成新版本。',job['id'])
            return True
        try:
            version = cleaner.save({'job_id':job['id'],'selected':selected,'label':LABEL})
        except Exception as exc:
            self._record(book_id,'failed',str(exc) if isinstance(exc,(CleanError,ValueError,OSError)) else '保存清理版失败。',job['id'])
            return True
        self._record(book_id,'saved',f'已自动采用 {len(selected)} 项高置信度的作者话 / 广告删除。'+pending,job['id'],version['id'])
        return True

    def retry(self):
        with self.lib.db() as db:
            count = db.execute("UPDATE auto_clean SET status='queued',message='',updated=? WHERE root=? AND status='failed'",(time.time(),self.lib.root)).rowcount
        self.blocked = ''
        if count:self.start()
        return self.status()

    def record(self,book_id):
        with self.lib.db() as db:
            row = db.execute('SELECT status,job_id,version_id,message,updated FROM auto_clean WHERE book_id=?',(book_id,)).fetchone()
        return dict(row)|{'label':STATES.get(row['status'],row['status'])} if row else None

    def status(self):
        values = self.settings()
        with self.lib.db() as db:
            counts = {r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM auto_clean WHERE root=? GROUP BY status',(self.lib.root,))}
            recent = [dict(r)|{'label':STATES.get(r['status'],r['status'])} for r in db.execute('''SELECT a.book_id,a.status,a.message,a.updated,b.title
              FROM auto_clean a JOIN books b ON b.id=a.book_id WHERE a.root=? ORDER BY a.updated DESC LIMIT 8''',(self.lib.root,))]
        return {'enabled':values['enabled'],'since':values['since'],'counts':counts,'recent':recent,
                'blocked':self.blocked if counts.get('queued') else '','options':OPTIONS,'label':LABEL}
