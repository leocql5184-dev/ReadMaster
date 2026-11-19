"""Offline XCOMET INT8 sensitivity experiment; never changes production weights.

Run with .venv-comet/Scripts/python.exe scripts/analyze_xcomet_sensitivity.py
Outputs local source/translation excerpts under .build, not a public benchmark.
"""
import ast
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import time
from types import MethodType

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.build/xcomet-sensitivity'
OUT.mkdir(parents=True, exist_ok=True)
os.environ['HF_HUB_CACHE'] = str(ROOT / 'data/comet-models/hub')
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
import torch
from comet_worker import load_checkpoint_mapped, decode_complete_spans, validate_lengths, available_memory


def save(name, value):
    path = OUT / name
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def dataset():
    tree = ast.parse((ROOT / 'scripts/verify_acceptance_prompt.py').read_text(encoding='utf-8'))
    cases = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'CASES' for t in n.targets))
    rows = []
    for category, src, good, bad in cases:
        for faulty, mt in ((False, good), (True, bad)):
            if mt:
                rows.append(dict(id=f'{category}_{"bad" if faulty else "good"}', src=src, mt=mt,
                                 faulty=faulty, category=category, origin='authored_control'))
    # Real document excerpts have NO human gold labels. Only measure output drift.
    candidates, seen = [], set()
    for path in sorted((ROOT / 'data/documents').glob('*/document.json')):
        doc = json.loads(path.read_text(encoding='utf-8'))
        for block in doc.get('blocks', []):
            for sentence in block.get('sentences', []):
                src = sentence.get('source', '')
                mt = block.get('translations', {}).get(sentence['id'], '')
                if not isinstance(mt, str) or not 50 <= len(src) <= 550 or not mt or (src, mt) in seen:
                    continue
                seen.add((src, mt))
                candidates.append(dict(id=f'real_{path.parent.name}_{sentence["id"]}', src=src, mt=mt,
                                       faulty=None, category='real_unlabeled', origin='local_document'))
    # Deterministic broad selection independent of model outputs.
    candidates.sort(key=lambda r: hashlib.sha256((r['src'] + r['mt']).encode()).hexdigest())
    rows.extend(candidates[:12])
    return rows


def span_signature(spans):
    return [(s['start'], s['end'], s['severity']) for s in spans]


def metrics(results, reference, rows):
    delta = [abs(a['score'] - b['score']) for a, b in zip(results, reference)]
    flips = sum(bool(a['spans']) != bool(b['spans']) for a, b in zip(results, reference))
    changed = sum(span_signature(a['spans']) != span_signature(b['spans']) for a, b in zip(results, reference))
    bad = [i for i, r in enumerate(rows) if r['faulty'] is True]
    good = [i for i, r in enumerate(rows) if r['faulty'] is False]
    # Flags are diagnostic proxies, NOT localized true-positive recall.
    return dict(n=len(rows), score_mae=sum(delta)/len(delta), score_max=max(delta),
                span_changed=changed, flag_flips=flips,
                lost_flags=[rows[i]['id'] for i,(a,b) in enumerate(zip(results,reference)) if b['spans'] and not a['spans']],
                added_flags=[rows[i]['id'] for i,(a,b) in enumerate(zip(results,reference)) if a['spans'] and not b['spans']],
                bad_controls_flagged=sum(bool(results[i]['spans']) for i in bad), bad_controls=len(bad),
                good_controls_flagged=sum(bool(results[i]['spans']) for i in good), good_controls=len(good))


