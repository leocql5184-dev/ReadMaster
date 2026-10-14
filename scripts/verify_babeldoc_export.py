"""Actual HTTP + isolated-engine export check using clearly synthetic fixture data."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from readmaster import server


def main():
    fixture = ROOT / '.build/babel-pdf-fixture'
    server.DATA = ROOT / '.build/babel-http-validation'
    server.DOCS.clear()
    http = server.create_server(0)
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{http.server_port}'
    ident = hashlib.sha256((fixture/'scientific-source.pdf').read_bytes()).hexdigest()[:24]
    folder = server.DATA / 'documents' / ident
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture/'scientific-source.pdf', folder/'original.pdf')
    doc = json.loads((fixture/'revised-document.json').read_text(encoding='utf-8'))
    doc.update(id=ident, name='Synthetic scientific PDF', terms=[], state='complete',
               updated=time.time(), progress={'page': 1})
    server.DOCS[ident] = doc
    server.STOPS[ident] = threading.Event()
    server.save(doc)

    def request(path, body=None, binary=False):
        req = urllib.request.Request(base+path, None if body is None else json.dumps(body).encode(),
                                     {'Content-Type': 'application/json', 'X-ReadMaster': '1'})
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.read() if binary else json.load(response)

    try:
        request('/api/export', {'id': ident})
        request('/api/pause', {'id': ident})
        cancellation_deadline = time.monotonic()+30
        while time.monotonic() < cancellation_deadline:
            state = request('/api/document/'+ident)
            if state.get('pdf_export', {}).get('state') == 'paused' and ident not in server.ACTIVE_TASKS:
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Export cancellation did not finish')
        assert [b['translations'] for b in state['blocks']] == [b['translations'] for b in doc['blocks']]
        request('/api/export', {'id': ident})
        deadline = time.monotonic()+300
        while time.monotonic() < deadline:
            state = request('/api/document/'+ident)
            if state.get('pdf_export', {}).get('state') != 'processing':
                break
            time.sleep(.5)
        else:
            raise RuntimeError('HTTP export timed out')
        assert state['pdf_export']['state'] == 'ready', state.get('pdf_export')
        for kind in ('mono', 'dual'):
            raw = request('/api/document/'+ident+'/export/'+kind, binary=True)
            assert raw.startswith(b'%PDF-'), kind
        # A subsequent translation change must invalidate the prior export.
        first = next(b for b in doc['blocks'] if b['sentences'])
        sid = first['sentences'][0]['id']
        previous = first['translations'][sid]
        first['translations'][sid] = previous+'版本变化'
        assert request('/api/document/'+ident)['pdf_export']['state'] == 'stale'
        try:
            request('/api/document/'+ident+'/export/mono', binary=True)
            raise AssertionError('stale PDF was served')
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
        first['translations'][sid] = previous
        server.save(doc)
        report = {'base': base, 'fixture': 'synthetic', 'pdf_export': 'ready',
                  'cancel_and_regenerate': True, 'translations_preserved': True,
                  'download_formats': ['mono', 'dual'], 'stale_version_rejected': True,
                  'rendered_units': state['pdf_export']['rendered_units']}
        (ROOT/'.build/babel-http-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report), flush=True)
    finally:
        http.shutdown()
        worker.join(timeout=5)
        http.server_close()


if __name__ == '__main__':
    main()
