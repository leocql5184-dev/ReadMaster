"""Desktop lifecycle checks that must not call a real model."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from readmaster import server
from readmaster.desktop import CloseController, Instance, window_size


class DesktopTests(unittest.TestCase):
    def tearDown(self):
        server.SHUTTING_DOWN.clear()

    def test_close_waits_for_active_model_call(self):
        window = Mock()
        destroyed = threading.Event()
        window.destroy.side_effect = destroyed.set
        stop = threading.Event()
        active = {'paper'}
        with patch.object(server, 'ACTIVE_TASKS', active), patch.object(server, 'STOPS', {'paper': stop}):
            close = CloseController(window)
            self.assertFalse(close.on_closing())
            self.assertTrue(stop.is_set())
            self.assertFalse(destroyed.wait(.05))
            with server.GUARD:
                active.clear()  # process_document removes this only after save.
            self.assertTrue(destroyed.wait(2))
            self.assertTrue(close.on_closing())

    def test_shutdown_blocks_new_work_but_allows_progress_save(self):
        with tempfile.TemporaryDirectory() as folder:
            ident = 'a' * 24
            doc = {'id': ident, 'state': 'complete', 'pages': [{}], 'blocks': [], 'progress': {}}
            with patch.object(server, 'DATA', Path(folder)), patch.object(server, 'DOCS', {ident: doc}):
                http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
                thread = threading.Thread(target=http.serve_forever, daemon=True)
                thread.start()
                try:
                    server.SHUTTING_DOWN.set()
                    base = f'http://127.0.0.1:{http.server_port}'
                    def post(path, payload):
                        return urllib.request.urlopen(urllib.request.Request(base + path,
                            json.dumps(payload).encode(), {'X-ReadMaster': '1'}), timeout=3)
                    with self.assertRaises(urllib.error.HTTPError) as failure:
                        post('/api/start', {'id': ident})
                    self.assertEqual(failure.exception.code, 409)
                    with post('/api/progress', {'id': ident, 'page': 1}) as response:
                        self.assertEqual(response.status, 200)
                    saved = json.loads((Path(folder) / 'documents' / ident / 'document.json').read_text())
                    self.assertEqual(saved['progress']['page'], 1)
                finally:
                    http.shutdown(); http.server_close(); thread.join()

    def test_saved_window_size_is_bounded_and_corruption_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'window.json'
            path.write_text('{')
            self.assertEqual(window_size(path), (1280, 850))
            path.write_text(json.dumps({'width': -1, 'height': 90000}))
            self.assertEqual(window_size(path), (900, 1400))

    def test_windows_mutex_releases_for_relaunch(self):
        import os
        if os.name != 'nt':
            self.skipTest('Windows instance guard')
        with tempfile.TemporaryDirectory() as folder:
            first, second = Instance(Path(folder)), Instance(Path(folder))
            try:
                self.assertTrue(first.acquire())
                self.assertFalse(second.acquire())
            finally:
                first.release()
            try:
                self.assertTrue(second.acquire())
            finally:
                second.release()
