"""Create a relocatable Windows COMET runtime without user data or model credentials."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT/'.venv-comet/Lib/site-packages'
    archive = ROOT/'.build/python-3.12.10-embed-amd64.zip'
    if not source.is_dir() or not archive.is_file():
        raise SystemExit('Prepare .venv-comet and the official Python 3.12.10 embedded archive first.')
    if hashlib.md5(archive.read_bytes()).hexdigest() != 'fe8ef205f2e9c3ba44d0cf9954e1abd3':
        raise SystemExit('Python archive digest does not match the official release.')
    target = ROOT/'tools/comet'
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        for name in zipped.namelist():
            if not (target/name).resolve().is_relative_to(target.resolve()):
                raise SystemExit('Invalid embedded Python archive path')
        zipped.extractall(target)
    (target/'python312._pth').write_text('python312.zip\n.\nLib/site-packages\napp\nimport site\n', encoding='utf-8')
    shutil.copytree(source, target/'Lib/site-packages', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (target/'app').mkdir(exist_ok=True)
    shutil.copy2(ROOT/'scripts/comet_worker.py', target/'app/comet_worker.py')
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
        env.pop(key, None)
    env['PATH'] = str(Path(env.get('SystemRoot', 'C:/Windows'))/'System32')
    result = subprocess.run([str(target/'python.exe'), '-c',
        'import json, importlib.metadata as m; import torch, comet; '
        'print(json.dumps({"torch":torch.__version__,"comet":m.version("unbabel-comet"),'
        '"huggingface_hub":m.version("huggingface-hub"),"hf_xet":m.version("hf-xet")}))'],
        env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        (ROOT/'.build/comet-portable-error.txt').write_text(result.stderr, encoding='utf-8')
        raise SystemExit('Portable COMET import failed; see .build/comet-portable-error.txt')
    report = json.loads(result.stdout.strip().splitlines()[-1])
    report.update(python_archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                  relocatable_python=True, includes_credentials=False, includes_models=False,
                  bytes=sum(p.stat().st_size for p in target.rglob('*') if p.is_file()))
    (target/'runtime-manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
