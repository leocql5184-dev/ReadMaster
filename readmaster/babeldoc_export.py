"""Supervise isolated PDF replay; publish only complete, versioned output."""
import copy
import json
from pathlib import Path
import subprocess
import time
import uuid

from .babeldoc_replay import ApprovedReplay
from .babeldoc_runtime import command


def export_document(document, folder, root, cancelled=lambda: False, notify=lambda text: None):
    snapshot = copy.deepcopy(document)
    replay = ApprovedReplay(snapshot)
    if not replay.expected or replay.unapproved:
        raise ValueError('请先完成正文翻译和验收，再导出最终 PDF')
    folder, root = Path(folder), Path(root)
    args = command(root, 'pdf')
    stage = folder / 'exports' / ('pending-' + uuid.uuid4().hex)
    stage.mkdir(parents=True)
    source = stage / 'document.json'
    source.write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
    job = stage / 'job.json'
    job.write_text(json.dumps({'input': str((folder/'original.pdf').resolve()),
                              'document': str(source.resolve()), 'output': str((stage/'pdf').resolve())}), encoding='utf-8')
    # Only document paths cross this process boundary; no login or API secrets.
    notify('正在生成论文 PDF')
    with (stage / 'worker.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([*args, '--job', str(job)],
                                   stdout=log, stderr=log, cwd=root,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancelled():
                    raise InterruptedError('PDF 导出已暂停，已保存的译文保留')
                if time.monotonic()-started > 1800:
                    raise RuntimeError('PDF 导出超时，请检查引擎资源和文档')
                time.sleep(.2)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    report_file = stage / 'pdf/replay-report.json'
    if not report_file.exists():
        raise RuntimeError('PDF 排版失败，详情已保存到本机导出日志')
    report = json.loads(report_file.read_text(encoding='utf-8'))
    if process.returncode or not report['complete'] or report['translation_version'] != replay.version:
        raise RuntimeError('PDF 句块对应未完整通过检查，未发布；详情已保存到本机导出报告')
    if not {'mono_pdf_path', 'dual_pdf_path'}.issubset(report.get('paths', {})):
        raise RuntimeError('PDF 引擎未生成完整的译文和双语文件')
    for file in report['paths'].values():
        path = Path(file).resolve()
        if not path.is_relative_to((stage/'pdf').resolve()) or not path.is_file():
            raise RuntimeError('PDF 引擎输出路径不符合导出范围')
        import pymupdf
        with pymupdf.open(path) as pdf:
            if len(pdf) != len(snapshot['pages']):
                raise RuntimeError('输出 PDF 页数与原文不一致，未发布')
    result = {'translation_version': replay.version, 'paths': report['paths'],
              'rendered_units': report['rendered_units'], 'created_at': time.time()}
    (stage / 'manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
