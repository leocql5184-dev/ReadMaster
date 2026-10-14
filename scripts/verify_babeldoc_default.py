"""Live default controller -> evaluation -> acceptance -> native PDF export."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from readmaster import server, progress
from readmaster.babeldoc_export import export_document
from readmaster.models import ModelStore
from readmaster.pdf import extract_pdf


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--provider',choices=['deepseek','codex'],required=True)
    args=parser.parse_args()
    os.environ.pop('READMASTER_TRANSLATION_ENGINE',None)
    source=ROOT/'.build/babel-pdf-fixture/scientific-source.pdf'
    ident=hashlib.sha256(source.read_bytes()).hexdigest()[:24]
    data=ROOT/'.build/default-live'/args.provider
    folder=data/'documents'/ident
    folder.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,folder/'original.pdf')
    if args.provider=='deepseek':
        store=ModelStore(ROOT/'output/ReadMaster-Client-Windows-x64/data')
        profile=next(p for p in store.public()['profiles'] if p['provider']=='api'
                     and p.get('endpoint','').rstrip('/')=='https://api.deepseek.com/v1' and p['has_key'])
        config,key=store.snapshot(profile['id'])
    else:
        config,key={'provider':'codex','model':'','max_revisions':2,'timeout':240},''
    doc=extract_pdf(folder/'original.pdf')
    frozen=copy.deepcopy([(b['id'],b['source'],b['sentences'],b['bbox']) for b in doc['blocks']])
    doc.update(id=ident,name='Live default scientific integration fixture',terms=[],events=[],
               state='processing',progress={'page':1},updated=time.time(),error='')
    doc['task']=progress.begin(doc['blocks'],1,1,args.provider)
    server.DATA=data
    server.DOCS[ident]=doc
    server.STOPS[ident]=threading.Event()
    server.ACTIVE_TASKS.add(ident)
    started=time.monotonic()
    server.process_document(ident,1,1,model_config=config,model_key=key)
    report={'provider':args.provider,'default_engine_without_override':True,'source':'synthetic PDF',
            'fresh_model_translation':True,'controller_state':doc['state'],'error':doc.get('error','')}
    report['frozen_source_ids_and_boxes_unchanged']=frozen==[(b['id'],b['source'],b['sentences'],b['bbox']) for b in doc['blocks']]
    if doc['state']=='complete' and all(b['status']=='accepted' for b in doc['blocks'] if b['sentences']):
        result=export_document(doc,folder,ROOT)
        report.update(pdf_export=result,terms=len(doc['terms']),
                      checkpoint_statuses=[c['status'] for c in doc['translation_checkpoints']],
                      passed=report['frozen_source_ids_and_boxes_unchanged'])
    else:
        report['passed']=False
    report['elapsed_seconds']=round(time.monotonic()-started,2)
    (data/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('provider','controller_state','passed','elapsed_seconds')}),flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':
    main()
