"""Unzip and verify the actual executable with no developer runtimes on PATH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('pdf')
parser.add_argument('--real', action='store_true', help='Run one page through the authenticated packaged Codex adapter')
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
archive = root/'output/ReadMaster-Windows-x64.zip'
destination = root/'.build'/('便携 解压测试 '+str(int(time.time())))
destination.mkdir(parents=True)
with zipfile.ZipFile(archive) as z:
    names = z.namelist()
    assert not any('/data/' in n or n.endswith(('auth.json', 'original.pdf')) for n in names)
    for name in names:
        assert (destination/name).resolve().is_relative_to(destination.resolve())
    z.extractall(destination)
home = destination/'ReadMaster-Windows-x64'
env = dict(os.environ)
env['PATH'] = str(Path(os.environ['SystemRoot'])/'System32')
env.pop('READMASTER_DATA', None)
env.pop('PYTHONPATH', None)
env.pop('READMASTER_API_KEY', None)
process = subprocess.Popen([str(home/'ReadMaster.exe'), '--no-browser', '--port', '8768'],
                           cwd=home, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
base = 'http://127.0.0.1:8768'

def request(path, body=None, raw=False):
    data = body if raw else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base+path, data, {'X-ReadMaster': '1', 'Content-Type': 'application/pdf' if raw else 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        value = response.read()
        return value if raw else json.loads(value)

try:
    deadline = time.monotonic()+60
    while time.monotonic()<deadline:
        try:
            assert request('/api/health')['ok']
            break
        except Exception:
            if process.poll() is not None:
                raise RuntimeError('Portable app exited before health check')
            time.sleep(.5)
    else:
        raise RuntimeError('Portable app did not start')
    assert request('/api/documents') == []
    pdf = Path(args.pdf).read_bytes()
    ident = request('/api/import?name=portable-test.pdf', pdf, raw=True)
    # Raw request returns JSON bytes for the import route.
    ident = json.loads(ident)['id']
    assert ident == hashlib.sha256(pdf).hexdigest()[:24]
    document = request('/api/document/'+ident)
    assert len(document['pages']) == 33
    image = request('/api/document/'+ident+'/image?page=1', raw=True)
    assert image.startswith(b'\x89PNG')
    assert request('/api/settings')['codex_available']
    version = subprocess.check_output([str(home/'tools/codex/bin/codex.exe'), '--version'], env=env,
                                     creationflags=subprocess.CREATE_NO_WINDOW, text=True).strip()
    second = subprocess.Popen([str(home/'ReadMaster.exe'), '--no-browser'], cwd=home, env=env,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    assert second.wait(timeout=15) == 0
    report = {'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
              'clean_archive': True, 'no_python_or_node_on_path': True, 'unicode_and_spaces_path': True,
              'pages_imported': len(document['pages']), 'image_rendered': True, 'codex': version, 'single_instance': True}
    # Reference-only range checks job creation/completion without a paid model call.
    request('/api/start', {'id': ident, 'start_page': 11, 'end_page': 11})
    for _ in range(40):
        document = request('/api/document/'+ident)
        if document['state'] != 'processing':
            break
        time.sleep(.1)
    progress = document['processing_progress']
    assert progress['percent'] == 100 and progress['state'] == 'complete'
    assert progress['total_units'] == 0 and progress['start_page'] == 11
    report['selected_range_progress'] = True
    # Seed an explicit test fixture, restart, and read it without any model call.
    request('/api/shutdown', {})
    process.wait(timeout=15)
    saved_path = home/'data/documents'/ident/'document.json'
    saved = json.loads(saved_path.read_text(encoding='utf-8'))
    sample = next(b for b in saved['blocks'] if b['sentences'])
    sample['translations'] = {s['id']: '离线历史测试译文' for s in sample['sentences']}
    sample['status'] = 'accepted'
    saved_path.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
    process = subprocess.Popen([str(home/'ReadMaster.exe'), '--no-browser', '--port', '8768'],
                               cwd=home, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(60):
        try:
            if request('/api/health')['ok']:break
        except Exception:time.sleep(.2)
    restored = request('/api/document/'+ident)
    restored_sample = next(b for b in restored['blocks'] if b['id']==sample['id'])
    assert restored_sample['translations']==sample['translations']
    assert request('/api/documents')[0]['translated_blocks']==1
    repeated = json.loads(request('/api/import?name=renamed-history.pdf', pdf, raw=True))
    assert repeated=={'id':ident, 'cached':True}
    assert len(request('/api/documents'))==1
    report['history_restart_and_renamed_pdf_reuse'] = True
    report['history_translation_is_fixture'] = True
    if args.real:
        request('/api/start', {'id': ident, 'start_page': 3, 'end_page': 3})
        deadline = time.monotonic()+1200
        stage = None
        while time.monotonic()<deadline:
            document = request('/api/document/'+ident)
            if document.get('active_role') != stage:
                stage = document.get('active_role')
                print('packaged real pipeline: '+str(stage), flush=True)
            if document['state'] != 'processing':
                break
            time.sleep(3)
        else:
            raise RuntimeError('Packaged real pipeline exceeded its test budget')
        if document['state'] == 'failed':
            raise RuntimeError(document['error'])
        blocks = [b for b in document['blocks'] if b['page'] == 3 and b['sentences']]
        for block in blocks:
            assert set(block['translations']) == {s['id'] for s in block['sentences']}
            assert block['status'] in ('accepted', 'needs_review')
        report['real_pipeline'] = {'page': 3, 'blocks': len(blocks),
                    'accepted': sum(b['status'] == 'accepted' for b in blocks),
                    'needs_review': sum(b['status'] == 'needs_review' for b in blocks)}
    request('/api/delete', {'id': ident})
    assert request('/api/documents') == []
    assert not (home/'data/documents'/ident).exists()
    assert Path(args.pdf).read_bytes() == pdf
    report['delete_library_copy_preserves_external_pdf'] = True
    (root/'output/portable-test-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)
finally:
    try:
        if any(d['id']==ident for d in request('/api/documents')):
            request('/api/pause', {'id': ident})
            for _ in range(90):
                if request('/api/document/'+ident)['state'] != 'processing':
                    break
                time.sleep(3)
        request('/api/shutdown', {})
        process.wait(timeout=15)
    except Exception:
        process.terminate()
