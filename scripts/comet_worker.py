"""Isolated COMET worker; no reference translation, no fallback/synthetic score."""
import json
import os
from pathlib import Path
import sys


def normalize_spans(spans, text):
    """Keep tokenizer-decoded text separately; quoted evidence uses original offsets."""
    result = []
    for span in spans:
        start, end = span['start'], span['end']
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text):
            raise ValueError('Invalid XCOMET offsets')
        result.append({**span, 'model_text':span.get('text'), 'text':text[start:end]})
    return result


def decode_complete_spans(model, probabilities, input_ids, offsets):
    """Decode actual token labels, including an error that runs to sentence end.

    COMET 2.2.7 only flushes a span on a subsequent O label. A sentence
    classified entirely as erroneous consequently loses its final span.
    Zero-length BOS/EOS offsets also must not overwrite a real character end.
    This adapter preserves the model's label probabilities and severity rule.
    """
    probability_rows = probabilities.tolist()
    token_rows = input_ids.tolist()
    offset_rows = offsets.tolist() if hasattr(offsets, 'tolist') else offsets
    output = []
    for values, tokens, positions in zip(probability_rows, token_rows, offset_rows):
        spans, active = [], None
        def flush():
            nonlocal active
            if active:
                spans.append({'start':active['start'], 'end':active['end'],
                    'severity':active['severity'], 'confidence':sum(active['confidence'])/len(active['confidence']),
                    'text':model.encoder.tokenizer.decode(active['tokens'])})
                active = None
        for probs, token, position in zip(values, tokens, positions):
            start, end = map(int, position)
            if end <= start:
                flush()
                continue
            threshold = model.decoding_threshold
            if threshold:
                index = max(range(1,len(probs)), key=probs.__getitem__) if sum(probs[1:])>threshold else 0
            else:
                index = max(range(len(probs)), key=probs.__getitem__)
            label = model.label_encoder.ids_to_label[index]
            if label.startswith('I-'):
                if active is None:
                    active = {'start':start, 'end':end, 'severity':label.split('-',1)[1], 'tokens':[], 'confidence':[]}
                active['end'] = end
                active['tokens'].append(token)
                active['confidence'].append(probs[index])
            else:
                flush()
        flush()
        output.append(spans)
    return output


def validate_lengths(model, rows):
    """Match actual encoder inputs; never let COMET silently truncate paper text."""
    limit = min(model.encoder.max_positions, 512)
    separate = model.hparams.class_identifier == 'referenceless_regression_metric'
    for i, row in enumerate(rows):
        lengths = [len(model.encoder.tokenizer.encode(row[key], add_special_tokens=False))
                   for key in ('src', 'mt')]
        # ReferencelessRegression embeds separately; the pinned Kiwi and XCOMET
        # checkpoints use unified/joint inputs and must fit the combined limit.
        if any(length + 2 > limit - 2 for length in lengths) or (not separate and sum(lengths) + 4 > limit):
            raise ValueError(f'length:{i}')


def load_checkpoint_mapped(checkpoint):
    """Use the official loader with disk-backed tensors to reduce peak RAM.

    PyTorch Lightning opens the file before torch.load, which normally creates
    a second full CPU copy. Map only this trusted checkpoint, preserving its
    original weights, dtype and COMET's initialization/configuration logic.
    """
    import torch
    from comet import load_from_checkpoint
    checkpoint = Path(checkpoint).resolve()
    original = torch.load

    def mapped(file, *args, **kwargs):
        name = getattr(file, 'name', file)
        if isinstance(name, (str, os.PathLike)) and Path(name).resolve() == checkpoint:
            kwargs['mmap'] = True
            return original(str(checkpoint), *args, **kwargs)
        return original(file, *args, **kwargs)

    torch.load = mapped
    try:
        # Prefer complete local encoder/tokenizer caches. A fresh installation
        # may still need their small official config/tokenizer downloads.
        return load_with_cached_assets(load_from_checkpoint, checkpoint)
    finally:
        torch.load = original


def load_with_cached_assets(loader, checkpoint):
    """Transformers can raise AttributeError for a missing local vocabulary.

    The pinned Transformers version tries converting a missing vocabulary
    before reporting a cache miss. Retry that specific case with the official
    online loader, preserving the model/tokenizer chosen by COMET.
    """
    try:
        return loader(checkpoint, local_files_only=True)
    except OSError:
        return loader(checkpoint, local_files_only=False)
    except AttributeError as exc:
        if "'NoneType' object has no attribute 'endswith'" not in str(exc):
            raise
        return loader(checkpoint, local_files_only=False)


