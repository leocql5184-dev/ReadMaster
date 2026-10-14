"""Real translation/QE/revision/export in an isolated synthetic paper library.

--prepare-only exercises the live translation adapter without claiming real QE.
The full mode requires the operator's authorized COMET models and LLM connection.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from readmaster import server, progress
from readmaster.babeldoc_agent import BabelDOCTranslationAgent
from readmaster.babeldoc_export import export_document
from readmaster.models import ModelStore
from readmaster.pdf import extract_pdf
from readmaster.quality import translate_batch, snapshot, PAPER_CHECKLIST, paper_checks
from readmaster.pipeline import review_schema, translation_schema, validate_translations, validate_review


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--diagnose-only', action='store_true', help='Real LLM diagnosis/repair, explicitly without metric scores')
    parser.add_argument('--provider', choices=('api','codex'), default='api')
    parser.add_argument('--model', help='Model override for this isolated verification only')
    parser.add_argument('--paper-document', type=Path, help='Existing document.json; verify an isolated full-paper copy')
    args = parser.parse_args()
    name = 'comet-live-verification' if args.provider=='api' else 'comet-codex-verification'
    if args.paper_document:
        name += '-full-paper'
    data = ROOT/'.build'/name
    data.mkdir(parents=True, exist_ok=True)
    source = data/'scientific-control.pdf'
    if args.paper_document:
        original = args.paper_document.resolve()
        source.write_bytes((original.parent/'original.pdf').read_bytes())
    elif not source.exists():
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=500, height=360)
            page.insert_textbox(pymupdf.Rect(45, 45, 455, 140),
                'The method does not require labeled data. The model achieved an accuracy of 92% on the test set.', fontsize=11)
            page.insert_textbox(pymupdf.Rect(45, 160, 455, 270),
                'Each experiment used a batch size of 32. Training took 15 ms per step.', fontsize=11)
            pdf.save(source)
    ident = hashlib.sha256(source.read_bytes()).hexdigest()[:24]
    folder = data/'documents'/ident
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'original.pdf').write_bytes(source.read_bytes())
    path = folder/'document.json'
    if path.exists():
        doc = json.loads(path.read_text(encoding='utf-8'))
    elif args.paper_document:
        doc = copy.deepcopy(json.loads(original.read_text(encoding='utf-8')))
        doc.update(id=ident, state='ready', events=[], error='', updated=time.time())
        for field in ('quality', 'task', 'pdf_export'):
            doc.pop(field, None)
    else:
        doc = extract_pdf(source)
        doc.update(id=ident, name='Synthetic scientific evaluation control', state='ready',
            terms=[{'source':'labeled data','target':'标注数据','definition':'带标签的数据'},
                   {'source':'batch size','target':'批大小','definition':'每批样本数量'}],
            progress={'page':1}, events=[], error='', updated=time.time())
    store = ModelStore(ROOT/'output/ReadMaster-Client-Windows-x64/data')
    profiles = store.public()['profiles']
    profile = next((p for p in profiles if p['id'] == store.state.get('active_id') and p.get('has_key')), None)
    if profile is None:
        profile = next((p for p in profiles if p['provider'] == 'api' and p.get('has_key')), None)
    if profile is None and args.provider=='api':
        raise RuntimeError('Configure a live model connection first')
    if args.provider=='codex':
        config, key = store.snapshot('codex')
    else:
        config, key = store.snapshot(profile['id'])
    if args.model:
        config['model'] = args.model
    stop = threading.Event()
    server.DATA, server.DOCS, server.STOPS = data, {ident:doc}, {ident:stop}
    sentences = [s for b in doc['blocks'] for s in b['sentences']]
    doc['task'] = progress.begin(doc['blocks'], 1, len(doc['pages']), config['model'])
    report_path = data/('llm-report.json' if args.diagnose_only else 'prepare-report.json' if args.prepare_only else 'report.json')
    report = {'llm_model':config['model'], 'source':str(source), 'document_id':ident,
              'metrics_mocked':False, 'paper_is_synthetic':not bool(args.paper_document), 'prepared':False,
              'real_qe_passed':False, 'export_passed':False}
    try:
        def make_agent():
            return (server.CodexAgent(folder/'runs',config,server.codex_binary(),stop.is_set)
                    if config['provider']=='codex' else server.ApiAgent(config,key,stop.is_set))
        if any(not b['translations'].get(s['id']) for b in doc['blocks'] for s in b['sentences']):
            agent = server.CachedAgent(BabelDOCTranslationAgent(make_agent(), ROOT),
                                       folder/'cache', config)
            result = translate_batch(agent, sentences, '\n\n'.join(b['source'] for b in doc['blocks']), doc['terms'])
            for b in doc['blocks']:
                b['translations'] = {s['id']:result['translations'][s['id']] for s in b['sentences']}
                if b['sentences']:
                    b['status'] = 'needs_review'
            # Deliberate test-only semantic fault. Numeric/units remain correct.
            if not args.paper_document:
                faulty = next(s for s in sentences if 'does not require' in s['source'])
                for b in doc['blocks']:
                    if faulty['id'] in b['translations']:
                        b['translations'][faulty['id']] = '该方法需要标注数据。'
                report['initial_fault_injected'] = True
            server.save(doc)
        report['prepared'] = True
        report['initial_translation'] = {s['id']:b['translations'][s['id']]
                                        for b in doc['blocks'] for s in b['sentences']}
        if args.prepare_only:
            print(json.dumps({'prepared':True, 'sentences':len(sentences), 'real_qe_tested':False}), flush=True)
            return
        if args.diagnose_only:
            current = report['initial_translation']
            agent = server.CachedAgent(BabelDOCTranslationAgent(make_agent(), ROOT),
                                       folder/'diagnostic-cache', config)
            payload = {'sentences':sentences, 'translations':[{'id':s['id'],'text':current[s['id']]} for s in sentences],
                       'document':snapshot(doc['blocks'], current), 'terms':doc['terms'],
                       'checklist':PAPER_CHECKLIST, 'xcomet_findings':[],
                       'instruction':'这是独立的真实 LLM 诊断测试，未运行 COMET，不能编造分数。核对全文忠实性并给出有原文证据的修改意见。'}
            review = validate_review(agent.call('evaluate', payload, review_schema()), sentences)
            issues = [i for i in review['issues'] if i['severity']=='blocking']
            faulty = next(s for s in sentences if 'does not require' in s['source'])
            if faulty['id'] not in {i['id'] for i in issues}:
                raise RuntimeError('The real evaluator missed the injected negation fault')
            selected = [s for s in sentences if s['id'] in {i['id'] for i in issues}]
            repair = validate_translations(agent.call('revise', {**payload, 'sentences':selected,
                'context':json.dumps(payload['document'],ensure_ascii=False), 'issues':issues}, translation_schema()), selected)
            final = {**current, **repair}
            if '不' not in final[faulty['id']] and '无需' not in final[faulty['id']]:
                raise RuntimeError('The real translator did not restore the negation')
            if paper_checks(doc['blocks'], final):
                raise RuntimeError('The repaired translation did not retain paper invariants')
            report.update(llm_diagnosis_passed=True, llm_repair_passed=True, review=review, repair=repair)
            print(json.dumps({'real_llm_diagnosis':True,'real_llm_repair':True,'real_qe_tested':False}),flush=True)
            return
        doc['state'] = 'processing'
        server.process_document(ident, 1, len(doc['pages']), model_config=config, model_key=key)
        report['state'] = doc['state']
        report['quality'] = doc.get('quality')
        if doc.get('quality', {}).get('status') != 'accepted':
            raise RuntimeError(doc.get('error') or doc.get('quality',{}).get('reason') or 'Real QE did not pass')
        report['real_qe_passed'] = True
        final = {s['id']:b['translations'][s['id']] for b in doc['blocks'] for s in b['sentences']}
        if not args.paper_document and '不' not in next(final[s['id']] for s in sentences if 'does not require' in s['source']):
            raise RuntimeError('The deliberately removed negation was not repaired')
        exported = export_document(doc, folder, ROOT)
        report['export'] = exported
        report['export_passed'] = True
        print(json.dumps({'passed':True, 'minimum_score':doc['quality']['minimum_score'],
                          'revisions':doc['quality']['revisions'], 'pdf_export':True}), flush=True)
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
