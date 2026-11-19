"""EduMirror paired full-pipeline experiment, fixed initial text and two rounds.

Only the configured Codex translation/review calls use the network. COMET
scores and all stored comparison artifacts are local. Resumable by call cache.
"""
import copy
import csv
import difflib
import hashlib
import html
import json
import os
import re
from pathlib import Path
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from readmaster.models import ModelStore
from readmaster.agents import CodexAgent
from readmaster.babeldoc_agent import BabelDOCTranslationAgent
from readmaster.server import CachedAgent, codex_binary
from readmaster.checkpoints import paragraph_batches
from readmaster.pipeline import glossary_schema
from readmaster.quality import CometBackend, translate_batch, evaluate_document, units_for, score_summary
from readmaster.comet_service import WORKER
from scripts.compare_xcomet_paper import BoundedAgent, RevisionBudget

OUT = ROOT/'.build/edumirror-dual-comparison'
SOURCE = Path(os.environ['READMASTER_TEST_PDF']) if os.environ.get('READMASTER_TEST_PDF') else None
MODELS = {'kiwi':('Unbabel/wmt22-cometkiwi-da','1ad785194e391eebc6c53e2d0776cada8f83179a'),
          'xcomet':('Unbabel/XCOMET-XL','6a123c5e8e6dccab25e5fcffa3c8b417abadb462')}


class CompactCodexAgent(CodexAgent):
    """Remove duplicated wire fields, keeping each sentence and its translation.

    CachedAgent still validates against the full original payload. This same
    lossless representation is used in both arms, for evaluate/revise/accept.
    """
    def call(self,role,payload,schema):
        wire=copy.deepcopy(payload)
        data=wire.get('reader_data',wire)
        if 'document' in data:
            for block in data['document']['blocks']:
                if block['preserved']:
                    assert block['translation']==block['source']
                    block.pop('translation')
                    block.pop('sentences')
                else:
                    assert block['translation']==''.join(s['translation'] for s in block['sentences'])
                    block.pop('translation')
                    # The source block is reconstructable from the unchanged
                    # sentence list, except meaningful math/layout evidence.
                    if block.get('verified_numeric_source')==block['source']:
                        block.pop('verified_numeric_source')
                    block.pop('source')
            data.pop('translations',None)
            data['context_representation']='document.blocks[].sentences contains every fixed sentence id, original source and current translation; preserved blocks retain original source. Top-level sentences identifies the requested ids. Concatenate in listed order for paragraph context.'
        return super().call(role,wire,schema)


