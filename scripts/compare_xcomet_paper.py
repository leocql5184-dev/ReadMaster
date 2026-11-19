"""One-paper paired experiment: shared fresh translation, FP32 vs mixed INT8.

Uses the configured Codex connection and unchanged application QE/revision logic.
All data and model responses stay in an isolated .build directory. Resumable.
"""
import copy
import csv
import html
import json
import os
from pathlib import Path
import shutil
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from readmaster.models import ModelStore
from readmaster.agents import CodexAgent
from readmaster.babeldoc_agent import BabelDOCTranslationAgent
from readmaster.server import CachedAgent, codex_binary
from readmaster.checkpoints import paragraph_batches
from readmaster.pdf import extract_pdf
from readmaster.pipeline import glossary_schema, review_schema, validate_review
from readmaster.quality import (CometBackend, translate_batch, evaluate_document, units_for,
                               score_summary, snapshot, PAPER_CHECKLIST, paper_checks)
from readmaster.comet_service import WORKER

OUT=ROOT/'.build/xcomet-paper-comparison'
SOURCE=ROOT/'data/documents/81ee7469d062ed41fbc9833a/original.pdf'


def save(name,value):
    path=OUT/name
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def read(name):
    return json.loads((OUT/name).read_text(encoding='utf-8'))


def log(message):
    print(message,flush=True)
    save('progress.json',dict(time=time.time(),message=message))


class RevisionBudget(Exception):
    pass


class BoundedAgent:
    def __init__(self,inner):
        self.inner=inner
        self.revisions=0

    def call(self,role,payload,schema):
        if role=='revise':
            if self.revisions>=2:
                raise RevisionBudget()
            self.revisions+=1
        return self.inner.call(role,payload,schema)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    config,_=ModelStore(ROOT/'data').snapshot('codex')
    config={**config,'timeout':600,'retry_attempts':2}
    def agent(name):
        raw=CodexAgent(OUT/name/'runs',config,codex_binary(),notify=log)
        return CachedAgent(BabelDOCTranslationAgent(raw,ROOT),OUT/name/'cache',config)
    if not (OUT/'document.json').exists():
        log('Extracting fresh paper text')
        shutil.copy2(SOURCE,OUT/'original.pdf')
        doc=extract_pdf(OUT/'original.pdf')
        doc.update(name='Learning What Interventions Reveal: Relation-Aware Response Models for Auditable Cognitive Guidance',terms=[])
        for block in doc['blocks']:
            block['translations']={}
        save('document.json',doc)
    doc=read('document.json')
    blocks=doc['blocks']
    sentences=[s for b in blocks for s in b['sentences']]
    full_source='\n\n'.join(b['source'] for b in blocks)
    initial_agent=agent('translation')
    if not doc['terms']:
        log('Extracting shared terminology')
        terms=initial_agent.call('terms',{'source':full_source[:16000]},glossary_schema()).get('terms',[])
        doc['terms']=[t for t in terms if isinstance(t,dict) and t.get('source') and t['source'] in full_source and all(isinstance(t.get(k),str) for k in ('target','definition'))]
        save('document.json',doc)
    terms=doc['terms']
    groups=list(paragraph_batches([b for b in blocks if b['sentences']]))
    for index,group in enumerate(groups):
        batch_path=f'translation/batch-{index:03}.json'
        if (OUT/batch_path).exists():
            result=read(batch_path)
        else:
            log(f'Fresh translation batch {index+1}/{len(groups)}')
            selected=[s for b in group for s in b['sentences']]
            positions=[blocks.index(b) for b in group]
            nearby=blocks[max(0,min(positions)-1):max(positions)+2]
            context='\n\n'.join(b['id']+': '+b['source'] for b in nearby)
            result=translate_batch(initial_agent,selected,context,terms)
            save(batch_path,result)
        for b in group:
            b['translations']={s['id']:result['translations'][s['id']] for s in b['sentences']}
    initial={s['id']:b['translations'][s['id']] for b in blocks for s in b['sentences']}
    save('initial.json',initial)
    save('initial-document.json',doc)
    log(f'Fresh translation saved: {len(sentences)} sentence fragments, {len(doc["pages"])} pages')
    # Pin the comparison's CPU threading even when crossing between FP32 and INT8.
    # The production INT8 worker already enforces six threads; env makes the
    # FP32 subprocess use the same CPU thread budget.
    os.environ['OMP_NUM_THREADS']='6'
    os.environ['MKL_NUM_THREADS']='6'
    # Keep the evaluator fixed even when a verified Kiwi INT8 bundle is installed.
    os.environ['READMASTER_KIWI_MODEL']='Unbabel/wmt22-cometkiwi-da'
    os.environ['READMASTER_KIWI_REVISION']='1ad785194e391eebc6c53e2d0776cada8f83179a'
    baseline_backend=CometBackend(ROOT,OUT/'qe-cache')
    if not (OUT/'scores-initial.json').exists():
        log('COMETKiwi scoring the shared initial translation')
        units=units_for(blocks,initial)
        scores=baseline_backend.predict('kiwi',units)
        save('scores-initial.json',{u['id']:p['score'] for u,p in zip(units,scores)})
    def independent(name,current):
        path=f'{name}/independent-review.json'
        if not (OUT/path).exists():
            log(f'{name}: separate semantic review, without metric scores or XCOMET findings')
            reviewer=agent(name+'-blind-review')
            payload=dict(sentences=sentences,translations=[{'id':s['id'],'text':current[s['id']]} for s in sentences],
                         document=snapshot(blocks,current),terms=terms,checklist=PAPER_CHECKLIST)
            review=validate_review(reviewer.call('accept',payload,review_schema()),sentences)
            save(path,review)
        return read(path)
    independent('initial',initial)
    for branch in ('fp32','int8'):
        path=f'{branch}/result.json'
        if (OUT/path).exists():
            continue
        branch_blocks=copy.deepcopy(blocks)
        backend=CometBackend(ROOT,OUT/'qe-cache')
        if branch=='fp32':
            backend.models['xcomet']='Unbabel/XCOMET-XL'
            backend.revisions['xcomet']='6a123c5e8e6dccab25e5fcffa3c8b417abadb462'
        else:
            assert backend.models['xcomet'].endswith('manifest.json'), 'INT8 bundle not selected'
        actual=agent(branch)
        bounded=BoundedAgent(actual)
        latest={}
        started=time.monotonic()
        def checkpoint(current,result):
            latest.clear()
            latest.update(copy.deepcopy(result))
            save(f'{branch}/checkpoint.json',result)
        log(f'{branch}: same initial translation, maximum two revision rounds')
        try:
            result=evaluate_document(bounded,branch_blocks,terms,backend,checkpoint=checkpoint,
                                     notify=lambda s:log(branch+': '+s),diversify=False)
        except RevisionBudget:
            result=latest
            result.update(status='needs_review',reason='Reached experiment limit of two revision rounds; not declared accepted.')
        final=result['translations']
        units=units_for(blocks,final)
        scores=backend.predict('kiwi',units)
        result.update(scores={u['id']:p['score'] for u,p in zip(units,scores)},
                      experiment_seconds=time.monotonic()-started,live_calls=actual.live_calls,
                      xcomet_model=backend.models['xcomet'])
        result.update(score_summary(result['scores']))
        save(path,result)
    WORKER.close()
    for branch in ('fp32','int8'):
        independent(branch,read(f'{branch}/result.json')['translations'])
    report(doc,config)
    log('COMPLETE: '+str(OUT/'REPORT.md'))


