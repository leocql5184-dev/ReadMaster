"""Create a relocatable, self-contained Windows PDF runtime from pinned dependencies."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT/'.build/babel-runtime/Lib/site-packages'
    archive = ROOT/'.build/python-3.12.10-embed-amd64.zip'
    if not source.is_dir() or not archive.is_file():
        raise SystemExit('Prepare the pinned engine environment and official Python embed archive first.')
    if hashlib.md5(archive.read_bytes()).hexdigest() != 'fe8ef205f2e9c3ba44d0cf9954e1abd3':
        raise SystemExit('Python embed archive does not match the official release digest.')
    target = ROOT/'tools/babeldoc'
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        for name in zipped.namelist():
            if not (target/name).resolve().is_relative_to(target.resolve()):
                raise SystemExit('Invalid Python archive path')
        zipped.extractall(target)
    (target/'python312._pth').write_text('python312.zip\n.\nLib/site-packages\napp\nimport site\n', encoding='utf-8')
    shutil.copytree(source, target/'Lib/site-packages', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    app = target/'app'
    (app/'readmaster').mkdir(parents=True, exist_ok=True)
    (app/'readmaster/__init__.py').write_text('"""Isolated PDF replay package."""\n', encoding='utf-8')
    shutil.copy2(ROOT/'readmaster/babeldoc_replay.py', app/'readmaster/babeldoc_replay.py')
    (app/'scripts').mkdir(exist_ok=True)
    for name in ('babeldoc_template.py', 'babeldoc_pdf_worker.py', 'babeldoc_assets.py'):
        shutil.copy2(ROOT/'scripts'/name, app/'scripts'/name)
    assets = Path.home()/'.cache/babeldoc'
    for name in ('fonts', 'models', 'tiktoken'):
        if not (assets/name).is_dir():
            raise SystemExit('Engine assets have not been prepared: '+name)
        shutil.copytree(assets/name, target/'assets'/name, dirs_exist_ok=True)
    result = subprocess.run([str(target/'python.exe'), str(app/'scripts/babeldoc_template.py')],
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        (ROOT/'.build/babel-portable-error.txt').write_text(result.stderr, encoding='utf-8')
        raise SystemExit('Portable engine import check failed; see .build/babel-portable-error.txt')
    template = json.loads(result.stdout.strip().splitlines()[-1])
    report = {'python_archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
              'babeldoc': template['version'], 'relocatable_python': True,
              'asset_files': sum(1 for p in (target/'assets').rglob('*') if p.is_file()),
              'bytes': sum(p.stat().st_size for p in target.rglob('*') if p.is_file())}
    (target/'runtime-manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
