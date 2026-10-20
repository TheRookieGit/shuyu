"""Novels uploaded from Shuyu Lite, saved into the library as ordinary files so versions stay with the book."""
from __future__ import annotations

from pathlib import Path
import re
import uuid

from ai_cleaner import MAX_SOURCE, SUPPORTED, CleanError, Document
from book_names import detect

# Inside the library root, so the full page lists uploads like any other book. Automatic cleanup skips this folder.
UPLOAD_FOLDER = 'Lite上传'
MARK = '【lite上传】'


def clean_name(value,limit):
    # Brackets would confuse title/author parsing of the saved file name; the rest is what Windows forbids.
    return re.sub(r'\s+',' ',re.sub(r'[<>:"/\\|?*\x00-\x1f《》【】\[\]]','',str(value))).strip(' .')[:limit]


def file_stem(title,author):
    return f'《{title}》'+(f'作者：{author}' if author else '')+MARK


class Uploads:
    def __init__(self,lib):
        self.lib = lib

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
        stem,number = file_stem(title,author),1
        while True:
            target = folder/(stem+(f'（{number}）' if number>1 else '')+ext)
            try:
                with target.open('xb') as stream:stream.write(raw)
                break
            except FileExistsError:number += 1
        return self.lib.book(self.lib.register(target))