def save(name,value):
    path = OUT/name
    path.parent.mkdir(parents=True,exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temp.replace(path)


def read(name):
    return json.loads((OUT/name).read_text(encoding='utf-8'))


def log(message):
    print(time.strftime('%H:%M:%S')+' '+message,flush=True)


def backend(branch):
    value = CometBackend(ROOT,OUT/'qe-cache')
    if branch == 'fp32':
        for kind,(model,revision) in MODELS.items():
            value.models[kind],value.revisions[kind] = model,revision
    else:
        for kind in MODELS:
            assert value.models[kind].endswith('manifest.json'), (kind,value.models[kind])
    return value


def score(branch,blocks,texts,label):
    path = f'cross-scores/{label}-{branch}.json'
    if not (OUT/path).exists():
        units = units_for(blocks,texts)
        predictions = backend(branch).predict('kiwi',units)
        save(path,{u['id']:p['score'] for u,p in zip(units,predictions)})
    return read(path)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    os.environ['OMP_NUM_THREADS']='6'
    os.environ['MKL_NUM_THREADS']='6'
    os.environ['READMASTER_COMET_DEVICE']='cpu'
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    for kind in MODELS:
        os.environ.pop('READMASTER_'+kind.upper()+'_MODEL',None)
    if not (OUT/'original.pdf').exists():
        if SOURCE is None or not SOURCE.is_file():
            raise ValueError('Set READMASTER_TEST_PDF to the authorized experiment PDF before running.')
        shutil.copy2(SOURCE,OUT/'original.pdf')
    config,_ = ModelStore(ROOT/'data').snapshot('codex')
    config = {**config,'timeout':900,'retry_attempts':2}
    def agent(name):
        raw = CompactCodexAgent(OUT/name/'runs',config,codex_binary(),notify=log)
        return CachedAgent(BabelDOCTranslationAgent(raw,ROOT),OUT/name/'cache',config)
    doc = read('document.json')
    if not doc.get('experiment_prepared'):
        suppressed = []
        for b in doc['blocks']:
            if b['page']>1 and b['source']==doc['name'] and b['bbox'][1]<80:
                suppressed.append(b['id'])
                b.update(sentences=[],kind='preserved',status='preserved',preserved_reason='running_header')
        doc['experiment_prepared']=True
        doc['suppressed_running_headers']=suppressed
        save('document.json',doc)
    # PDF extraction can omit spaces after periods or combine long enumerated
    # captions. Split at actual sentence/clause boundaries before either arm;
    # never truncate a COMET input or align arbitrary source/target slices.
    if not doc.get('long_units_split'):
        edits=[]
        for block in doc['blocks']:
            if not any(len(s['source'])>800 for s in block['sentences']):
                continue
            sentences_new=[]
            for sentence in block['sentences']:
                source=sentence['source']
                cuts=[0]+[m.end() for m in re.finditer(r'(?<=[.!?])(?=[A-Z])|(?<=;)\s+',source)]+[len(source)] if len(source)>800 else [0,len(source)]
                for start,end in zip(cuts,cuts[1:]):
                    text=source[start:end].strip()
                    if text:
                        offset=sentence['start']+start+len(source[start:end])-len(source[start:end].lstrip())
                        sentences_new.append(dict(id=block['id']+f'_{len(sentences_new)+1:03}',source=text,start=offset,end=offset+len(text)))
            edits.append(dict(block=block['id'],before=len(block['sentences']),after=len(sentences_new)))
            block['sentences']=sentences_new
        doc['long_units_split']=edits
        save('document.json',doc)
    blocks = doc['blocks']
    sentences = [s for b in blocks for s in b['sentences']]
    if not (OUT/'initial.json').exists():
        save('experiment.json',dict(paper=doc['name'],pages=len(doc['pages']),sentences=len(sentences),
             source_sha256=hashlib.sha256((OUT/'original.pdf').read_bytes()).hexdigest(),
             requested_llm_model=config.get('model') or 'Codex current default, not explicitly pinned',
             service='https://chatgpt.com/backend-api/codex',shared_initial=True,max_revision_rounds=2,
             baseline_models=MODELS,quantized_models=backend('int8').models,
             quantized_revisions=backend('int8').revisions,
             method='Same frozen extraction/glossary/initial translation; both metrics switched together. No diversity search. Both Kiwi scorers score all final outputs.',
             exclusions='Images, table cells, standalone formulae, reference list, short ineligible blocks and running headers are preserved by the reader. Appendix prose included.',
             shared_segmentation=doc['long_units_split'],
             caveat='One stochastic run per arm; cannot assign every wording difference causally to quantization.'))
    initial_agent = agent('translation')
    if not doc['terms']:
        log('Shared glossary')
        source = '\n\n'.join(b['source'] for b in blocks if b['sentences'])
        terms = initial_agent.call('terms',{'source':source[:16000]},glossary_schema()).get('terms',[])
        doc['terms'] = [t for t in terms if isinstance(t,dict) and t.get('source') and t['source'] in source and all(isinstance(t.get(k),str) for k in ('target','definition'))]
        save('document.json',doc)
    terms = doc['terms']
    groups = list(paragraph_batches([b for b in blocks if b['sentences']]))
    def translate(index,group):
        path = f'translation/batch-{index:03}.json'
        selected = [s for b in group for s in b['sentences']]
        if (OUT/path).exists():
            cached=read(path)
            if set(cached['translations'])=={s['id'] for s in selected}:
                return index,cached
        positions = [blocks.index(b) for b in group]
        nearby = blocks[max(0,min(positions)-1):max(positions)+2]
        context = '\n\n'.join(b['id']+': '+b['source'] for b in nearby)
        log(f'Translating batch {index+1}/{len(groups)}, {len(selected)} units')
        result = translate_batch(initial_agent,selected,context,terms)
        save(path,result)
        log(f'Saved translation batch {index+1}/{len(groups)}')
        return index,result
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(translate,index,group) for index,group in enumerate(groups)]
        for future in as_completed(futures):
            index,result = future.result()
            for b in groups[index]:
                b['translations'] = {s['id']:result['translations'][s['id']] for s in b['sentences']}
    initial = {s['id']:b['translations'][s['id']] for b in blocks for s in b['sentences']}
    save('initial.json',initial)
    save('initial-document.json',doc)
    log(f'Shared initial translation complete: {len(initial)} units')
    for arm in ('fp32','int8'):
        log('Initial scoring with '+arm+' Kiwi')
        score(arm,blocks,initial,'initial')
    for arm in ('fp32','int8'):
        if (OUT/f'{arm}/result.json').exists():
            continue
        arm_blocks = copy.deepcopy(blocks)
        metric = backend(arm)
        actual = agent(arm)
        bounded = BoundedAgent(actual)
        latest = {}
        def checkpoint(current,result):
            latest.clear()
            latest.update(copy.deepcopy(result))
            save(f'{arm}/checkpoint.json',result)
        start = time.monotonic()
        log(arm+': starting dual-model review/revision, at most two rounds')
        try:
            result = evaluate_document(bounded,arm_blocks,terms,metric,checkpoint=checkpoint,
                                       notify=lambda s:log(arm+': '+s),diversify=False)
        except RevisionBudget:
            result = latest
            result.update(status='needs_review',reason='Two-revision experiment limit; not declared accepted.')
        result.update(models=metric.models,revisions_used=metric.revisions,experiment_seconds=time.monotonic()-start)
        save(f'{arm}/result.json',result)
        log(arm+': final output saved')
    for label,text in [('initial',initial)]+[(arm,read(f'{arm}/result.json')['translations']) for arm in ('fp32','int8')]:
        for scorer in ('fp32','int8'):
            log(f'Cross scoring {label} using {scorer}')
            score(scorer,blocks,text,label)
    WORKER.close()
    make_comparison()
    log('PIPELINE COMPLETE; all differences ready for semantic audit')


