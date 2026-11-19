"""Local metric-only diagnostics after both review arms finish."""
import json
import os
import re
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.compare_dual_comet_paper import OUT, read, save, backend
from readmaster.quality import units_for
from readmaster.comet_service import WORKER


def main():
    os.environ['OMP_NUM_THREADS']='6';os.environ['MKL_NUM_THREADS']='6'
    os.environ['READMASTER_COMET_DEVICE']='cpu'
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    os.environ.pop('READMASTER_KIWI_MODEL',None);os.environ.pop('READMASTER_XCOMET_MODEL',None)
    doc=read('initial-document.json');initial=read('initial.json')
    arms={a:read(f'{a}/result.json') for a in ('fp32','int8')}
    units={u['id']:u for u in units_for(doc['blocks'],initial)}
    first={a:next(h for h in r['history'] if h.get('scores')) for a,r in arms.items()}
    flagged={a:{r['id'] for r in h['xcomet']} for a,h in first.items()}
    ids=sorted(flagged['fp32']|flagged['int8'])
    pairs=[units[i] for i in ids]
    values={}
    for arm in ('fp32','int8'):
        values[arm]=backend(arm).predict('xcomet',pairs)
    sig=lambda r:[(s['start'],s['end'],s['severity']) for s in r['error_spans']]
    comparison=[dict(id=i,source=units[i]['src'],translation=units[i]['mt'],
                     fp32_flagged=i in flagged['fp32'],int8_flagged=i in flagged['int8'],
                     fp32=a,int8=b,spans_changed=sig(a)!=sig(b)) for i,a,b in zip(ids,values['fp32'],values['int8'])]
    save('initial-xcomet-comparison.json',dict(count=len(ids),
         fp32_selected=len(flagged['fp32']),int8_selected=len(flagged['int8']),
         only_fp32=sorted(flagged['fp32']-flagged['int8']),only_int8=sorted(flagged['int8']-flagged['fp32']),
         span_changes=sum(r['spans_changed'] for r in comparison),rows=comparison))
    source={s['id']:s['source'] for b in doc['blocks'] for s in b['sentences']}
    groups=[['p005_b008_006','p005_b010_001','p005_b011_001'],
            ['p003_b066_005','p003_b068_001'],['p008_b022_003','p008_b023_001'],
            ['p020_b007_003','p020_b008_001']]
    joined=[]
    for group in groups:
        text=source[group[0]].strip()
        for sid in group[1:]:
            # Only the observed psycho- / logical hyphenation is removed.
            text=(text[:-1]+source[sid].lstrip() if text.endswith('psycho-') else text+' '+source[sid].lstrip())
        for label,translations in [('initial',initial)]+[(a,r['translations']) for a,r in arms.items()]:
            joined.append(dict(id=' + '.join(group),variant=label,src=text,mt=''.join(translations[s] for s in group)))
    for arm in ('fp32','int8'):
        scores=backend(arm).predict('kiwi',joined)
        for row,pred in zip(joined,scores):
            row[arm+'_kiwi']=pred['score']
    save('joined-diagnostics.json',dict(scope='Four manually identified broken sentences rejoined for diagnosis only; not substituted into full-paper scores.',rows=joined))
    print(json.dumps(dict(initial_union=len(ids),changed_spans=sum(r['spans_changed'] for r in comparison),joined_diagnostics=joined),ensure_ascii=False),flush=True)


if __name__=='__main__':
    try:
        main()
    finally:
        WORKER.close()