_MODELS = {}
_MODEL_LOADS = 0


def available_memory():
    if sys.platform == 'win32':
        import ctypes
        class Status(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]
        status = Status()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.available
    return 0  # Unknown memory: retain only one model.


def run(request):
    global _MODEL_LOADS
    cache_root = Path(request.get('cache_root', Path(__file__).resolve().parent.parent/'data'/'comet-models'))
    cache_root.mkdir(parents=True, exist_ok=True)
    os.environ['HF_HUB_CACHE'] = str(cache_root/'hub')
    os.environ['TORCH_HOME'] = str(cache_root/'torch')
    import torch
    from huggingface_hub import snapshot_download, try_to_load_from_cache
    # Checkpoints are trusted official/model paths chosen by the local operator.
    checkpoint = request['model']
    if not Path(checkpoint).is_file():
        options = {'revision': request.get('revision'), 'cache_dir': str(cache_root/'hub')}
        cached = try_to_load_from_cache(checkpoint, 'checkpoints/model.ckpt', **options)
        config = try_to_load_from_cache(checkpoint, 'hparams.yaml', **options)
        if isinstance(cached, str) and isinstance(config, str) and Path(cached).is_file() and Path(config).is_file():
            checkpoint = cached
        else:
            model_dir = snapshot_download(checkpoint, **options, allow_patterns=['hparams.yaml', 'checkpoints/*.ckpt'])
            checkpoint = str(Path(model_dir)/'checkpoints'/'model.ckpt')
    key = (str(checkpoint), request.get('device', 'cpu'))
    if key not in _MODELS:
        import gc
        # XL plus Kiwi fit only when sufficient physical RAM remains. CUDA uses
        # one resident model to avoid assuming spare VRAM from host RAM.
        required = Path(checkpoint).stat().st_size + 3 * 1024**3
        if key[1] == 'cuda' or available_memory() < required or len(_MODELS) >= 2:
            _MODELS.clear()
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        _MODELS[key] = load_checkpoint_mapped(checkpoint)
        _MODEL_LOADS += 1
    model = _MODELS[key]
    if model.hparams.class_identifier == 'xcomet_metric':
        from types import MethodType
        model.decode = MethodType(decode_complete_spans, model)
    validate_lengths(model, request['data'])
    device = request.get('device', 'cpu')
    if device not in ('cpu', 'cuda'):
        raise ValueError('device')
    if device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('cuda')
    model.eval()
    output = model.predict(request['data'], batch_size=1, gpus=1 if device == 'cuda' else 0,
                           progress_bar=False, num_workers=0)
    metadata = getattr(output, 'metadata', None)
    spans = getattr(metadata, 'error_spans', None)
    return [{'score': float(score), **({'error_spans': normalize_spans(spans[i], request['data'][i]['mt'])} if spans is not None else {})}
            for i, score in enumerate(output.scores)]


def execute(inp, out):
    out = Path(out)
    try:
        result = {'results': run(json.loads(Path(inp).read_text(encoding='utf-8'))),
                  'worker': {'pid': os.getpid(), 'model_loads': _MODEL_LOADS,
                             'resident_models': len(_MODELS)}}
    except Exception as exc:
        name = type(exc).__name__
        if isinstance(exc, ImportError):
            message = 'COMET 依赖未安装，请运行 scripts/setup_comet.py'
        elif 'Gated' in name or '401' in str(exc) or '403' in str(exc):
            message = 'COMET 模型访问未授权；请在 Hugging Face 接受相应模型条件并登录后重试'
        elif isinstance(exc, ValueError) and str(exc).startswith('length:'):
            message = '评估单元超过模型 token 上限，未截断或评分；需要细分此单元后重试'
        elif 'memory' in str(exc).lower():
            message = 'COMET 可用内存不足；改用 CPU 或降低模型规模'
        else:
            message = 'COMET 模型加载或推理失败（'+name+'），请检查模型路径、网络和隔离依赖'
        result = {'error': message}
    temp = out.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temp.replace(out)


if __name__ == '__main__':
    if sys.argv[1:] == ['--serve']:
        for line in sys.stdin:
            request = json.loads(line)
            execute(request['input'], request['output'])
    else:
        execute(sys.argv[1], sys.argv[2])
