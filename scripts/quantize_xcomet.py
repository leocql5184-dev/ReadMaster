"""Export the tested mixed INT8 XCOMET recipe, leaving source weights intact."""
import inspect
import json
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
os.environ['HF_HUB_CACHE'] = str(ROOT/'data/comet-models/hub')
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
from xcomet_quantized import FORMAT, sha256


def main():
    import torch
    from comet_worker import load_checkpoint_mapped
    from comet.models.multitask.xcomet_metric import XCOMETMetric
    torch.set_num_threads(6)
    torch.backends.quantized.engine = 'fbgemm'
    target = ROOT/'data/comet-models/xcomet-xl-int8'
    if (target/'manifest.json').exists():
        raise SystemExit('Verified bundle manifest already exists; refusing overwrite.')
    target.mkdir(exist_ok=True)
    checkpoint = ROOT/'data/comet-models/hub/models--Unbabel--XCOMET-XL/snapshots/6a123c5e8e6dccab25e5fcffa3c8b417abadb462/checkpoints/model.ckpt'
    expected = 'b644a48a1163ca7f8d3bcf237816eea7ecf368ecdbd6eb16ea27fef218fcd716'
    print('Verifying source checkpoint',flush=True)
    if sha256(checkpoint) != expected:
        raise RuntimeError('Source checkpoint checksum mismatch')
    started = time.monotonic()
    model = load_checkpoint_mapped(checkpoint).eval()
    model.requires_grad_(False)
    model.encoder.tokenizer.save_pretrained(target/'encoder')
    model.encoder.model.config.save_pretrained(target/'encoder')
    hparams = {k:v for k,v in dict(model.hparams).items() if k in inspect.signature(XCOMETMetric.__init__).parameters}
    specs = []
    for name,module in list(model.named_modules()):
        if not isinstance(module,(torch.nn.Linear,torch.nn.Embedding)):
            continue
        # Exact recipe tested in the preceding sensitivity analysis.
        if isinstance(module,torch.nn.Linear):
            prefix = 'encoder.model.encoder.layer.'
            if not name.startswith(prefix) or int(name[len(prefix):].split('.')[0]) in (1,2,8,10):
                continue
            module.qconfig = torch.ao.quantization.per_channel_dynamic_qconfig
            quantized = torch.ao.nn.quantized.dynamic.Linear.from_float(module)
            spec = dict(name=name,type='Linear',**{'in':module.in_features,'out':module.out_features,'bias':module.bias is not None})
        else:
            module.qconfig = torch.ao.quantization.float_qparams_weight_only_qconfig
            quantized = torch.ao.nn.quantized.Embedding.from_float(module)
            spec = dict(name=name,type='Embedding',num_embeddings=module.num_embeddings,embedding_dim=module.embedding_dim)
        parent, _, leaf = name.rpartition('.')
        setattr(model.get_submodule(parent),leaf,quantized.eval())
        specs.append(spec)
    print('Saving packed INT8 tensors',flush=True)
    torch.save(model.state_dict(),target/'weights.pt')
    (target/'LICENSE-NOTICE.txt').write_text('Derived from Unbabel/XCOMET-XL revision 6a123c5e8e6dccab25e5fcffa3c8b417abadb462.\nSource: https://huggingface.co/Unbabel/XCOMET-XL\nLicense: CC BY-NC-SA 4.0 https://creativecommons.org/licenses/by-nc-sa/4.0/\nModification: mixed INT8 quantization; encoder layers 1,2,8,10 and output heads remain FP32.\nTokenizer/config derived from facebook/xlm-roberta-xl (model card: MIT).\nThis local artifact is not a clearance for public redistribution or commercial use.\n',encoding='utf-8')
    info = dict(format=FORMAT, torch=torch.__version__, source_sha256=expected,
                recipe='mixed_protect_top4_heads', protected_layers=[1,2,8,10],
                hparams=hparams, modules=specs, weights_bytes=(target/'weights.pt').stat().st_size,
                seconds=time.monotonic()-started,
                files={str(p.relative_to(target)).replace('\\','/'):sha256(p) for p in sorted(target.rglob('*')) if p.is_file() and p.name!='manifest.json'})
    # Manifest is the commit marker: write only after every artifact exists.
    (target/'manifest.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'bundle':str(target),'weights_bytes':info['weights_bytes'],'modules':len(specs),'seconds':info['seconds']}),flush=True)


if __name__=='__main__':
    main()
