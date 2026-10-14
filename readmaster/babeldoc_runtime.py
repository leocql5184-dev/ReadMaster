"""Resolve an isolated engine in development and in the portable application."""
from pathlib import Path


def command(root, mode):
    if mode not in ('template', 'pdf'):
        raise ValueError('Unknown BabelDOC worker mode')
    root = Path(root)
    filename = 'babeldoc_template.py' if mode == 'template' else 'babeldoc_pdf_worker.py'
    bundled = root / 'tools/babeldoc'
    if (bundled/'python.exe').is_file() and (bundled/'app/scripts'/filename).is_file():
        return [str(bundled/'python.exe'), str(bundled/'app/scripts'/filename)]
    python = root/'.build/babel-runtime/Scripts/python.exe'
    script = root/'scripts'/filename
    if python.is_file() and script.is_file():
        return [str(python), str(script)]
    raise RuntimeError('BabelDOC 独立运行环境未准备，请使用完整客户端或运行环境准备脚本')


def available(root):
    try:
        command(root, 'pdf')
        return True
    except RuntimeError:
        return False
