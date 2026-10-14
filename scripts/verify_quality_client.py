"""Run actual QE/repair/UI/export through the built EXE in an isolated library.

Reuses an approved synthetic control PDF, deliberately changes one negation,
and keeps model credentials only in a temporary DPAPI-protected local profile.
No credentials or model files are added to the distributable archive.
"""
import json
import argparse
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from readmaster.models import ModelStore
from readmaster.quality import VERSION, score_summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path,
                        default=ROOT/'.build/package-release-candidate/ReadMaster-Client-Windows-x64')
    candidate = parser.parse_args().candidate.resolve()
    if not (candidate/'ReadMaster.exe').is_file():
        raise RuntimeError('The supplied candidate has no ReadMaster.exe')
    control = ROOT/'.build/comet-live-verification'
    completed = json.loads((control/'report.json').read_text(encoding='utf-8'))
    if not completed.get('real_qe_passed') or not completed.get('export_passed'):
        raise RuntimeError('Complete the actual source QE/export verification first')
    directory = ROOT/'.build'/('质量客户端 验证-'+uuid.uuid4().hex[:8])
    home = directory/'ReadMaster'
    shutil.copytree(candidate, home, copy_function=os.link)
    data = home/'data'
    data.mkdir()
    source_models = ROOT/'data/comet-models'
    for folder in ('hub', 'torch'):
        if (source_models/folder).is_dir():
            shutil.copytree(source_models/folder, data/'comet-models'/folder, copy_function=os.link,
                            ignore=shutil.ignore_patterns('.locks', '*.incomplete', '*.tmp', '*.lock'))
    original_store = ModelStore(ROOT/'output/ReadMaster-Client-Windows-x64/data')
    shutil.copyfile(original_store.path, data/'models.json')
    store = ModelStore(data)
    profile = next(p for p in store.public()['profiles'] if p['provider']=='api' and p['has_key'])
    store.activate(profile['id'])
    ident = completed['document_id']
    original = control/'documents'/ident
    folder = data/'documents'/ident
    folder.mkdir(parents=True)
    shutil.copyfile(original/'original.pdf', folder/'original.pdf')
    doc = json.loads((original/'document.json').read_text(encoding='utf-8'))
    for field in ('quality', 'task', 'pdf_export'):
        doc.pop(field, None)
    before = {s['id']:b['translations'][s['id']] for b in doc['blocks'] for s in b['sentences']}
    faulty = next(s for b in doc['blocks'] for s in b['sentences'] if 'does not require' in s['source'])
    for block in doc['blocks']:
        if faulty['id'] in block['translations']:
            block['translations'][faulty['id']] = '该方法需要标注数据。'
        if block['sentences']:
            block['status'] = 'needs_review'
    doc.update(state='needs_review', events=[], error='', updated=time.time())
    (folder/'document.json').write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    with socket.socket() as available:
        available.bind(('127.0.0.1', 0))
        port = available.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = dict(os.environ)
    env.update(PATH=str(Path(env.get('SystemRoot','C:/Windows'))/'System32'),
               HF_HOME=str(directory/'private-hf-home'), HF_HUB_OFFLINE='1', READMASTER_COMET_DEVICE='cpu',
               READMASTER_URL=base, READMASTER_FIXTURE_ID=ident,
               READMASTER_TEST_PDF=str(folder/'original.pdf'), READMASTER_FAULT_ID=faulty['id'])
    for key in ('READMASTER_DATA','READMASTER_API_KEY','PYTHONPATH','PYTHONHOME','VIRTUAL_ENV',
                'HF_TOKEN','HUGGINGFACE_HUB_TOKEN','READMASTER_TRANSLATION_ENGINE','READMASTER_COMET_PYTHON',
                'READMASTER_KIWI_MODEL','READMASTER_XCOMET_MODEL','READMASTER_KIWI_REVISION','READMASTER_XCOMET_REVISION'):
        env.pop(key, None)
    process = subprocess.Popen([str(home/'ReadMaster.exe'),'--no-browser','--port',str(port)],
                              cwd=home, env=env, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    report = {'home':str(home), 'synthetic_control':True, 'metrics_mocked':False,
              'llm_model':profile['model'],
              'offline_model_cache':True, 'system_python_or_node_required_by_client':False,
              'user_library_modified':False, 'passed':False}
    def request(path, body=None):
        req = urllib.request.Request(base+path, json.dumps(body).encode() if body is not None else None,
                                     {'X-ReadMaster':'1'})
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)
    try:
        deadline = time.monotonic()+60
        while time.monotonic()<deadline:
            try:
                health = request('/api/health')
                if health['ok']:
                    report['version'] = health.get('version')
                    break
            except Exception:
                if process.poll() is not None:
                    raise RuntimeError('Native client exited during startup')
                time.sleep(.3)
        else:
            raise RuntimeError('Native startup timed out')
        node = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
        subprocess.run([str(node), str(ROOT/'tests/quality-client-browser.cjs')], env=env, cwd=ROOT, check=True)
        final = request('/api/document/'+ident)
        if final.get('quality',{}).get('status')!='accepted':
            raise RuntimeError('Native quality verification did not pass')
        if final['quality']['version']!=VERSION or not score_summary(final['quality']['scores'])['score_gate_passed']:
            raise RuntimeError('Native accepted a score below threshold')
        after = {s['id']:b['translations'][s['id']] for b in final['blocks'] for s in b['sentences']}
        if set(before)!=set(after) or any(after[k]!=v for k,v in before.items() if k!=faulty['id']):
            raise RuntimeError('Targeted repair changed unrelated sentence IDs/text')
        history = json.loads((folder/'history/document-quality.json').read_text(encoding='utf-8'))
        if not any(h.get('xcomet') for h in history['history']) or not history['history'][-1].get('acceptance'):
            raise RuntimeError('Missing actual XCOMET/independent acceptance evidence')
        downloads = {}
        for kind in ('mono','dual'):
            with urllib.request.urlopen(base+'/api/document/'+ident+'/export/'+kind, timeout=30) as response:
                content = response.read()
            if not content.startswith(b'%PDF'):
                raise RuntimeError('Native exported download is not PDF')
            with pymupdf.open(stream=content, filetype='pdf') as pdf:
                text = re.sub(r'\s+', '', '\n'.join(p.get_text() for p in pdf))
                if re.sub(r'\s+', '', after[faulty['id']]) not in text:
                    raise RuntimeError('Native PDF does not contain the current repaired sentence')
                downloads[kind] = {'bytes':len(content), 'pages':len(pdf), 'repaired_negation_present':True}
            (directory/(kind+'.pdf')).write_bytes(content)
        report.update(passed=True, quality=final['quality'], pdf_export=final['pdf_export'],
                      unchanged_sentences_preserved=True, sentence_ids_preserved=True, downloads=downloads)
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        if process.poll() is None:
            try:
                request('/api/pause', {'id':ident})
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    try:
                        request('/api/shutdown', {})
                        break
                    except Exception:
                        time.sleep(.5)
                process.wait(timeout=30)
            except Exception:
                process.terminate()
                process.wait(timeout=10)
        (ROOT/'.build/quality-client-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'], 'version':report.get('version'),
                      'minimum_score':final['quality']['minimum_score']}), flush=True)


if __name__ == '__main__':
    main()
