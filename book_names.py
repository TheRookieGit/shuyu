"""Reading a novel's title and author from its file name, its first lines or its EPUB metadata."""
from __future__ import annotations

import io
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET

TITLE_LINE = re.compile(r'^\s*(?:书名|标题|作品名称?|小说名称?)\s*[：:]\s*《?(.+?)》?\s*$')
BOOK_LINE = re.compile(r'^\s*《(.+?)》')
AUTHOR_LINE = re.compile(r'作者\s*[：:]\s*([^\s（(\[【]+)')


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


def from_text(text):
    """书名：/《书名》 and 作者： in the opening lines, where downloaded novels usually state them."""
    title = author = ''
    for line in [line for line in text[:3000].splitlines() if line.strip()][:30]:
        if not title and (match := TITLE_LINE.match(line) or BOOK_LINE.match(line)):title = match.group(1).strip()
        if not author and (match := AUTHOR_LINE.search(line)):author = match.group(1).strip()
    return title,author


def from_epub(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            container = ET.fromstring(zf.read('META-INF/container.xml'))
            opf = ET.fromstring(zf.read(next(n.attrib['full-path'] for n in container.iter() if n.tag.endswith('rootfile'))))
    except (zipfile.BadZipFile,KeyError,StopIteration,ET.ParseError):
        return '',''
    def first(tag):
        return next((n.text.strip() for n in opf.iter() if n.tag.rsplit('}',1)[-1]==tag and n.text and n.text.strip()),'')
    return first('title'),first('creator')


def detect(name,raw,text):
    """A file name that names the book wins; otherwise the book's own metadata, then the bare file name.
    lite/lite.js previews the same rules before uploading."""
    named_title,named_author = metadata(name)
    inner_title,inner_author = from_epub(raw) if Path(name).suffix.lower() in {'.epub','.eqpub'} else from_text(text)
    explicit = re.search(r'《.+?》',Path(name).stem)
    return (named_title if explicit else inner_title or named_title),(named_author or inner_author)
