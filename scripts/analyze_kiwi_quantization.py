"""Offline sensitivity study on fixed controls; no translation or network calls.

Select a recipe on the pre-existing 32 diagnostic samples, then evaluate on
the frozen paper translations separately. Original weights are never modified.
"""
import gc
import json
import os
from pathlib import Path
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'.build/kiwi-quantization'
CHECKPOINT = ROOT/'data/comet-models/hub/models--Unbabel--wmt22-cometkiwi-da/snapshots/1ad785194e391eebc6c53e2d0776cada8f83179a/checkpoints/model.ckpt'
os.environ['HF_HUB_CACHE'] = str(ROOT/'data/comet-models/hub')
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'


def save(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT/name
    temp = dest.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(dest)


def drift(scores, baseline):
    import numpy as np
    a, b = np.array(scores), np.array(baseline)
    delta = a-b
    return dict(n=len(a), mean=float(a.mean()), signed_mean_delta=float(delta.mean()),
                mae=float(np.abs(delta).mean()), p95=float(np.quantile(np.abs(delta), .95)),
                max_abs=float(np.abs(delta).max()),
                flips_07=int(((a > .7) != (b > .7)).sum()),
                promoted_07=int(((a > .7) & (b <= .7)).sum()),
                demoted_07=int(((a <= .7) & (b > .7)).sum()),
                flips_05=int(((a < .5) != (b < .5)).sum()))


def convert(module):
    import torch
    if isinstance(module, torch.nn.Linear):
        module.qconfig = torch.ao.quantization.per_channel_dynamic_qconfig
        return torch.ao.nn.quantized.dynamic.Linear.from_float(module).eval()
    module.qconfig = torch.ao.quantization.float_qparams_weight_only_qconfig
    return torch.ao.nn.quantized.Embedding.from_float(module).eval()


def replace(model, name, module):
    parent, _, leaf = name.rpartition('.')
    setattr(model.get_submodule(parent), leaf, module)


def main():
    import torch
    from comet_worker import load_checkpoint_mapped, validate_lengths
    from xcomet_quantized import sha256
    torch.set_num_threads(6)
    torch.set_num_interop_threads(1)
    torch.manual_seed(0)
    torch.backends.quantized.engine = 'fbgemm'
    rows = json.loads((ROOT/'.build/xcomet-sensitivity/samples.json').read_text(encoding='utf-8'))
    save('samples.json', rows)
    print('Loading original COMETKiwi', flush=True)
    model = load_checkpoint_mapped(CHECKPOINT).eval()
    model.requires_grad_(False)
    validate_lengths(model, rows)
    batches = [model.prepare_sample([{'src':r['src'],'mt':r['mt']}],stage='predict') for r in rows]
    originals = {n:m for n,m in model.named_modules() if isinstance(m,(torch.nn.Linear,torch.nn.Embedding))}
    prefix = 'encoder.model.encoder.layer.'
    heads = {n for n in originals if isinstance(originals[n],torch.nn.Linear) and not n.startswith(prefix)}
    embeds = {n for n in originals if isinstance(originals[n],torch.nn.Embedding)}
    layers = sorted({int(n[len(prefix):].split('.')[0]) for n in originals if n.startswith(prefix)})
    report = dict(source_sha256=sha256(CHECKPOINT), source_bytes=CHECKPOINT.stat().st_size,
                  torch=torch.__version__, threads=6, engine='fbgemm', variants=[],
                  scope='32 diagnostic examples used for recipe selection, not a held-out quality benchmark.')

    def predict(indices):
        with torch.inference_mode():
            return [float(model.predict_step(batches[i]).scores[0]) for i in indices]

    full = list(range(len(rows)))
    baseline = predict(full)
    save('baseline.json', baseline)
    official = model.predict([{'src':rows[0]['src'],'mt':rows[0]['mt']}],batch_size=1,gpus=0,num_workers=0,progress_bar=False)
    report['official_path_delta'] = abs(float(official.scores[0])-baseline[0])
    assert report['official_path_delta'] < 1e-6

    def run(name, names, indices=full):
        names = sorted(names)
        start = time.monotonic()
        try:
            for n in names:
                replace(model,n,convert(originals[n]))
            scores = predict(indices)
            stats = dict(name=name, modules=names, seconds=time.monotonic()-start,
                         **drift(scores,[baseline[i] for i in indices]))
            report['variants'].append(stats)
            save(name+'.json',dict(summary=stats,scores=scores))
            save('sensitivity.json',report)
            print(json.dumps({k:v for k,v in stats.items() if k!='modules'}),flush=True)
            return stats
        finally:
            for n in names:
                replace(model,n,originals[n])
            gc.collect()

    # All layers use the same full diagnostic set, including authored errors.
    if '--extend' in sys.argv:
        prior = json.loads((OUT/'sensitivity.json').read_text(encoding='utf-8'))
        assert prior['source_sha256'] == report['source_sha256']
        report = prior
        ranked = [s for s in report['variants'] if s['name'].startswith('layer_')]
    else:
        ranked = [run('layer_'+str(i),[n for n in originals if n.startswith(prefix+str(i)+'.')]) for i in layers]
    ranked.sort(key=lambda s:s['mae'], reverse=True)
    report['sensitivity_order'] = [int(s['name'].split('_')[1]) for s in ranked]
    if '--extend' not in sys.argv:
        run('embeddings',embeds)
        run('heads',heads)
        run('all_int8',originals)
        run('protect_heads',set(originals)-heads)
    for count in ((8,12,16) if '--extend' in sys.argv else (2,4,6)):
        protected = heads | {n for s in ranked[:count] for n in s['modules']}
        run('protect_top'+str(count)+'_heads',set(originals)-protected)
    report['restoration_max_delta'] = max(abs(a-b) for a,b in zip(predict(full),baseline))
    assert report['restoration_max_delta'] < 1e-6
    report['completed'] = True
    save('sensitivity.json',report)
    print('Sensitivity complete',flush=True)


if __name__ == '__main__':
    main()
