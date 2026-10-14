"""One bounded resident COMET process shared by documents; idle RAM is released."""
import atexit
import json
from pathlib import Path
import subprocess
import threading
import time


class ResidentWorker:
    def __init__(self, idle_seconds=120):
        self.lock = threading.Lock()
        self.process = self.timer = self.log = None
        self.identity = None
        self.idle_seconds = idle_seconds
        self.last_used = time.monotonic()
        self.last_report = None

    def _stop(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process.stdin.close()
            self.process = None
        if self.log:
            self.log.close()
            self.log = None

    def close(self):
        with self.lock:
            self._stop()

    def _expire(self):
        with self.lock:
            if time.monotonic() - self.last_used >= self.idle_seconds:
                self._stop()

    def invoke(self, python, worker, root, inp, out, cancelled):
        while not self.lock.acquire(timeout=.2):
            if cancelled():
                raise InterruptedError('已暂停 COMET 评估')
        try:
            if cancelled():
                raise InterruptedError('已暂停 COMET 评估')
            if self.timer:
                self.timer.cancel()
                self.timer = None
            identity = (str(python), str(worker), str(root))
            if self.identity != identity or self.process is None or self.process.poll() is not None:
                self._stop()
                path = Path(root) / 'data' / 'comet-worker.log'
                path.parent.mkdir(parents=True, exist_ok=True)
                # Bounded log: each worker lifetime replaces the previous log.
                self.log = path.open('w', encoding='utf-8')
                self.process = subprocess.Popen([str(python), str(worker), '--serve'],
                    stdin=subprocess.PIPE, stdout=self.log, stderr=self.log, text=True, encoding='utf-8',
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.identity = identity
            self.process.stdin.write(json.dumps({'input': str(inp), 'output': str(out)}) + '\n')
            self.process.stdin.flush()
            deadline = time.monotonic() + 1800
            while not out.exists():
                if cancelled():
                    raise InterruptedError('已暂停 COMET 评估')
                if self.process.poll() is not None:
                    raise RuntimeError('COMET 推理进程退出；初译已保存')
                if time.monotonic() > deadline:
                    raise RuntimeError('COMET 推理超时；初译已保存')
                time.sleep(.1)
            reply = json.loads(out.read_text(encoding='utf-8'))
            self.last_report = reply.get('worker')
            return reply
        except BaseException:
            self._stop()
            raise
        finally:
            if self.process is not None:
                self.last_used = time.monotonic()
                self.timer = threading.Timer(self.idle_seconds, self._expire)
                self.timer.daemon = True
                self.timer.start()
            self.lock.release()


WORKER = ResidentWorker()
atexit.register(WORKER.close)
