"""Novels uploaded from Shuyu Lite, saved into the library as ordinary files so versions stay with the book."""
from __future__ import annotations

import io
from pathlib import Path
import re
import shutil
import subprocess
import uuid
import zipfile

from ai_cleaner import MAX_SOURCE, SUPPORTED, CleanError, Document
from archive_engine import ARCHIVES, ArchiveEngine, ArchiveError
from book_names import detect

# Inside the library root, so the full page lists uploads like any other book. Automatic cleanup skips this folder.
UPLOAD_FOLDER = 'Lite上传'
MARK = '【lite上传】'
MAX_ARCHIVE = 512*1024*1024
# Cleaned versions of an upload sit beside it, and so do uploaded archives whose novels were already saved one by one.
# Scans skip both, so neither shows up as books of their own.
VERSION_FILE = re.compile(r'【AI清理版\d+】(?:（\d+）)?\.[^.]+$')


def clean_name(value,limit):
    # Brackets would confuse title/author parsing of the saved file name; the rest is what Windows forbids.
    return re.sub(r'\s+',' ',re.sub(r'[<>:"/\\|?*\x00-\x1f《》【】\[\]]','',str(value))).strip(' .')[:limit]


def file_stem(title,author,mark=MARK):
    return f'《{title}》'+(f'作者：{author}' if author else '')+mark


def is_version_file(path,root):
    return bool(VERSION_FILE.search(path.name)) and path.parent==Path(root)/UPLOAD_FOLDER


def skipped_by_scan(path,root):
    return path.parent==Path(root)/UPLOAD_FOLDER and (bool(VERSION_FILE.search(path.name)) or path.suffix.lower() in ARCHIVES)


def free_path(folder,stem,ext):
    number = 1
    while (target := folder/(stem+(f'（{number}）' if number>1 else '')+ext)).exists():number += 1
    return target


class Uploads:
    def __init__(self,lib):
        self.lib = lib

    def owns(self,book,path=None):
        """Whether the book is a Lite upload; with a path, whether that is one of its version files."""
        if book['container'] or not book['rel'].startswith(UPLOAD_FOLDER+'/'):return False
        return path is None or is_version_file(Path(path),Path(book['root']).resolve())

    def version_target(self,book,number,ext):
        title,author = clean_name(book['title'],60) or '小说',clean_name(book['author'],30)
        return free_path(Path(book['root']).resolve()/UPLOAD_FOLDER,file_stem(title,author,f'【AI清理版{number}】'),ext)

    def move_versions(self):
        """Versions saved before uploads kept them beside the file move there once the library can be reached."""
        with self.lib.db() as db:
            rows = [dict(r) for r in db.execute(f'''SELECT v.id,v.number,v.path,b.id book_id FROM book_versions v JOIN books b ON b.id=v.book_id
                WHERE b.rel LIKE ? AND b.container='' ''',(UPLOAD_FOLDER+'/%',))]
        for row in rows:
            source = self.lib.data_dir/row['path']
            if Path(row['path']).is_absolute() or not source.is_file():continue
            try:
                book = self.lib.book(row['book_id'])
                if not (Path(book['root'])/UPLOAD_FOLDER).is_dir():continue
                target = self.version_target(book,row['number'],source.suffix)
                shutil.copy2(source,target)
                with self.lib.db() as db:db.execute('UPDATE book_versions SET path=? WHERE id=?',(str(target),row['id']))
                source.unlink()
            except Exception:
                continue

    def save(self,raw,name,title=None,author=None):
        return self.receive(io.BytesIO(raw),len(raw),name,title,author)

    def receive(self,stream,length,name,title=None,author=None):
        """Store one upload: a novel, or an archive whose novels are each saved beside it.
        title/author None: read from the file name or the book itself. Given values are the reader's edits."""
        ext = Path(str(name)).suffix.lower()
        if ext not in SUPPORTED|ARCHIVES:raise CleanError('只能上传 TXT、MD、EPUB 小说或压缩包。')
        limit = MAX_ARCHIVE if ext in ARCHIVES else MAX_SOURCE
        if not 0<length<=limit:raise CleanError(f'只能上传 {limit//1024//1024} MB 以内的{"压缩包" if ext in ARCHIVES else "小说"}。')
        root = Path(self.lib.root)
        if not root.is_dir():raise CleanError('Windows 电脑上的书库暂时无法访问，请确认磁盘已连接。')
        work = self.lib.data_dir/'uploads'/uuid.uuid4().hex;work.mkdir(parents=True)
        try:
            temporary = work/('upload'+ext)
            with temporary.open('wb') as target:
                remaining = length
                while remaining:
                    chunk = stream.read(min(remaining,1024*1024))
                    if not chunk:raise CleanError('上传没有完成，请重试。')
                    target.write(chunk);remaining -= len(chunk)
            if ext in ARCHIVES:return self._archive(temporary,work,str(name))
            uid, = self.lib.register(self._novel(temporary,str(name),title,author))
            return self.lib.book(uid)
        finally:
            shutil.rmtree(work,ignore_errors=True)

    def _novel(self,path,name,title=None,author=None):
        # Check that it opens as a novel before anything is written into the library.
        text = Document(path).text;raw = path.read_bytes();ext = Path(name).suffix.lower()
        found_title,found_author = detect(name,raw,text)
        title = clean_name(found_title if title is None else title,60)
        author = clean_name(found_author if author is None else author,30)
        if not title:raise CleanError('请填写书名。')
        folder = Path(self.lib.root)/UPLOAD_FOLDER;folder.mkdir(exist_ok=True)
        target = free_path(folder,file_stem(title,author),ext)
        with target.open('xb') as stream:stream.write(raw)
        return target

    def _archive(self,path,work,name):
        """Unpack every novel inside, nested archives included; the archive itself is kept beside them."""
        # A private engine, so its unpacked copies are removed with this upload and never mix with the library's.
        engine = ArchiveEngine(work/'engine',self.lib.engine.passwords)
        engine.tool,engine.setup_error,engine._tool_checked = self.lib.engine.ensure_tool(),self.lib.engine.setup_error,True
        try:leaves,issues = engine.catalog(path)
        except (ArchiveError,subprocess.TimeoutExpired,zipfile.BadZipFile) as exc:
            raise CleanError(str(exc) if isinstance(exc,ArchiveError) else '压缩包无法读取。')
        novels = [leaf for leaf in leaves if leaf['ext'] in SUPPORTED]
        if not novels:
            raise CleanError('压缩包里没有找到 TXT、MD 或 EPUB 小说。'+(issues[0]['message'] if issues else ''))
        saved,failed = [],[]
        for leaf in novels:
            inner = leaf['name'].replace('\\','/').rsplit('/',1)[-1]
            try:saved.append(self._novel(engine.materialize(path,leaf['chain'],inner),inner))
            except (CleanError,ArchiveError,subprocess.TimeoutExpired,OSError) as exc:
                failed.append(f'{inner}：{exc}' if isinstance(exc,(CleanError,ArchiveError)) else f'{inner}：无法取出。')
        if not saved:raise CleanError('压缩包里的小说都没能导入：'+'；'.join(failed[:3]))
        folder = Path(self.lib.root)/UPLOAD_FOLDER
        stem = clean_name(Path(name).stem,80) or '压缩包'
        archive = free_path(folder,stem+MARK,Path(name).suffix.lower())
        shutil.copyfile(path,archive)
        books = [self.lib.book(uid) for uid in self.lib.register(*saved)]
        return {'archive':archive.name,'books':books,'failed':failed+[f'{i["name"]}：{i["message"]}' for i in issues][:20],
                'other_files':len(leaves)-len(novels)}
