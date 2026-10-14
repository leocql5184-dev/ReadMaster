import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from readmaster.comet_service import ResidentWorker


class ServiceTests(unittest.TestCase):
    def test_process_reused_then_idle_released(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = root / 'fake.py'
            worker.write_text('''import sys,json,os
from pathlib import Path
n=0
for line in sys.stdin:
    req=json.loads(line);n+=1
    out=Path(req['output']);tmp=out.with_suffix('.tmp')
    tmp.write_text(json.dumps({'pid':os.getpid(),'calls':n}))
    tmp.replace(out)
''', encoding='utf-8')
            service = ResidentWorker(idle_seconds=.2)
            try:
                inp = root / 'input.json'
                inp.write_text('{}')
                first = service.invoke(sys.executable, worker, root, inp, root/'a.json', lambda: False)
                second = service.invoke(sys.executable, worker, root, inp, root/'b.json', lambda: False)
                self.assertEqual(first['pid'], second['pid'])
                self.assertEqual(second['calls'], 2)
                process = service.process
                process.wait(timeout=4)
                third = service.invoke(sys.executable, worker, root, inp, root/'c.json', lambda: False)
                self.assertEqual(third['calls'], 1)
            finally:
                service.close()

    def test_cancellation_kills_inflight_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = root/'fake.py'
            worker.write_text('import time; time.sleep(30)', encoding='utf-8')
            service = ResidentWorker()
            start = time.monotonic()
            try:
                with self.assertRaises(InterruptedError):
                    service.invoke(sys.executable, worker, root, root/'in.json', root/'out.json',
                                   lambda: time.monotonic()-start > .2)
                self.assertIsNone(service.process)
            finally:
                service.close()
