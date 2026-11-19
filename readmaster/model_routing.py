"""Lightweight model routing shared by the client and local QE backend."""
import hashlib
import json
import os
from pathlib import Path


MODEL_BUNDLES = {'kiwi': ('cometkiwi-int8', 'readmaster-cometkiwi-int8-v1'),
                 'xcomet': ('xcomet-xl-int8', 'readmaster-xcomet-int8-v1')}
ORIGINAL_MODELS = {'kiwi': 'Unbabel/wmt22-cometkiwi-da', 'xcomet': 'Unbabel/XCOMET-XL'}


def verified_manifest(path, strict=False, expected_format=None):
    try:
        path = Path(path)
        evidence = json.loads(path.with_name('verified.json').read_text(encoding='utf-8'))
        data = path.read_bytes()
        if evidence.get('passed') is not True or evidence.get('manifest_sha256') != hashlib.sha256(data).hexdigest():
            return False
        if strict:
            manifest = json.loads(data)
            files = manifest.get('files', {})
            if manifest.get('format') != expected_format or not isinstance(files, dict) or 'weights.pt' not in files:
                return False
            for name, digest in files.items():
                item = (path.parent / name).resolve()
                if (not item.is_relative_to(path.parent.resolve()) or not item.is_file()
                        or not isinstance(digest, str) or len(digest) != 64):
                    return False
        return True
    except (OSError, ValueError, AttributeError, TypeError):
        return False


def select_models(root):
    # Library/research scripts retain auto mode. The launcher explicitly defaults
    # to quantized, so installed clients cannot silently fetch original weights.
    mode = os.environ.get('READMASTER_COMET_MODE', 'auto')
    if mode not in {'auto', 'quantized', 'fp32'}:
        raise ValueError('READMASTER_COMET_MODE 必须是 quantized、fp32 或 auto')
    device = os.environ.get('READMASTER_COMET_DEVICE', 'cpu')
    if mode == 'quantized' and device != 'cpu':
        raise ValueError('当前量化模型仅支持 CPU；请设置 READMASTER_COMET_DEVICE=cpu')
    models = {}
    for kind, (folder, format_name) in MODEL_BUNDLES.items():
        override = os.environ.get('READMASTER_' + kind.upper() + '_MODEL')
        bundle = Path(root) / 'data/comet-models' / folder / 'manifest.json'
        if mode == 'quantized':
            chosen = Path(override) if override else bundle
            if not verified_manifest(chosen, strict=True, expected_format=format_name):
                raise ValueError(f'{kind} 量化模型缺失、文件不完整或尚未验证：{chosen}。'
                                 '请准备完整量化模型包并运行对应 verify_quantized 脚本；'
                                 '不会自动下载原始权重。需要原模型时请显式使用 --comet-mode fp32。')
            models[kind] = str(chosen.resolve())
        elif override:
            models[kind] = override
        elif mode == 'auto' and device == 'cpu' and verified_manifest(bundle):
            models[kind] = str(bundle)
        else:
            models[kind] = ORIGINAL_MODELS[kind]
    return models
