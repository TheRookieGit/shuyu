"""爱妻的书屿 Windows app: the shelf in its own window, with the service kept in the system tray.

Closing the window only hides it, so the GitHub web page can still reach this computer.
"""
import json
from pathlib import Path
import sys
import threading
import urllib.request
import webbrowser

import server

APP_NAME = '爱妻的书屿'
WEB_ORIGIN = 'https://therookiegit.github.io'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'AiqiShuyu'
ICON = Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'assets'/'shuyu.ico'


def launch_command():
    if getattr(sys,'frozen',False):
        return f'"{sys.executable}" --minimized'
    return f'"{sys.executable}" "{Path(__file__).resolve()}" --minimized'


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:
            return winreg.QueryValueEx(key,RUN_VALUE)[0]==launch_command()
    except OSError:
        return False


def set_autostart(enabled):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY,0,winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key,RUN_VALUE,0,winreg.REG_SZ,launch_command())
        else:
            try:winreg.DeleteValue(key,RUN_VALUE)
            except FileNotFoundError:pass


def show_running(port):
    """Ask the copy already running to show its window; False if it is not the app."""
    request = urllib.request.Request(f'http://127.0.0.1:{port}/api/app/show',data=b'{}',headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=5) as response:
            return bool(json.load(response).get('ok'))
    except (OSError,ValueError):
        return False


def main():
    parser = server.build_parser()
    parser.add_argument('--minimized',action='store_true',help='启动后只显示在系统托盘')
    parser.add_argument('--no-window',action='store_true',help='只运行后台服务（用于自动测试）')
    args = parser.parse_args()
    args.allow_origin = args.allow_origin or [WEB_ORIGIN]
    url = f'http://127.0.0.1:{args.port}'
    service = server.start(args)
    if service is None:
        if not args.no_window and not args.minimized and not show_running(args.port):
            webbrowser.open(url)
        return
    if args.no_window:
        try:service.serve_forever()
        finally:service.server_close()
        return

    import webview
    import pystray
    from PIL import Image

    quitting = threading.Event()
    window = webview.create_window(APP_NAME,url,width=1320,height=880,min_size=(960,640),hidden=args.minimized)

    def on_closing():
        if quitting.is_set():return True
        window.hide()
        return False

    def show(*_):
        window.show()
        window.restore()

    def quit_app(*_):
        if quitting.is_set():return
        quitting.set()
        tray.stop()
        threading.Thread(target=service.shutdown,daemon=True).start()
        window.destroy()

    def toggle_autostart(*_):
        set_autostart(not autostart_enabled())

    def serve():
        service.serve_forever()
        # A newer copy took over the port (see app_runtime.reuse_or_reload): leave quietly.
        if not quitting.is_set():quit_app()

    window.events.closing += on_closing
    service.on_show = show
    tray = pystray.Icon('shuyu',Image.open(ICON),APP_NAME,menu=pystray.Menu(
        pystray.MenuItem('打开爱妻的书屿',show,default=True),
        pystray.MenuItem('在浏览器中打开',lambda *_:webbrowser.open(url)),
        pystray.MenuItem('开机自动启动',toggle_autostart,checked=lambda _:autostart_enabled()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('退出',quit_app)))
    threading.Thread(target=serve,daemon=True).start()
    tray.run_detached()
    try:
        webview.start(private_mode=False,storage_path=str(Path(args.data_dir)/'webview'))
    finally:
        quit_app()
        service.server_close()


if __name__=='__main__':
    main()
