"""Novels uploaded from Shuyu Lite, saved into the library as ordinary files so versions stay with the book."""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import uuid

from ai_cleaner import MAX_SOURCE, SUPPORTED, CleanError, Document
from book_names import detect

# Inside the library root, so the full page lists uploads like any other book. Automatic cleanup skips this folder.
UPLOAD_FOLDER = 'Lite上传'
MARK = '【lite上传】'
# Cleaned versions of an upload sit beside it; scans skip them, so they never show up as books of their own.
VERSION_FILE = re.compile(r'【AI清理版\d+】(?:（\d+）)?\.[^.]+$')


def clean_name(value,limit):
    # Brackets would confuse title/author parsing of the saved file name; the rest is what Windows forbids.
    return re.sub(r'\s+',' ',re.sub(r'[<>:"/\\|?*\x00-\x1f《》【】\[\]]','',str(value))).strip(' .')[:limit]


def file_stem(title,author,mark=MARK):
    return f'《{title}》'+(f'作者：{author}' if author else '')+mark


def is_version_file(path,root):
    return bool(VERSION_FILE.search(path.name)) and path.parent==Path(root)/UPLOAD_FOLDER


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
        """title/author None: read from the file name or the book itself. Given values are the reader's edits."""
        ext = Path(str(name)).suffix.lower()
        if ext not in SUPPORTED:raise CleanError('只能上传 TXT、MD 或 EPUB 小说。')
        if not raw or len(raw)>MAX_SOURCE:raise CleanError('只能上传 64 MB 以内的文件。')
        root = Path(self.lib.root)
        if not root.is_dir():raise CleanError('Windows 电脑上的书库暂时无法访问，请确认磁盘已连接。')
        # Check that it opens as a novel before anything is written into the library.
        temporary = self.lib.data_dir/'uploads'/(uuid.uuid4().hex+ext)
        temporary.parent.mkdir(parents=True,exist_ok=True)
        try:
            temporary.write_bytes(raw);text = Document(temporary).text
        finally:
            temporary.unlink(missing_ok=True)
        found_title,found_author = detect(str(name),raw,text)
        title = clean_name(found_title if title is None else title,60)
        author = clean_name(found_author if author is None else author,30)
        if not title:raise CleanError('请填写书名。')
        folder = root/UPLOAD_FOLDER;folder.mkdir(exist_ok=True)
        target = free_path(folder,file_stem(title,author),ext)
        with target.open('xb') as stream:stream.write(raw)
        return self.lib.book(self.lib.register(target))
