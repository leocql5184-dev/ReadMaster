"""Self-contained CPU INT8 COMET bundles; load without FP32 checkpoints."""
import hashlib
import json
from pathlib import Path

FORMAT = 'readmaster-xcomet-int8-v1'
KIWI_FORMAT = 'readmaster-cometkiwi-int8-v1'


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(path):
    path = Path(path).resolve()
    info = json.loads(path.read_text(encoding='utf-8'))
    if info.get('format') not in (FORMAT, KIWI_FORMAT):
        raise ValueError('Unsupported COMET quantization bundle')
    if 'weights.pt' not in info.get('files', {}):
        raise ValueError('Quantized weight checksum is missing')
    for name in info['files']:
        candidate = (path.parent/name).resolve()
        if not candidate.is_relative_to(path.parent) or not candidate.is_file():
            raise ValueError('Invalid or missing quantized bundle file: '+name)
    return info


def load_quantized(path):
    import torch
    path = Path(path).resolve()
    info = read_manifest(path)
    if info['format'] == KIWI_FORMAT:
        import comet.encoders.xlmr as encoder_module
        from comet.models.multitask.unified_metric import UnifiedMetric as Metric
        encoder_class = 'XLMRobertaModel'
    else:
        import comet.encoders.xlmr_xl as encoder_module
        from comet.models.multitask.xcomet_metric import XCOMETMetric as Metric
        encoder_class = 'XLMRobertaXLModel'
    if torch.__version__.split('+')[0] != info['torch'].split('+')[0]:
        raise RuntimeError('Quantized COMET requires PyTorch '+info['torch'])
    if 'fbgemm' not in torch.backends.quantized.supported_engines:
        raise RuntimeError('This CPU runtime does not support fbgemm INT8')
    torch.backends.quantized.engine = 'fbgemm'
    for name, digest in info['files'].items():
        if sha256(path.parent/name) != digest:
            raise ValueError('Quantized bundle checksum mismatch: '+name)
    params = dict(info['hparams'], pretrained_model=str(path.parent/'encoder'),
                  load_pretrained_weights=False, local_files_only=True)
    # Only the large encoder is initialized on meta. COMET's metrics and
    # non-registered tensors must be initialized normally on CPU.
    original = getattr(encoder_module, encoder_class)
    def empty_encoder(*args, **kwargs):
        with torch.device('meta'):
            return original(*args, **kwargs)
    setattr(encoder_module, encoder_class, empty_encoder)
    try:
        model = Metric(**params)
    finally:
        setattr(encoder_module, encoder_class, original)
    for spec in info['modules']:
        if spec['type'] == 'Linear':
            module = torch.ao.nn.quantized.dynamic.Linear(spec['in'],spec['out'],bias_=spec['bias'],dtype=torch.qint8)
        elif spec['type'] == 'Embedding':
            module = torch.ao.nn.quantized.Embedding(spec['num_embeddings'],spec['embedding_dim'],dtype=torch.quint8)
        else:
            raise ValueError('Unsupported quantized module type')
        parent, _, leaf = spec['name'].rpartition('.')
        setattr(model.get_submodule(parent),leaf,module)
    state = torch.load(path.parent/'weights.pt', map_location='cpu', weights_only=True, mmap=True)
    model.load_state_dict(state, strict=True, assign=True)
    # HF's nonpersistent position buffers are intentionally absent from state_dict.
    for module in model.modules():
        for name, value in list(module._buffers.items()):
            if value is not None and value.is_meta:
                if name == 'position_ids':
                    module._buffers[name] = torch.arange(value.shape[-1],dtype=value.dtype).expand(value.shape)
                elif name == 'token_type_ids':
                    module._buffers[name] = torch.zeros(value.shape,dtype=value.dtype)
                else:
                    raise RuntimeError('Unmaterialized buffer: '+name)
    if any(p.is_meta for p in model.parameters()):
        raise RuntimeError('Unmaterialized model parameters')
    model.eval()
    model.requires_grad_(False)
    return model