def report(doc,config):
    blocks=doc['blocks']
    initial=read('initial.json')
    arms={b:read(f'{b}/result.json') for b in ('fp32','int8')}
    texts={'initial':initial,**{b:v['translations'] for b,v in arms.items()}}
    scores={'initial':read('scores-initial.json'),**{b:v['scores'] for b,v in arms.items()}}
    assert set(scores['initial'])==set(scores['fp32'])==set(scores['int8'])
    summaries={}
    for name,current in texts.items():
        review=read(f'{name}/independent-review.json')
        summaries[name]={**score_summary(scores[name]),'median':statistics.median(scores[name].values()),
                         'rule_findings':len(paper_checks(blocks,current)),
                         'independent_blockers':sum(x['severity']=='blocking' for x in review['issues']),
                         'independent_warnings':sum(x['severity']=='warning' for x in review['issues']),
                         'changed_sentences':sum(current[k]!=initial[k] for k in initial)}
    summary=dict(paper=doc['name'],pages=len(doc['pages']),sentences=len(initial),provider=config['provider'],
                 requested_llm_model=config.get('model') or 'Codex CLI default (not explicitly pinned)',
                 max_revision_rounds=2,summaries=summaries,
                 limitations=['One paper, one run per branch; stochastic model variation is not controlled by repeat trials.',
                              'Kiwi is also the optimization gate: its gain is not independent proof of quality.',
                              'Review payload omits metric scores and XCOMET findings, but run paths expose branch names; not a strict blind review or human gold standard.',
                              'Comparison isolates XCOMET variant, not the benefit of COMET vs no COMET.'])
    save('summary.json',summary)
    lines=['# XCOMET 原版与量化版：单篇论文对照','',doc['name'],'',
           '同一份新初译、相同术语表和审校服务，每条路线最多两轮修订；COMETKiwi 始终为原版。','',
           '| 指标 | 初译 | 原版 XCOMET 审校后 | 量化 XCOMET 审校后 |','|---|---:|---:|---:|']
    for key,label in [('document_score','Kiwi 平均分'),('median','Kiwi 中位数'),('minimum_score','Kiwi 最低分'),('above_threshold_percentage','超过 0.7 的比例 (%)'),('below_floor_count','低于 0.5 的单元数'),('independent_blockers','独立 LLM 复核阻塞意见数'),('rule_findings','程序规则检查意见数'),('changed_sentences','相对初译改动句块数')]:
        lines.append('| '+label+' | '+' | '.join(f'{summaries[n][key]:.4f}' if isinstance(summaries[n][key],float) else str(summaries[n][key]) for n in ('initial','fp32','int8'))+' |')
    lines+=['','## 运行状态','']
    for name,result in arms.items():
        lines.append(f"- {name}：{result['status']}；{result['reason']}；修订 {result['revisions']} 轮，审校修订阶段 {result['experiment_seconds']:.1f} 秒。")
    lines+=['','## 解释边界','',
            'Kiwi 同时参与修订目标，因此分数上升不是独立的质量证明。语义复核的论文输入不含 Kiwi 分数或 XCOMET 线索，但程序运行路径包含分支名称，不属于严格盲测；复核仍是同一模型服务，不代替人工判断。单篇、每条路线一次运行，也不能区分量化影响与大模型随机性。',
            'Codex 使用账号当前默认模型，未显式锁定模型名称。运行时间包含大模型服务等待、加载和推理，不能据此推断纯 XCOMET 的加速倍数。',
            '实验达到两轮上限时会如实保留 needs_review，不会把未通过验收的译文标为合格。',
            '现有图表、独立公式和参考文献按阅读器规则保留原文；报告比较可翻译正文单元。',
            '', '完整原文、三份译文和逐单元分数见 `comparison.html`；原始模型响应、每轮记录和复核意见保存在各分支目录。逐项文本核对见 `text-audit.md`。','']
    different=[k for k in initial if texts['fp32'][k]!=texts['int8'][k]]
    whitespace=sum(''.join(texts['fp32'][k].split())==''.join(texts['int8'][k].split()) for k in different)
    lines += ['## 具体差异与判断','',
              f"两条路线最终有 {len(different)} 个句块不同，其中 {whitespace} 个仅为空白字符差异。量化路线 Kiwi 平均分比原版路线低 {summaries['fp32']['document_score']-summaries['int8']['document_score']:.4f}。",
              '两条路线均补回了初译遗漏的 are crossed with（交叉组合）含义，并将部分 clean condition 的译法从“无污染”调整为更符合本文语境的“无失配”。原版路线还进行了更多空格、措辞和跨句块表达重排。',
              '初译五条程序规则报警经文本核对是数字书写形式或括号形式差异，不能计为五个实质误译。三份译文的自动语义复核均没有阻塞意见，但初译中仍可找到上述遗漏，说明这次自动复核不是可靠的质量金标准。',
              '本次没有发现量化路线明显的新增实质误译，但这只是本篇的文本核对结果，不能证明量化无损，也不能证明 COMET 组件整体优于不用 COMET。','']
    if (OUT/'joined-diagnostics.json').exists():
        diag=read('joined-diagnostics.json')
        lines+=['## 两处断句诊断（不替代全文平均分）','',
                '对两处跨页/跨块残句合并原文和译文后，使用同一原版 Kiwi 重新评分；只用于解释低分来源。', '',
                '| 输入 | 初译 | 原版路线 | 量化路线 |','|---|---:|---:|---:|']
        for case in ('cross_page_word','controller_condition'):
            vals={r['branch']:r['kiwi'] for r in diag['rows'] if r['case']==case}
            lines.append('| '+case+' | '+' | '.join(f'{vals[n]:.4f}' for n in ('initial','fp32','int8'))+' |')
    (OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    units=units_for(blocks,initial)
    unit_texts={name:{u['id']:u['mt'] for u in units_for(blocks,current)} for name,current in texts.items()}
    records=[]
    for u in units:
        records.append({'id':u['id'],'source':u['src'],**{name:unit_texts[name][u['id']] for name in texts},
                        **{name+'_kiwi':scores[name][u['id']] for name in texts}})
    with (OUT/'scores.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    esc=lambda x:html.escape(str(x))
    body=''.join('<tr><td>'+esc(r['id'])+'<p>'+esc(r['source'])+'</p></td>'+''.join('<td><b>'+f'{r[n+"_kiwi"]:.4f}'+'</b><p>'+esc(r[n])+'</p></td>' for n in texts)+'</tr>' for r in records)
    (OUT/'comparison.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>XCOMET 论文对照</title><style>body{font:16px/1.6 system-ui;margin:24px;color:#222}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:12px;vertical-align:top;width:25%;overflow-wrap:anywhere}th{position:sticky;top:0;background:#eef3f6}b{color:#30546c}p{white-space:pre-wrap}</style><h1>'+esc(doc['name'])+'</h1><p>分数均由未量化的 COMETKiwi 实际推理获得。分数不等于人工质量结论。</p><table><thead><tr><th>原文</th><th>初译</th><th>原版 XCOMET 审校后</th><th>量化 XCOMET 审校后</th></tr></thead><tbody>'+body+'</tbody></table></html>',encoding='utf-8')


if __name__=='__main__':
    try:
        main()
    finally:
        WORKER.close()
