"""Run HTTP and dense-reader regressions using an isolated real-paper library."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from readmaster.pdf import extract_pdf


def main():
    ident='f285b0971ae4a790e402fb93'
    original=ROOT/'output/ReadMaster-Client-Windows-x64/data/documents'/ident
    data=ROOT/'.build'/('reader-compat-'+uuid.uuid4().hex[:8])
    folder=data/'documents'/ident
    folder.mkdir(parents=True)
    shutil.copyfile(original/'original.pdf',folder/'original.pdf')
    historical=json.loads((original/'document.json').read_text(encoding='utf-8'))
    doc=extract_pdf(folder/'original.pdf')
    old={b['id']:b for b in historical['blocks']}
    for block in doc['blocks']:
        saved=old.get(block['id'],{})
        if block['sentences']==saved.get('sentences') and saved.get('status')=='accepted':
            block['translations']=saved['translations']
            block['status']='accepted'
    doc.update(id=ident,name='Reader compatibility paper',state='partial',terms=historical.get('terms',[]),
               events=[],progress={'page':1},error='',updated=time.time())
    (folder/'document.json').write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
    env=dict(os.environ)
    env['READMASTER_DATA']=str(data)
    env['READMASTER_TEST_URL']=env['READMASTER_URL']='http://127.0.0.1:8790'
    env['READMASTER_TEST_PDF']=str(folder/'original.pdf')
    env.pop('READMASTER_TRANSLATION_ENGINE',None)
    server_log=(data/'server.log').open('w',encoding='utf-8')
    process=subprocess.Popen([sys.executable,'-m','readmaster.server','--no-browser','--port','8790'],
                             cwd=ROOT,env=env,stdout=server_log,stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    report={'paper_pages':len(doc['pages']),'library':str(data),'models_called':False,
            'translation_evidence':'existing accepted translations; UI compatibility only'}
    try:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            try:
                with urllib.request.urlopen(env['READMASTER_URL']+'/api/health',timeout=2) as r:
                    if json.load(r)['ok']:break
            except Exception:
                if process.poll() is not None:raise RuntimeError('Server exited')
                time.sleep(.2)
        else:raise RuntimeError('Server startup timed out')
        req=urllib.request.Request(env['READMASTER_URL']+'/api/models/skip',b'{}',{'X-ReadMaster':'1'})
        with urllib.request.urlopen(req,timeout=10):pass
        subprocess.run([sys.executable,'-m','unittest','discover','-s','tests'],env=env,cwd=ROOT,check=True)
        node=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
        for script in ('word-count.cjs','inline-browser.cjs','progress-browser.cjs'):
            subprocess.run([str(node),str(ROOT/'tests'/script)],env=env,cwd=ROOT,check=True)
        report.update(passed=True,http_tests_enabled=True,dense_inline_and_mobile=True,
                      pause_and_partial_progress_display=True)
    except Exception as exc:
        report.update(error=str(exc),server_exit_code=process.poll())
        raise
    finally:
        if process.poll() is None:
            req=urllib.request.Request(env['READMASTER_URL']+'/api/shutdown',b'{}',{'X-ReadMaster':'1'})
            with urllib.request.urlopen(req,timeout=10):pass
            process.wait(timeout=20)
        server_log.close()
        (ROOT/'.build/reader-compat-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