def main():
    torch.set_num_threads(6)
    torch.set_num_interop_threads(1)
    torch.manual_seed(0)
    torch.backends.quantized.engine = 'fbgemm'
    checkpoint = ROOT / 'data/comet-models/hub/models--Unbabel--XCOMET-XL/snapshots/6a123c5e8e6dccab25e5fcffa3c8b417abadb462/checkpoints/model.ckpt'
    print('Loading unchanged XCOMET checkpoint', flush=True)
    start = time.monotonic()
    model = load_checkpoint_mapped(checkpoint).eval()
    model.requires_grad_(False)
    model.decode = MethodType(decode_complete_spans, model)
    rows = dataset()
    for row in rows:
        validate_lengths(model, [row])
    save('samples.json', rows)
    batches = [model.prepare_sample([{'src':r['src'], 'mt':r['mt']}], stage='predict') for r in rows]
    linear = {n:m for n,m in model.named_modules() if isinstance(m, torch.nn.Linear)}
    embeddings = {n:m for n,m in model.named_modules() if isinstance(m, torch.nn.Embedding)}
    originals = {**linear, **embeddings}
    inventory = [{'name':n,'type':type(m).__name__,'shape':list(m.weight.shape),
                  'bytes':m.weight.numel()*m.weight.element_size()} for n,m in originals.items()]
    save('inventory.json', inventory)
    report = dict(torch=torch.__version__, platform=platform.platform(), threads=6, engine='fbgemm',
                  checkpoint=str(checkpoint), checkpoint_bytes=checkpoint.stat().st_size,
                  load_seconds=time.monotonic()-start, available_ram_bytes=available_memory(),
                  method='Actual per-channel dynamic INT8 Linear; row-wise weight-only INT8 Embedding; one group at a time plus combined candidates.',
                  caveats=['Small diagnostic set, not held-out benchmark or release validation.',
                           'Real excerpts have no human labels. Flagging a bad sentence is not proof of finding its actual error.',
                           'Cached tokenization and direct predict_step; timing is not end-to-end app latency.',
                           'FP32 originals retained for restoration: process RAM is not deployable INT8 RAM.'],
                  variants=[])
    all_idx = list(range(len(rows)))
    screen = [i for i,r in enumerate(rows) if r['faulty'] is True] + [0,2,4]

    def predict(indices):
        out = []
        with torch.inference_mode():
            for i in indices:
                p = model.predict_step(batches[i])
                out.append(dict(id=rows[i]['id'], score=float(p.scores[0]), spans=p.metadata.error_spans[0]))
        return out

    predict([0])  # warmup
    baseline_start = time.monotonic()
    baseline = predict(all_idx)
    save('baseline.json', baseline)
    report['baseline_seconds'] = time.monotonic()-baseline_start
    report['baseline_metrics'] = metrics(baseline, baseline, rows)
    # Verify the direct path against the application's official predict interface.
    official = model.predict([{'src':rows[0]['src'],'mt':rows[0]['mt']}], batch_size=1,
                             gpus=0, num_workers=0, progress_bar=False)
    report['official_path_score_delta'] = abs(float(official.scores[0])-baseline[0]['score'])
    report['official_path_span_equal'] = span_signature(official.metadata.error_spans[0]) == span_signature(baseline[0]['spans'])
    assert report['official_path_score_delta'] < 1e-6 and report['official_path_span_equal']
    save('report.json', report)
    print('BASELINE '+json.dumps(report['baseline_metrics']), flush=True)

    def replace(name, module):
        parent, _, leaf = name.rpartition('.')
        setattr(model.get_submodule(parent) if parent else model, leaf, module)

    def run(name, names, indices):
        converted = []
        t = time.monotonic()
        packed_bytes = 0
        try:
            for n in names:
                original = originals[n]
                if isinstance(original, torch.nn.Linear):
                    original.qconfig = torch.ao.quantization.per_channel_dynamic_qconfig
                    quant = torch.ao.nn.quantized.dynamic.Linear.from_float(original)
                else:
                    original.qconfig = torch.ao.quantization.float_qparams_weight_only_qconfig
                    quant = torch.ao.nn.quantized.Embedding.from_float(original)
                quant.eval()
                weight = quant.weight()
                packed_bytes += weight.numel()*weight.element_size()
                if weight.qscheme() in (torch.per_channel_affine, torch.per_channel_affine_float_qparams):
                    packed_bytes += weight.q_per_channel_scales().numel()*weight.q_per_channel_scales().element_size()
                    packed_bytes += weight.q_per_channel_zero_points().numel()*weight.q_per_channel_zero_points().element_size()
                replace(n,quant)
                converted.append(n)
            convert_seconds = time.monotonic()-t
            predict([indices[0]])
            t = time.monotonic()
            results = predict(indices)
            duration = time.monotonic()-t
            selected = [rows[i] for i in indices]
            reference = [baseline[i] for i in indices]
            summary = dict(name=name, modules=names, scope='full' if indices==all_idx else 'screen',
                           seconds=duration, convert_seconds=convert_seconds,
                           float_weight_bytes=sum(originals[n].weight.numel()*4 for n in names),
                           quant_weight_and_scale_bytes=packed_bytes, **metrics(results,reference,selected))
            save(name+'.json', dict(summary=summary, results=results))
            report['variants'].append(summary)
            print(json.dumps({k:v for k,v in summary.items() if k!='modules'}),flush=True)
        finally:
            for n in converted:
                replace(n,originals[n])
            gc.collect()
        save('report.json',report)

    layer_prefix = 'encoder.model.encoder.layer.'
    layers = sorted({'.'.join(n.split('.')[:5]) for n in linear if n.startswith(layer_prefix)},key=lambda n:int(n.split('.')[-1]))
    for layer in layers:
        run('layer_'+layer.split('.')[-1], [n for n in linear if n.startswith(layer+'.')], screen)
    heads = [n for n in linear if not n.startswith(layer_prefix)]
    for n in heads:
        run('head_'+n.replace('.','_'),[n],all_idx)
    run('embeddings',list(embeddings),all_idx)
    # Full-set confirmation of the four most sensitive layers from screening.
    ranked = sorted(report['variants'][:len(layers)],key=lambda x:(x['flag_flips'],x['span_changed'],x['score_mae']),reverse=True)
    for entry in ranked[:4]:
        run(entry['name']+'_full',entry['modules'],all_idx)
    run('all_linear',list(linear),all_idx)
    run('all_linear_embeddings',list(originals),all_idx)
    protected = {n for entry in ranked[:4] for n in entry['modules']} | set(heads)
    run('mixed_protect_top4_heads',[n for n in originals if n not in protected],all_idx)
    repeat = predict(all_idx)
    report['restoration_metrics'] = metrics(repeat,baseline,rows)
    assert report['restoration_metrics']['score_max'] < 1e-6
    assert report['restoration_metrics']['span_changed'] == 0
    report['completed'] = True
    save('report.json',report)
    print('COMPLETE '+str(OUT),flush=True)


if __name__ == '__main__':
    main()
