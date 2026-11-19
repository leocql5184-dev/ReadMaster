"""Export a measured COMETKiwi INT8 recipe without modifying its source."""
import argparse
import inspect
import json
import time

from analyze_kiwi_quantization import ROOT, OUT, CHECKPOINT, convert, replace
from xcomet_quantized import KIWI_FORMAT, sha256

SOURCE_SHA256 = '4f357aa38b0737dcd502f166238c99711ff3419d7b5c8cdf9cde08525a8e7858'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recipe', required=True, help='Name from the local sensitivity report')
    args = parser.parse_args()
    import torch
    from comet_worker import load_checkpoint_mapped
    from comet.models.multitask.unified_metric import UnifiedMetric
    torch.set_num_threads(6)
    torch.set_num_interop_threads(1)
    torch.backends.quantized.engine = 'fbgemm'
    target = ROOT/'data/comet-models/cometkiwi-int8'
    if (target/'manifest.json').exists():
        raise SystemExit('Bundle already exists; refusing overwrite.')
    report = json.loads((OUT/'sensitivity.json').read_text(encoding='utf-8'))
    if not report.get('completed') or sha256(CHECKPOINT) != SOURCE_SHA256 or report['source_sha256'] != SOURCE_SHA256:
        raise RuntimeError('Source checkpoint or sensitivity evidence mismatch')
    recipe = next(s for s in report['variants'] if s['name'] == args.recipe)
    target.mkdir(parents=True,exist_ok=True)
    started = time.monotonic()
    model = load_checkpoint_mapped(CHECKPOINT).eval()
    model.requires_grad_(False)
    model.encoder.tokenizer.save_pretrained(target/'encoder')
    model.encoder.model.config.save_pretrained(target/'encoder')
    hparams = {k:v for k,v in dict(model.hparams).items() if k in inspect.signature(UnifiedMetric.__init__).parameters}
    modules = []
    for name in recipe['modules']:
        module = model.get_submodule(name)
        if isinstance(module,torch.nn.Linear):
            spec = dict(name=name,type='Linear',**{'in':module.in_features,'out':module.out_features,'bias':module.bias is not None})
        else:
            spec = dict(name=name,type='Embedding',num_embeddings=module.num_embeddings,embedding_dim=module.embedding_dim)
        replace(model,name,convert(module))
        modules.append(spec)
    torch.save(model.state_dict(),target/'weights.pt')
    rows = json.loads((OUT/'samples.json').read_text(encoding='utf-8'))
    predictions = model.predict([{'src':r['src'],'mt':r['mt']} for r in rows],batch_size=1,gpus=0,num_workers=0,progress_bar=False)
    expected = json.loads((OUT/(args.recipe+'.json')).read_text(encoding='utf-8'))['scores']
    delta = max(abs(float(a)-b) for a,b in zip(predictions.scores,expected))
    if delta >= 1e-6:
        raise RuntimeError('Export recipe does not reproduce sensitivity scores')
    notice = ('Derived from Unbabel/wmt22-cometkiwi-da, revision 1ad785194e391eebc6c53e2d0776cada8f83179a.\n'
              'Source: https://huggingface.co/Unbabel/wmt22-cometkiwi-da\n'
              'License: CC BY-NC-SA 4.0 https://creativecommons.org/licenses/by-nc-sa/4.0/\n'
              'Modification: mixed CPU INT8 quantization, recipe '+args.recipe+'.\n'
              'Tokenizer/config derived from microsoft/infoxlm-large.\n'
              'Local artifact; no redistribution or commercial clearance is asserted.\n')
    (target/'LICENSE-NOTICE.txt').write_text(notice,encoding='utf-8')
    info = dict(format=KIWI_FORMAT,torch=torch.__version__,source_sha256=SOURCE_SHA256,
                source_model='Unbabel/wmt22-cometkiwi-da',source_revision='1ad785194e391eebc6c53e2d0776cada8f83179a',
                recipe=args.recipe,hparams=hparams,modules=modules,threads=6,
                protected_layers=[i for i in range(24) if not any(m['name'].startswith(f'encoder.model.encoder.layer.{i}.') for m in modules)],
                weights_bytes=(target/'weights.pt').stat().st_size,seconds=time.monotonic()-started,
                diagnostic_export_max_delta=delta,
                files={str(p.relative_to(target)).replace('\\','/'):sha256(p) for p in sorted(target.rglob('*')) if p.is_file() and p.name!='manifest.json'})
    (target/'manifest.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:info[k] for k in ('recipe','weights_bytes','protected_layers','diagnostic_export_max_delta','seconds')}),flush=True)


if __name__ == '__main__':
    main()
