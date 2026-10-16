"""Run the official Claude CLI with subscription authentication and text-only input."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

from ai_cleaner import CleanError, MODEL, SCHEMA, SYSTEM


def find_command(configured=''):
    """Resolve native/npm installations without ever passing a command to a shell."""
    candidates=[configured] if configured else [shutil.which('claude')]
    if not configured:
        candidates.extend([str(Path.home()/'.local/bin/claude.exe'),
                           str(Path(os.environ.get('APPDATA',''))/'npm/claude.cmd')])
    for candidate in candidates:
        if not candidate:continue
        path=Path(os.path.expandvars(str(candidate).strip().strip('"'))).expanduser()
        if not path.is_absolute():path=Path(shutil.which(str(path)) or path)
        if not path.is_file():continue
        path=path.resolve()
        if path.suffix.lower() in {'.cmd','.bat','.ps1'}:
            # npm shims forward to these known package entry points. Do not execute
            # a .cmd via cmd.exe: JSON quoting and Unicode must remain argv data.
            package=path.parent/'node_modules/@anthropic-ai/claude-code'
            native=package/'bin/claude.exe'
            if native.is_file():return [str(native)]
            script=package/'cli.js';node=shutil.which('node')
            if script.is_file() and node:return [node,str(script)]
            continue
        if path.suffix.lower()=='.js':
            node=shutil.which('node')
            if node:return [node,str(path)]
        elif (os.name=='nt' and path.suffix.lower()=='.exe') or (os.name!='nt' and os.access(path,os.X_OK)):
            return [str(path)]
    return []


def subscription_environment():
    # Keep the CLI's own login, never inherit keys/proxy/cloud-provider settings
    # from the app's API environment. Changes apply only to the child process.
    allowed={'CLAUDE_CONFIG_DIR','CLAUDE_CODE_OAUTH_TOKEN','CLAUDE_CODE_GIT_BASH_PATH'}
    env={key:value for key,value in os.environ.items()
         if key.upper() in allowed or not key.upper().startswith(('ANTHROPIC_','CLAUDE_','CLAUDECODE'))}
    env.update(CLAUDE_CODE_SAFE_MODE='1',CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
    return env


class ClaudeTerminal:
    def __init__(self,config,directory):
        self.config=config;self.directory=Path(directory)
        self.command=find_command(config.get('terminal_path',''))

    def _run(self,args,prompt=None,timeout=20):
        if not self.command:raise CleanError('没有找到 Claude Code。请先安装，或在设置中指定 Claude 可执行文件。')
        self.directory.mkdir(parents=True,exist_ok=True)
        command=self.command+['--safe-mode','--restricted','--settings','{"forceLoginMethod":"claudeai"}']+args
        try:
            return subprocess.run(command,input=prompt,capture_output=True,text=True,encoding='utf-8',errors='replace',
                timeout=timeout,cwd=self.directory,env=subscription_environment(),
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
        except subprocess.TimeoutExpired:raise CleanError('Claude 命令行响应超时，当前片段未应用；可稍后继续。')
        except OSError:raise CleanError('无法启动 Claude Code，请检查可执行文件路径。')

    def status(self):
        status={'installed':bool(self.command),'path':self.command[-1] if self.command else '',
                'logged_in':False,'auth_method':'','subscription_type':'','ready':False,'message':''}
        if not self.command:
            status['message']='没有找到 Claude Code。安装后在终端运行 claude auth login，并选择订阅账户登录。'
            return status
        try:
            response=self._run(['auth','status'])
            try:auth=json.loads(response.stdout.lstrip('\ufeff'))
            except (ValueError,TypeError):raise CleanError('无法读取 Claude 登录状态，请更新 Claude Code 并运行 claude auth status 检查。')
            if not isinstance(auth,dict):raise CleanError('Claude 登录状态格式不正确，请更新 Claude Code。')
            status.update(logged_in=bool(auth.get('loggedIn')),auth_method=str(auth.get('authMethod') or ''),
                          subscription_type=str(auth.get('subscriptionType') or ''))
            status['ready']=(response.returncode==0 and status['logged_in'] and status['auth_method']=='claude.ai'
                and auth.get('apiProvider')=='firstParty' and status['subscription_type'] in {'pro','max','team','enterprise'})
            if status['ready']:status['message']=f"已登录 Claude {status['subscription_type'].capitalize()} 订阅。"
            else:status['message']='未检测到可用的 Claude 订阅登录。请在终端运行 claude auth login，选择订阅账户；不会回退使用 API。'
        except CleanError as exc:status['message']=str(exc)
        return status

    def require_subscription(self):
        status=self.status()
        if not status['ready']:raise CleanError(status['message'])
        return status

    def analyze(self,segment,context_before='',context_after='',categories=()):
        result,usage=self.generate({'enabled_categories':list(categories),'context_before':context_before,
                           'segment':segment,'context_after':context_after},SYSTEM,SCHEMA)
        if not isinstance(result.get('edits'),list):raise CleanError('Claude 命令行未返回有效的修改清单。')
        return result,usage

    def generate(self,data,system,schema):
        # Recheck before every fragment: a logout or changed credential must stop
        # the job, rather than silently choosing an API-billed credential.
        self.require_subscription()
        prompt=json.dumps(data,ensure_ascii=False)
        response=self._run(['--print','--output-format','json','--json-schema',json.dumps(schema,separators=(',',':')),
            '--model',self.config.get('model',MODEL),'--effort','low','--system-prompt',system,
            '--tools','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--disallowedTools','mcp__*',
            '--disable-slash-commands','--no-chrome','--no-session-persistence','--permission-mode','dontAsk',
            '--max-turns','4'],prompt,180)
        try:body=json.loads(response.stdout.lstrip('\ufeff'))
        except (ValueError,TypeError):body={}
        if not isinstance(body,dict):body={}
        if response.returncode or body.get('is_error') or body.get('subtype')!='success':
            diagnostic=(str(body.get('result',''))+' '+response.stderr+' '+str(body.get('errors',''))).lower()
            if any(word in diagnostic for word in ('rate_limit','rate limit','usage limit','hit your limit','out of extra usage')):
                raise CleanError('Claude 订阅额度暂时不可用，请等待额度恢复；当前片段未应用，也不会切换到 API。')
            if any(word in diagnostic for word in ('model_not_found','model not found','does not exist','not have access to model')):
                raise CleanError('此订阅暂时无法使用设置的模型，请在 Claude 终端确认模型权限；不会切换到 API。')
            raise CleanError(f'Claude 命令行未完成检查（退出码 {response.returncode}）。请在终端确认登录、额度和模型权限，再继续。')
        result=body.get('structured_output')
        if not isinstance(result,dict):
            raise CleanError('Claude 命令行未返回完整的结构化建议，当前片段未应用。请更新 Claude Code 后重试。')
        usage=body.get('usage',{})
        return result,usage if isinstance(usage,dict) else {}
