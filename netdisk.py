"""Download novels from Quark (夸克网盘) and Baidu (百度网盘) share links into the library, as if uploaded from Lite.

Neither drive has a public API for this. Following their web pages: sign in by scanning a QR code with the drive's
app, save the shared novels and archives into the reader's own drive (a 书屿转存 folder), download them from there,
and store each one through Lite's upload, so archives are unpacked and novels named the usual way.

Speaking to the drives, and the sign-ins themselves, come from collectionkit (the my-collections submodule), which
Plexus and the wish hub share: signing in or out in one of them does so in all."""
from __future__ import annotations

import json
from pathlib import PurePosixPath
import shutil
import threading
import uuid

from ai_cleaner import SUPPORTED, CleanError, MAX_SOURCE
from archive_engine import ArchiveError, ARCHIVES
from collectionkit.netdisk import Drives, NetDiskError, Unreachable, links
from lite_upload import MAX_ARCHIVE

FOLDER = '书屿转存'
MAX_FILES = 500


def wanted(name,size):
    """Why a shared file is left out, or '' when it is a novel or archive Lite can take."""
    ext = PurePosixPath(name).suffix.lower()
    if ext not in SUPPORTED|ARCHIVES:return '不是小说或压缩包'
    limit = MAX_ARCHIVE if ext in ARCHIVES else MAX_SOURCE
    return '' if 0<size<=limit else f'超过 {limit//1024//1024} MB' if size>limit else '是空文件'


class NetDisk(Drives):
    """The drives signed in to for every app on this computer, and the downloads that bring their novels here."""
    def __init__(self,lib,store=None):
        super().__init__(FOLDER,store,MAX_FILES)
        self.lib = lib
        self.tasks = {}
        # Sign-ins from before they were shared move over once; the old rows stay, so an older 书屿 still has them.
        with lib.db() as db:
            rows = db.execute("SELECT key,value FROM settings WHERE key IN ('netdisk_quark','netdisk_baidu')").fetchall()
        saved = {}
        for row in rows:
            try:saved[row['key'].removeprefix('netdisk_')] = dict(json.loads(row['value']))
            except (ValueError,TypeError):pass
        self.adopt(saved)

    def fetch(self,text):
        found = links(text)
        if not found:raise NetDiskError('没有找到夸克网盘或百度网盘的分享链接。')
        for link in found:
            drive = self.drives[link['drive']]
            if not drive.signed_in:raise NetDiskError(f'请先用{drive.app}扫码登录{drive.label}。',login=drive.key)
        task = {'id':uuid.uuid4().hex[:12],'state':'reading','message':'正在读取分享链接…','books':[],'notes':[],'failed':[],'skipped':[],'login':''}
        self.tasks = {k:v for k,v in self.tasks.items() if v['state'] not in ('done','failed')}
        self.tasks[task['id']] = task
        threading.Thread(target=self._run,args=(task,found),daemon=True).start()
        return task

    def task(self,task_id):
        if task_id not in self.tasks:raise NetDiskError('找不到这次下载，可能书屿重新启动过。')
        return self.tasks[task_id]

    def _run(self,task,found):
        work = self.lib.data_dir/'netdisk'/task['id']
        work.mkdir(parents=True,exist_ok=True)
        try:
            for number,link in enumerate(found,1):
                drive = self.drives[link['drive']]
                where = f'{drive.label}链接' if len(found)==1 else f'第 {number} 个链接（{drive.label}）'
                task.update(state='reading',message=f'正在读取{where}…')
                try:share,files = drive.files(link)
                except NetDiskError as exc:
                    if exc.login:raise
                    task['failed'].append(f'{where}：{exc}');continue
                keep = []
                for item in files:
                    reason = wanted(item['name'],item['size'])
                    if reason:task['skipped'].append(f'{item["name"]}（{reason}）')
                    else:keep.append(item)
                if not keep:
                    task['failed'].append(f'{where}：里面没有 TXT、MD、EPUB 小说或压缩包。');continue
                task.update(state='saving',message=f'正在把 {len(keep)} 个文件转存到你{drive.label}的「{FOLDER}」文件夹…')
                try:
                    for done,(item,result) in enumerate(self.download(drive,share,keep,work),1):
                        if isinstance(result,str):task['failed'].append(f'{item["name"]}：{result}');continue
                        task.update(state='downloading',message=f'已下载 {done} / {len(keep)}：{item["name"]}')
                        self._store(task,item,result)
                except NetDiskError as exc:
                    if exc.login:raise
                    task['failed'].append(f'{where}：{exc}')
            task.update(state='done',message='')
        except NetDiskError as exc:
            task.update(state='failed',message=str(exc),login=exc.login)
        except (KeyError,TypeError,ValueError,AttributeError) as exc:
            task.update(state='failed',message='网盘的接口可能变了，没能完成下载。')
        finally:
            shutil.rmtree(work,ignore_errors=True)

    def download(self,drive,share,files,work):
        """Save the files into the reader's drive, then bring each into the work folder; yields (file, path or why not)."""
        for item,ref in drive.save(share,files):
            if isinstance(ref,str):yield item,ref;continue
            path = work/uuid.uuid4().hex
            try:
                with path.open('wb') as sink:status = drive.stream(ref,0,sink)
            except NetDiskError as exc:
                # A dropped connection or a missing link costs this file only; a lapsed sign-in stops the whole task.
                path.unlink(missing_ok=True)
                if exc.login:raise
                yield item,str(exc);continue
            if status!=200:path.unlink(missing_ok=True);yield item,f'下载失败（HTTP {status}）';continue
            if path.stat().st_size!=item['size']:path.unlink(missing_ok=True);yield item,'下载不完整，请重试';continue
            yield item,path

    def _store(self,task,item,path):
        """Into the library through Lite's upload, so the file is named, indexed and unpacked the same way."""
        try:
            with path.open('rb') as stream:result = self.lib.uploads.receive(stream,path.stat().st_size,item['name'])
        except (CleanError,ArchiveError,OSError,ValueError) as exc:
            task['failed'].append(f'{item["name"]}：{exc}');return
        finally:
            path.unlink(missing_ok=True)
        if 'books' in result:
            task['books'].extend(result['books'])
            task['notes'].append(f'{item["name"]}：取出 {len(result["books"])} 本小说'+(f'，另有 {result["other_files"]} 个其他文件没有导入' if result.get('other_files') else '')
                                 +(f'；{len(result["failed"])} 处没能取出' if result.get('failed') else ''))
        else:task['books'].append(result)
