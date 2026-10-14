"""Verify the built portable client and real PDF export in an isolated library."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import uuid
import zipfile

ROOT=Path(__file__).resolve().parent.parent


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--archive',type=Path,default=ROOT/'.build/client-candidate/ReadMaster-Client-Windows-x64.zip')
    args=parser.parse_args()
    destination=ROOT/'.build'/('客户端 验收-'+uuid.uuid4().hex[:8])
    destination.mkdir()
    with zipfile.ZipFile(args.archive) as archive:
        for name in archive.namelist():
            relative=Path(name).parts[1:]
            # Libraries such as OpenCV ship their own data directories. The
            # forbidden directory is ReadMaster's user library at app root.
            if (relative and relative[0]=='data') or name.endswith(('auth.json','original.pdf')):
                raise RuntimeError('Runtime user data found in archive')
            if not (destination/name).resolve().is_relative_to(destination.resolve()):
                raise RuntimeError('Archive path outside verification directory')
        archive.extractall(destination)
    home=destination/'ReadMaster-Client-Windows-x64'
    env=dict(os.environ)
    env['PATH']=str(Path(env.get('SystemRoot','C:/Windows'))/'System32')
    for key in ('READMASTER_DATA','READMASTER_API_KEY','PYTHONPATH','PYTHONHOME','VIRTUAL_ENV'):
        env.pop(key,None)
    env.pop('READMASTER_TRANSLATION_ENGINE',None)
    base='http://127.0.0.1:8788'
    process=None
    def request(path,body=None,raw=False):
        data=body if isinstance(body,bytes) else json.dumps(body).encode() if body is not None else None
        req=urllib.request.Request(base+path,data,{'X-ReadMaster':'1','Content-Type':'application/pdf' if isinstance(body,bytes) else 'application/json'})
        with urllib.request.urlopen(req,timeout=30) as response:
            value=response.read()
            return value if raw else json.loads(value)
    def launch():
        nonlocal process
        process=subprocess.Popen([str(home/'ReadMaster.exe'),'--no-browser','--port','8788'],
                                 cwd=home,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            try:
                if request('/api/health')['ok']:return
            except Exception:
                if process.poll() is not None:raise RuntimeError('Client exited during startup')
                time.sleep(.3)
        raise RuntimeError('Client startup timed out')
    def shutdown():
        if process and process.poll() is None:
            request('/api/shutdown',{})
            process.wait(timeout=20)
    report={'archive_sha256':hashlib.sha256(args.archive.read_bytes()).hexdigest(),
            'home':str(home),'system_python_or_node_required':False,'live_models_tested':False,
            'translations_are_approved_test_fixture':True}
    try:
        launch()
        if request('/api/documents')!=[]:raise RuntimeError('Verification library was not empty')
        pdf=(ROOT/'.build/babel-pdf-fixture/scientific-source.pdf').read_bytes()
        ident=request('/api/import?name=portable-fixture.pdf',pdf)['id']
        doc=request('/api/document/'+ident)
        if not doc['pdf_export_available']:raise RuntimeError('Packaged engine was not detected')
        expected=json.loads((ROOT/'.build/babel-pdf-fixture/document.json').read_text(encoding='utf-8'))
        shutdown()
        path=home/'data/documents'/ident/'document.json'
        saved=json.loads(path.read_text(encoding='utf-8'))
        fixture={b['id']:b for b in expected['blocks'] if b.get('sentences')}
        for block in saved['blocks']:
            if not block.get('sentences'):continue
            approved=fixture[block['id']]
            if block['sentences']!=approved['sentences']:raise RuntimeError('Fixture source/IDs differ')
            block['translations']=approved['translations']
            block['status']='accepted'
        path.write_text(json.dumps(saved,ensure_ascii=False),encoding='utf-8')
        launch()
        request('/api/export',{'id':ident})
        deadline=time.monotonic()+1800
        while time.monotonic()<deadline:
            doc=request('/api/document/'+ident)
            state=doc.get('pdf_export',{}).get('state')
            if state=='ready':break
            if state in ('failed','paused'):raise RuntimeError('Portable export failed: '+str(doc.get('pdf_export')))
            time.sleep(.5)
        else:raise RuntimeError('Portable export timed out')
        for kind in ('mono','dual'):
            content=request('/api/document/'+ident+'/export/'+kind,raw=True)
            if not content.startswith(b'%PDF'):raise RuntimeError('Invalid PDF download')
            (destination/(kind+'.pdf')).write_bytes(content)
        report.update(passed=True,empty_library=True,pdf_engine_detected=True,
                      rendered_units=doc['pdf_export']['rendered_units'],both_pdf_downloads=True)
        shutdown()
        launch()
        restored=request('/api/document/'+ident)
        if restored['pdf_export']['state']!='ready':raise RuntimeError('Export state lost on restart')
        if request('/api/document/'+ident+'/export/mono',raw=True)[:4]!=b'%PDF':raise RuntimeError('Restored download failed')
        report['export_and_translations_survive_restart']=True
    finally:
        shutdown()
        (ROOT/'.build/babel-client-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
