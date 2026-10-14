"""Fault-injected initial result, live reviewer/repair, UI refresh and native PDF."""
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import threading
import time
import uuid
from unittest.mock import patch
import pymupdf

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from readmaster import server
from readmaster.models import ModelStore
from readmaster.pdf import extract_pdf


def main():
    data=ROOT/'.build'/('revision-pdf-loop-'+uuid.uuid4().hex[:8])
    data.mkdir()
    source=data/'clinical-control.pdf'
    with pymupdf.open() as pdf:
        page=pdf.new_page(width=500,height=300)
        page.insert_textbox(pymupdf.Rect(50,50,450,160),
                            'The treatment did not improve survival. The sample contains twenty patients.',fontsize=11)
        pdf.save(source)
    ident=hashlib.sha256(source.read_bytes()).hexdigest()[:24]
    folder=data/'documents'/ident
    folder.mkdir(parents=True)
    (folder/'original.pdf').write_bytes(source.read_bytes())
    doc=extract_pdf(folder/'original.pdf')
    assert sum(len(b['sentences']) for b in doc['blocks'])==2
    doc.update(id=ident,name='Synthetic clinical translation control',state='ready',events=[],
               progress={'page':1},error='',updated=time.time(),terms=[
                   {'source':'treatment','target':'治疗','definition':'治疗处理。'},
                   {'source':'survival','target':'生存率','definition':'这里指生存情况。'},
                   {'source':'patients','target':'患者','definition':'患者。'}])
    (folder/'document.json').write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
    server.DATA=data
    http=server.create_server(0)
    store=ModelStore(ROOT/'output/ReadMaster-Client-Windows-x64/data')
    profile=next(p for p in store.public()['profiles'] if p['provider']=='api'
                 and p.get('endpoint','').rstrip('/')=='https://api.deepseek.com/v1' and p['has_key'])
    server.CONFIG,server.KEY=store.snapshot(profile['id'])
    # A test-only readiness sentinel holds no key; the real credential stays
    # in memory. Neither the test library nor any PDF worker receives it.
    server.MODELS.state['active_id']='ephemeral-test-connection'
    real_factory=server.ApiAgent
    evidence={'initial_fault_injected':False,'real_calls':0}
    class ControlledInitial:
        def __init__(self,*args,**kwargs):self.real=real_factory(*args,**kwargs)
        def call(self,role,payload,schema):
            if role=='babeldoc_translate' and payload['role']=='translate' and not evidence['initial_fault_injected']:
                evidence['initial_fault_injected']=True
                return {'items':[{'id':0,'output':'该治疗提高了生存率。'},
                                 {'id':1,'output':'样本包含二十名患者。'}]}
            evidence['real_calls']+=1
            return self.real.call(role,payload,schema)
    thread=threading.Thread(target=http.serve_forever,daemon=True)
    thread.start()
    env=dict(os.environ)
    env['READMASTER_URL']=f'http://127.0.0.1:{http.server_port}'
    env['READMASTER_FIXTURE_ID']=ident
    env['READMASTER_TEST_PDF']=str(source)
    os.environ.pop('READMASTER_TRANSLATION_ENGINE',None)
    node=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    try:
        with patch.object(server,'ApiAgent',ControlledInitial):
            subprocess.run([str(node),str(ROOT/'tests/revision-pdf-browser.cjs')],env=env,cwd=ROOT,check=True)
        final=server.DOCS[ident]
        history=json.loads(next((folder/'history').glob('*.json')).read_text(encoding='utf-8'))
        first=next(b for b in final['blocks'] if b['sentences'])['sentences'][0]['id']
        repairs=[h for h in history['history'] if h['role']=='revise']
        assert repairs and all({t['id'] for t in h['result']['translations']}=={first} for h in repairs)
        assert final['pdf_export']['state']=='ready'
        report={**evidence,'passed':True,'fixture':'synthetic; intentionally false initial negation',
                'targeted_live_repair':True,'reader_refresh_and_pdf_download':True,
                'rendered_units':final['pdf_export']['rendered_units'],
                'pdf_export':final['pdf_export'],'library':str(data)}
        (ROOT/'.build/revision-pdf-loop-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:report[k] for k in ('passed','initial_fault_injected','real_calls','rendered_units')}),flush=True)
    finally:
        server.STOPS[ident].set()
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)


if __name__=='__main__':
    main()
