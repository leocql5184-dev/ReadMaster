"""Real Codex tests, with outputs persisted by the running app, not mocked."""
import json
from pathlib import Path
import time
import urllib.request

BASE='http://127.0.0.1:8766'
ID='f285b0971ae4a790e402fb93'

def request(path,body=None):
    req=urllib.request.Request(BASE+path,None if body is None else json.dumps(body).encode(),{'Content-Type':'application/json','X-ReadMaster':'1'})
    with urllib.request.urlopen(req,timeout=30) as result:return json.load(result)

report=[]
for page,label in [(1,'abstract-and-introduction'),(3,'body-and-method'),(14,'appendix-and-table')]:
    doc=request('/api/document/'+ID)
    while doc['state']=='processing':
        time.sleep(3)
        doc=request('/api/document/'+ID)
    request('/api/start',{'id':ID,'start_page':page,'end_page':page})
    deadline=time.monotonic()+1800
    last=None
    while time.monotonic()<deadline:
        doc=request('/api/document/'+ID)
        stage=doc.get('active_role')
        if stage!=last:print(f'{label}: {stage}',flush=True);last=stage
        if doc['state']!='processing':break
        time.sleep(3)
    else:raise RuntimeError('real test exceeded total time budget')
    if doc['state']=='failed':raise RuntimeError(doc['error'])
    blocks=[b for b in doc['blocks'] if b['page']==page and b['sentences']]
    for b in blocks:
        assert set(b['translations'])=={s['id'] for s in b['sentences']}, b['id']
        assert b['status'] in ('accepted','needs_review'), b['id']
    report.append({'set':label,'page':page,'blocks':len(blocks),'sentences':sum(len(b['sentences']) for b in blocks),
                   'accepted':sum(b['status']=='accepted' for b in blocks),'needs_review':sum(b['status']=='needs_review' for b in blocks),
                   'revisions':sum(b.get('revisions',0) for b in blocks)})
    Path('output/live-test-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report[-1]),flush=True)
