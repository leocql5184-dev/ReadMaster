"""Run reader regression against the already extracted, isolated portable client."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parent.parent


def main():
    report=json.loads((ROOT/'.build/babel-client-smoke.json').read_text(encoding='utf-8'))
    home=Path(report['home']).resolve()
    if not home.is_relative_to((ROOT/'.build').resolve()) or not report.get('passed'):
        raise RuntimeError('Use the successful isolated portable smoke library')
    file=next((home/'data/documents').glob('*/document.json'))
    doc=json.loads(file.read_text(encoding='utf-8'))
    doc['state']='complete'
    doc['terms']=[{'source':'scientific method','target':'科研方法','definition':'用于验证研究假设的方法。'}]
    doc['outline']=[{'id':'fixture-outline','title':'Methods','translation':'方法','page':1,'level':1}]
    file.write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
    env=dict(os.environ)
    env['PATH']=str(Path(env.get('SystemRoot','C:/Windows'))/'System32')
    env.pop('READMASTER_DATA',None)
    env['READMASTER_URL']='http://127.0.0.1:8789'
    env['READMASTER_FIXTURE_ID']=doc['id']
    process=subprocess.Popen([str(home/'ReadMaster.exe'),'--no-browser','--port','8789'],cwd=home,env=env,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            try:
                with urllib.request.urlopen(env['READMASTER_URL']+'/api/health',timeout=2) as r:
                    if json.load(r)['ok']:break
            except Exception:
                if process.poll() is not None:raise RuntimeError('Client exited during startup')
                time.sleep(.2)
        else:raise RuntimeError('Client startup timeout')
        node=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
        subprocess.run([str(node),str(ROOT/'tests/babeldoc-reader-browser.cjs')],env=env,cwd=ROOT,check=True)
    finally:
        if process.poll() is None:
            req=urllib.request.Request(env['READMASTER_URL']+'/api/shutdown',b'{}',{'X-ReadMaster':'1'})
            with urllib.request.urlopen(req,timeout=10):pass
            process.wait(timeout=20)


if __name__=='__main__':
    main()
