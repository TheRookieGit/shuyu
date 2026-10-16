"""Read archive inventories and materialize individual members in a local cache."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import urllib.request
import zipfile

ARCHIVES = {'.zip', '.rar', '.7z', '.cbz', '.cbr', '.tar', '.gz', '.bz2', '.xz'}
IMAGES = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif', '.bmp'}
BOOKS = {'.txt', '.md', '.text', '.epub', '.eqpub', '.pdf', '.docx', '.doc', '.mobi', '.azw3', '.azw', '.rtf', '.html', '.htm', '.fb2'}
VERSION = 'recursive-4'
MAX_BYTES = 8 * 1024**3
MAX_DEPTH = 16
INSTALL_LOCK = threading.Lock()
# Downloads are pinned to the release linked by https://www.7-zip.org/download.html.
RELEASE = 'https://github.com/ip7z/7zip/releases/download/26.04/'
DOWNLOADS = {
    '7zr.exe': '256feca8e274e5da655e2a284fabafd9f554365eb164862089dacd4e8276d282',
    '7z2604-x64.exe': 'd54bf805f9f3704d1e8db2fa3498ae7ef2df0312b40b558e7c71c734430a665d',
}


class ArchiveError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def decode(data):
    for enc in ('utf-8-sig', 'gb18030', 'big5'):
        try:
            return data.decode(enc)
        except UnicodeError:
            pass
    return data.decode('utf-8', errors='replace')


def zip_label(info):
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode('cp437')
        return decode(raw)
    except UnicodeError:
        return info.filename


def member_key(chain):
    return chain[0] if len(chain)==1 else '@chain:' + json.dumps(chain,ensure_ascii=False,separators=(',',':'))


def source_chain(book):
    if book.get('chain'):
        chain = json.loads(book['chain'])
        if chain:
            return chain
    entry = book.get('entry','')
    if entry.startswith('@chain:'):
        return json.loads(entry[7:])
    return [entry] if entry else []


class ArchiveEngine:
    def __init__(self, data_dir, passwords=()):
        self.data_dir = Path(data_dir).resolve()
        self.passwords = tuple(dict.fromkeys(p for p in passwords if p))
        self.tool = None
        self.setup_error = ''
        self._tool_checked = False

    def ensure_tool(self):
        if self._tool_checked:
            return self.tool
        with INSTALL_LOCK:
            if self._tool_checked:
                return self.tool
            # Prefer the app's pinned portable version over an old system installation.
            location = self.data_dir / 'tools' / '7zip-26.04'
            exe = location / '7z.exe'
            if exe.is_file():
                self.tool = str(exe)
            elif os.name=='nt':
                try:
                    location.mkdir(parents=True,exist_ok=True)
                    for name,digest in DOWNLOADS.items():
                        target = location / name
                        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
                            request = urllib.request.Request(RELEASE+name,headers={'User-Agent':'Shuyu/2.0'})
                            with urllib.request.urlopen(request,timeout=45) as response:
                                payload = response.read(16*1024*1024)
                            if hashlib.sha256(payload).hexdigest()!=digest:
                                raise ArchiveError('engine','解压引擎校验失败，请重试。')
                            target.write_bytes(payload)
                    result = subprocess.run([str(location/'7zr.exe'),'x',str(location/'7z2604-x64.exe'),f'-o{location}','-y'],
                                            capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
                    if result.returncode or not exe.is_file():
                        raise ArchiveError('engine','解压引擎准备失败。')
                    self.tool = str(exe)
                except Exception as exc:
                    self.setup_error = f'无法准备解压引擎：{exc}'
            if not self.tool:
                for candidate in [shutil.which('7z'),shutil.which('7zz'),r'C:\Program Files\7-Zip\7z.exe']:
                    if candidate and Path(candidate).is_file():
                        self.tool = str(candidate)
                        break
            self._tool_checked = True
            return self.tool

    @property
    def revision(self):
        # Password changes invalidate only inventories, never original files.
        digest = hashlib.sha256(json.dumps(self.passwords,ensure_ascii=False).encode()).hexdigest()[:16]
        return VERSION + ':' + digest + ':' + ('7zip' if self.ensure_tool() else 'native')

    @staticmethod
    def failure(message):
        lower = message.lower()
        if any(s in lower for s in ('password','encrypted','wrong password')):
            return ArchiveError('password','压缩包有密码。请在书库设置中填入解压密码，程序会自动重试。')
        if any(s in lower for s in ('unexpected end','headers error','data error','is not archive','can not open','cannot open','crc failed')):
            return ArchiveError('damaged','压缩包不完整或已损坏，自动读取失败。')
        return ArchiveError('format','解压引擎无法读取此文件：'+message.strip()[-250:])

    def command(self, action, path, password=None, member=None):
        tool = self.ensure_tool()
        if not tool:
            raise ArchiveError('engine',self.setup_error or '解压引擎不可用，请连接网络后点击重试。')
        args = [tool,action,'-sccUTF-8','-y','-bd',f'-p{password}' if password else '-p-']
        if action=='l':
            args += ['-slt','-ba']
        else:
            args += ['-so','-spd']
        args += ['--',str(path)]
        if member is not None:
            args.append(member)
        return args

    def entries(self, path):
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                return [{'key':i.filename,'name':zip_label(i),'ext':Path(zip_label(i)).suffix.lower(),'size':i.file_size,
                         'encrypted':bool(i.flag_bits&1)} for i in zf.infolist()
                        if not i.is_dir() and not i.filename.startswith('__MACOSX/')
                        and not Path(i.filename.replace('\\','/')).name.startswith('.')]
        error = None
        for password in (None,*self.passwords):
            result = subprocess.run(self.command('l',path,password),capture_output=True,stdin=subprocess.DEVNULL,
                                    timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            output = decode(result.stdout)
            if result.returncode:
                error = self.failure(decode(result.stderr)+output[-600:])
                if error.code=='password':
                    continue
                raise error
            records = []
            for block in output.replace('\r\n','\n').split('\n\n'):
                values = dict(line.split(' = ',1) for line in block.splitlines() if ' = ' in line)
                name = values.get('Path','')
                if not name or values.get('Folder')=='+' or values.get('Attributes','').startswith('D'):
                    continue
                if Path(name.replace('\\','/')).name.startswith('.') or name.startswith('__MACOSX/'):
                    continue
                records.append({'key':name,'name':name,'size':int(values.get('Size') or 0),
                                'ext':Path(name).suffix.lower(),'encrypted':values.get('Encrypted')=='+'})
            return records
        raise error or ArchiveError('password','请填写压缩包密码。')

    def _copy(self, stream, target, expected=0):
        if expected>MAX_BYTES:
            raise ArchiveError('size','单个文件超过 8 GB 自动处理上限。')
        if expected and expected > shutil.disk_usage(target.parent).free-256*1024**2:
            raise ArchiveError('space','本机空间不足，无法自动取出压缩包内文件。')
        total = 0
        with target.open('wb') as dest:
            while True:
                chunk = stream.read(1024*1024)
                if not chunk:
                    break
                total += len(chunk)
                if total>MAX_BYTES:
                    raise ArchiveError('size','单个文件超过 8 GB 自动处理上限。')
                dest.write(chunk)
        return total

    def extract(self, path, entry, target):
        target = Path(target)
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary = target.with_name(target.name+'.'+os.urandom(6).hex()+'.tmp')
        display = entry
        try:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as zf:
                    try:
                        info = zf.getinfo(entry)
                    except KeyError:
                        raise ArchiveError('missing','压缩包内没有这个文件，请重新扫描。')
                    display = zip_label(info)
                    for password in (None,*self.passwords):
                        try:
                            with zf.open(info,pwd=password.encode('utf-8') if password else None) as stream:
                                self._copy(stream,temporary,info.file_size)
                            temporary.replace(target)
                            return target
                        except (RuntimeError,NotImplementedError,zipfile.BadZipFile):
                            continue
            error = None
            for password in (None,*self.passwords):
                # stdout is streamed to a generated path; archive paths never become disk paths.
                with tempfile_error() as stderr:
                    proc = subprocess.Popen(self.command('x',path,password,display),stdout=subprocess.PIPE,stderr=stderr,
                                            stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    timer = threading.Timer(180,proc.kill)
                    timer.start()
                    try:
                        self._copy(proc.stdout,temporary)
                        code = proc.wait()
                    finally:
                        timer.cancel()
                        proc.stdout.close()
                        if proc.poll() is None:
                            proc.kill()
                        proc.wait()
                    if not code:
                        temporary.replace(target)
                        return target
                    stderr.seek(0)
                    error = self.failure(decode(stderr.read()))
                    if error.code!='password':
                        raise error
            raise error or ArchiveError('password','请填写压缩包密码。')
        finally:
            temporary.unlink(missing_ok=True)

    def materialize(self, outer, chain, final_name=None):
        current = Path(outer)
        stat = current.stat()
        identity = f'{str(current).casefold()}:{stat.st_mtime_ns}:{stat.st_size}'
        for index,key in enumerate(chain):
            identity += '\0'+key
            digest = hashlib.sha256(identity.encode()).hexdigest()
            name = final_name if index==len(chain)-1 and final_name else key.replace('\\','/').rsplit('/',1)[-1]
            ext = Path(name).suffix.lower()
            # Generated directories and conservative names also handle Windows reserved filenames.
            stem = ''.join('_' if c in '<>:"/\\|?*' or ord(c)<32 else c for c in Path(name).stem).strip(' .')[:80] or 'book'
            if stem.upper().split('.')[0] in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
                stem = '_'+stem
            target = self.data_dir/'extracted'/digest/(stem+ext)
            marker = target.parent/'.entry.json'
            if marker.is_file():
                try:
                    known_name = json.loads(marker.read_text(encoding='utf-8'))['file']
                    if Path(known_name).name==known_name:
                        known = target.parent/known_name
                        if known.is_file():
                            current = known
                            continue
                except (OSError,ValueError,KeyError):
                    pass
            if not target.is_file():
                self.extract(current,key,target)
            marker.write_text(json.dumps({'file':target.name},ensure_ascii=False),encoding='utf-8')
            current = target
        return current

    def catalog(self, outer):
        leaves,issues = [],[]
        visited = 0
        def visit(path,chain,name,depth):
            nonlocal visited
            visited += 1
            if depth>MAX_DEPTH or visited>20000:
                issues.append({'chain':chain,'name':name,'code':'depth','message':'嵌套层数或包数超过自动处理上限。'})
                return
            try:
                entries = self.entries(path)
            except (ArchiveError,subprocess.TimeoutExpired,zipfile.BadZipFile) as exc:
                issues.append({'chain':chain,'name':name,'code':getattr(exc,'code','timeout'),
                               'message':'处理超时，可点击自动重试。' if isinstance(exc,subprocess.TimeoutExpired) else str(exc)})
                return
            book_entries = [e for e in entries if e['ext'] in BOOKS]
            archive_entries = [e for e in entries if e['ext'] in ARCHIVES]
            image_count = sum(e['ext'] in IMAGES for e in entries)
            if image_count and not book_entries and not archive_entries:
                leaves.append({'chain':chain,'key':member_key(chain) if chain else '', 'name':name,
                               'ext':Path(name).suffix.lower(),'size':Path(path).stat().st_size,'kind':'comic','image_count':image_count})
                return
            for entry in book_entries:
                keys = [*chain,entry['key']]
                leaves.append({**entry,'chain':keys,'key':member_key(keys),'kind':None,'image_count':0})
            for entry in archive_entries:
                keys = [*chain,entry['key']]
                try:
                    nested = self.materialize(outer,keys,entry['name'].replace('\\','/').rsplit('/',1)[-1])
                    visit(nested,keys,entry['name'],depth+1)
                except (ArchiveError,subprocess.TimeoutExpired) as exc:
                    issues.append({'chain':keys,'name':entry['name'],'code':getattr(exc,'code','timeout'),
                                   'message':'处理超时，可点击自动重试。' if isinstance(exc,subprocess.TimeoutExpired) else str(exc)})
            if not book_entries and not archive_entries and not image_count:
                issues.append({'chain':chain,'name':name,'code':'empty','message':'包内没有小说或图片文件。'})
        visit(Path(outer),[],Path(outer).name,0)
        return leaves,issues


def tempfile_error():
    # A file avoids stdout/stderr pipe deadlocks for large archives.
    import tempfile
    return tempfile.TemporaryFile()