def make_comparison():
    doc = read('initial-document.json')
    texts = {'initial':read('initial.json'),**{a:read(f'{a}/result.json')['translations'] for a in ('fp32','int8')}}
    scores = {label:{m:read(f'cross-scores/{label}-{m}.json') for m in ('fp32','int8')} for label in texts}
    records,differences = [],[]
    unit_owner = {sid:u['id'] for u in units_for(doc['blocks'],texts['initial']) for sid in u['ids']}
    for b in doc['blocks']:
        for s in b['sentences']:
            sid = s['id']
            row = dict(id=sid,page=b['page'],source=s['source'],**{a:t[sid] for a,t in texts.items()})
            row['score_unit'] = unit_owner[sid]
            row.update({a+'_'+m:sc[unit_owner[sid]] for a,by_model in scores.items() for m,sc in by_model.items()})
            records.append(row)
            if row['fp32'] != row['int8']:
                row['whitespace_only'] = ''.join(row['fp32'].split())==''.join(row['int8'].split())
                row['char_edits'] = [dict(operation=tag,fp32=row['fp32'][a:b],int8=row['int8'][c:d],fp32_range=[a,b],int8_range=[c,d]) for tag,a,b,c,d in difflib.SequenceMatcher(None,row['fp32'],row['int8'],autojunk=False).get_opcodes() if tag!='equal']
                differences.append(row)
    save('all-sentences.json',records)
    save('differences.json',differences)
    summaries = {label:{m:score_summary(sc) for m,sc in by_model.items()} for label,by_model in scores.items()}
    save('summary.json',dict(experiment=read('experiment.json'),summaries=summaries,
        differences=len(differences),whitespace_only=sum(r['whitespace_only'] for r in differences),
        changed_from_initial={a:sum(t[k]!=texts['initial'][k] for k in t) for a,t in texts.items()},
        status={a:read(f'{a}/result.json')['status'] for a in ('fp32','int8')}))
    fields = ['id','page','score_unit','source','initial','fp32','int8']+[a+'_'+m for a in texts for m in ('fp32','int8')]
    with (OUT/'scores.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(records)


if __name__ == '__main__':
    try:
        main()
    finally:
        WORKER.close()
