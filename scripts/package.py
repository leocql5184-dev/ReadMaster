"""Reproducible portable package; never include runtime documents or credentials."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

root=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(root))
parser=argparse.ArgumentParser()
parser.add_argument('--candidate',action='store_true',help='Build an isolated verification artifact without replacing the release archive.')
options=parser.parse_args()
out=root/('.build/client-candidate' if options.candidate else 'output')
out.mkdir(exist_ok=True)
dist=out if options.candidate else root/'.build/client-dist'
stage=root/('.build/package-release-candidate' if options.candidate else '.build/package-release')
target=stage/'ReadMaster-Client-Windows-x64'
engine=root/'tools/babeldoc'
comet_runtime=root/'tools/comet'
if not (engine/'python.exe').is_file() or not (engine/'runtime-manifest.json').is_file():
    raise RuntimeError('Prepare the isolated BabelDOC portable runtime before packaging.')
if not (comet_runtime/'python.exe').is_file() or not (comet_runtime/'runtime-manifest.json').is_file():
    raise RuntimeError('Run scripts/build_comet_runtime.py before packaging the QE client.')
subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir',
    '--name','ReadMaster','--distpath',str(dist),'--workpath',str(root/'.build/pyinstaller'),
    '--specpath',str(root/'.build'),'--icon',str(root/'assets/ReadMaster.ico'),
    '--add-data',str(root/'assets')+';assets','--add-data',str(root/'web')+';web',str(root/'launcher.py')],
    check=True,cwd=root,env={**os.environ,'PYINSTALLER_CONFIG_DIR':str(root/'.build/pyinstaller-cache')})
generated=dist/'ReadMaster'
if target.exists():
    # Replace only staging, never the user's installed app or its data directory.
    if not target.resolve().is_relative_to((root/'.build').resolve()):
        raise RuntimeError('invalid package path')
    shutil.rmtree(target)
shutil.copytree(generated,target)
shutil.copytree(root/'tools/codex',target/'tools/codex')
shutil.copytree(engine,target/'tools/babeldoc',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
shutil.copytree(comet_runtime,target/'tools/comet',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
source=target/'source'
for folder in ('readmaster','web','scripts','tests','assets'):
    shutil.copytree(root/folder,source/folder,ignore=shutil.ignore_patterns('__pycache__','paper_sets'))
for name in ('launcher.py','requirements.txt','requirements-comet.txt','requirements-babeldoc.txt','requirements-babeldoc-lock.txt','README.md','LICENSE','THIRD-PARTY-NOTICES.md','BabelDOC接入记录.md','COMET评估接入.md'):
    shutil.copy2(root/name,target/name)
    if name in ('launcher.py','requirements.txt','requirements-comet.txt','requirements-babeldoc.txt','requirements-babeldoc-lock.txt'):
        shutil.copy2(root/name,source/name)
if (root/'.impeccable/design.json').exists():
    (source/'.impeccable').mkdir()
    shutil.copy2(root/'.impeccable/design.json',source/'.impeccable/design.json')
license_dir=target/'licenses'
license_dir.mkdir()
for path in (root/'licenses').glob('*'):
    if path.is_file():shutil.copy2(path,license_dir/path.name)
# Include the upstream source artifacts downloaded during preparation.
if (root/'.build/upstream-source').exists():
    shutil.copytree(root/'.build/upstream-source',source/'upstream')
archive=out/'ReadMaster-Client-Windows-x64.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
    for file in target.rglob('*'):
        if file.is_file():zipped.write(file,file.relative_to(stage))
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
(out/'SHA256.txt').write_text(f'{digest}  {archive.name}\n',encoding='utf-8')
print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'sha256':digest}))
