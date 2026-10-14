"""Standalone Windows window; existing PDF/model processing stays local."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import logging
import os
import threading
import time

from . import server


class Instance:
    """A per-library Windows mutex prevents duplicate writers and windows."""
    def __init__(self, directory):
        self.directory = directory
        self.handle = None
        self.kernel = None

    def acquire(self):
        if os.name != 'nt':
            return True
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        name = hashlib.sha256(str(self.directory.resolve()).casefold().encode()).hexdigest()[:24]
        self.handle = self.kernel.CreateMutexW(None, True, 'Local\\ReadMaster-' + name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            return False
        return True

    def focus_existing(self):
        # A second click during initial startup waits briefly for the window.
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        for _ in range(80):
            try:
                pid = json.loads((self.directory / 'desktop-instance.json').read_text(encoding='utf-8'))['pid']
            except (OSError, ValueError, KeyError):
                time.sleep(.1)
                continue
            matches = []

            @callback_type
            def visit(hwnd, _):
                window_pid = wintypes.DWORD()
                user.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
                if window_pid.value == pid:
                    title = ctypes.create_unicode_buffer(256)
                    user.GetWindowTextW(hwnd, title, len(title))
                    # A hidden main window still owns the single-instance mutex.
                    # Do not mistake .NET/IME helper windows for the reader.
                    if title.value == 'ReadMaster':
                        matches.append(hwnd)
                return True

            user.EnumWindows(visit, 0)
            if matches:
                user.ShowWindow(matches[0], 9)  # SW_RESTORE
                user.SetForegroundWindow(matches[0])
                return
            time.sleep(.1)

    def release(self):
        if self.handle:
            self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def window_size(path):
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
        return max(900, min(2200, int(saved['width']))), max(600, min(1400, int(saved['height'])))
    except (OSError, ValueError, KeyError, TypeError):
        return 1280, 850


class CloseController:
    """Finish the current model call and persist results before closing."""
    def __init__(self, window):
        self.window = window
        self.waiting = False

    def on_closing(self):
        with server.GUARD:
            server.SHUTTING_DOWN.set()
            for stop in server.STOPS.values():
                stop.set()
            active = bool(server.ACTIVE_TASKS)
        if not active:
            return True
        if not self.waiting:
            self.waiting = True
            threading.Thread(target=self.finish, daemon=True).start()
        return False

    def finish(self):
        try:
            self.window.evaluate_js("document.getElementById('statusText').textContent = '正在暂停并保存，当前模型调用结束后自动退出…'; document.querySelectorAll('button').forEach(b => b.disabled = true)")
        except Exception:
            logging.exception('close notification failed')
        while True:
            with server.GUARD:
                if not server.ACTIVE_TASKS:
                    break
            time.sleep(.2)
        self.window.destroy()


def main():
    parser = argparse.ArgumentParser(description='ReadMaster 桌面客户端')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    server.DATA.mkdir(parents=True, exist_ok=True)
    instance = Instance(server.DATA)
    if not instance.acquire():
        instance.focus_existing()
        return
    http = None
    worker = None
    url = ''
    finished = threading.Event()
    try:
        import webview
        webview.settings['ALLOW_DOWNLOADS'] = True
        http = server.create_server(args.port)
        url = f'http://127.0.0.1:{http.server_port}'
        (server.DATA / 'address.txt').write_text(url, encoding='utf-8')
        server.atomic_json(server.DATA / 'desktop-instance.json', {'pid': os.getpid()})

        def serve():
            try:
                http.serve_forever(poll_interval=.2)
            finally:
                finished.set()

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        webview.settings['ALLOW_FILE_URLS'] = False
        webview.settings['SHOW_DEFAULT_MENUS'] = False
        width, height = window_size(server.DATA / 'window.json')
        window = webview.create_window('ReadMaster', url, width=width, height=height,
            min_size=(900, 600), background_color='#ffffff', text_select=True)
        closing = CloseController(window)
        window.events.closing += closing.on_closing

        def resized(width, height):
            if width >= 900 and height >= 600:
                with server.GUARD:
                    server.atomic_json(server.DATA / 'window.json', {'width': width, 'height': height})

        window.events.resized += resized

        def monitor():
            finished.wait()
            if not window.events.closed.is_set():
                window.destroy()

        # The existing footer Exit shuts down the service, then this window.
        threading.Thread(target=monitor, daemon=True).start()
        webview.start(gui='edgechromium', private_mode=False,
            storage_path=str(server.DATA / 'webview'), icon=str(server.ROOT / 'assets' / 'ReadMaster.ico'))
    except Exception:
        logging.exception('desktop startup failed')
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(None,
                'ReadMaster 客户端无法启动。请确认已安装 Microsoft Edge WebView2 Runtime。\n详细信息保存在 data/application.log。',
                'ReadMaster', 0x10)
        else:
            raise
    finally:
        if http:
            if worker and worker.is_alive():
                http.shutdown()
                worker.join(timeout=5)
            http.server_close()
        server.clear_address(url)
        (server.DATA / 'desktop-instance.json').unlink(missing_ok=True)
        instance.release()
