"""Build audited offline Inno slices and a pinned online bootstrapper. No upload."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from readmaster.model_routing import MODEL_BUNDLES, verified_manifest

VERSION = '0.5.0.1'
REPOSITORY = 'leocql5184-dev/ReadMaster'
WORK = ROOT / '.build' / ('installer-' + VERSION)
PAYLOAD = WORK / 'payload'
OUTPUT = ROOT / 'output' / ('ReadMaster-' + VERSION)
TOOLCHAIN = ROOT / '.build/installer-toolchain'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_tree(source, target):
    shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.log'), dirs_exist_ok=True)


def run(args, log=None):
    env = dict(os.environ, PYTHONUTF8='1', PYINSTALLER_CONFIG_DIR=str(ROOT/'.build/pyinstaller-cache'))
    if log:
        with Path(log).open('w', encoding='utf-8') as stream:
            subprocess.run([str(a) for a in args], cwd=ROOT, env=env, stdout=stream,
                           stderr=subprocess.STDOUT, check=True)
    else:
        subprocess.run([str(a) for a in args], cwd=ROOT, env=env, check=True)


def stage():
    if PAYLOAD.exists():
        raise RuntimeError('Payload already exists. Use --compile-only after reviewing it; never silently overwrite a distribution.')
    WORK.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    audit = json.loads((ROOT/'licenses/models/redistribution-audit.json').read_text(encoding='utf-8'))
    assert audit['redistribution'] == 'noncommercial_only_with_attribution_and_sharealike'
    for row in audit['models']:
        assert row['license'] == 'cc-by-nc-sa-4.0'
    print('Building current desktop executable...', flush=True)
    run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
         '--name', 'ReadMaster', '--distpath', WORK/'desktop', '--workpath', WORK/'pyinstaller',
         '--specpath', WORK, '--icon', ROOT/'assets/ReadMaster.ico',
         '--add-data', str(ROOT/'assets')+';assets', '--add-data', str(ROOT/'web')+';web', ROOT/'launcher.py'], WORK/'desktop-build.log')
    copy_tree(WORK/'desktop/ReadMaster', PAYLOAD)
    print('Copying portable runtimes and licensed assets...', flush=True)
    for name in ('codex', 'babeldoc', 'comet'):
        copy_tree(ROOT/'tools'/name, PAYLOAD/'tools'/name)
    copy_tree(ROOT/'licenses', PAYLOAD/'licenses')
    shutil.copy2(TOOLCHAIN/'InnoSetup/License.txt', PAYLOAD/'licenses/Inno-Setup.txt')
    copy_tree(ROOT/'.build/upstream-source', PAYLOAD/'source/upstream')
    (PAYLOAD/'assets').mkdir(exist_ok=True)
    shutil.copy2(ROOT/'assets/ReadMaster.ico', PAYLOAD/'assets/ReadMaster.ico')
    for name in ('LICENSE', 'README.md', 'THIRD-PARTY-NOTICES.md', 'WINDOWS安装与发布.md'):
        shutil.copy2(ROOT/name, PAYLOAD/name)
    for name in ('INSTALL-LICENSE.txt', 'INSTALL-README.txt'):
        shutil.copy2(ROOT/'installer'/name, PAYLOAD/name)
    print('Verifying and copying complete INT8 bundles...', flush=True)
    for kind, (folder, format_name) in MODEL_BUNDLES.items():
        source = ROOT/'data/comet-models'/folder
        manifest_path = source/'manifest.json'
        if not verified_manifest(manifest_path, strict=True, expected_format=format_name):
            raise RuntimeError('Unverified quantized model: '+kind)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        target = PAYLOAD/'data/comet-models'/folder
        target.mkdir(parents=True)
        for name, sha in manifest['files'].items():
            src = source/name
            if digest(src) != sha:
                raise RuntimeError('Bundle checksum mismatch: '+str(src))
            dst = target/name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        shutil.copy2(manifest_path, target/'manifest.json')
        # Keep verifiable activation evidence, without local paths/experiment statistics.
        (target/'verified.json').write_text(json.dumps({'passed': True,
            'manifest_sha256': digest(manifest_path), 'recipe': manifest['recipe'],
            'scope': 'Offline reload and prediction validation of the unchanged bundle; not lossless FP32 equivalence.'}, indent=2), encoding='utf-8')
        (target/'DISTRIBUTION-NOTICE.txt').write_text(
            'Noncommercial distribution of an adapted model under CC BY-NC-SA 4.0.\n'
            'Original creator: Unbabel. Source: https://huggingface.co/'+next(r['id'] for r in audit['models'] if ('kiwi' in r['id'].lower()) == (kind=='kiwi'))+'\n'
            'Source revision: '+next(r['revision'] for r in audit['models'] if ('kiwi' in r['id'].lower()) == (kind=='kiwi'))+'\n'
            'Modification by ReadMaster contributors: mixed CPU INT8, recipe '+manifest['recipe']+'.\n'
            'The original LICENSE-NOTICE records the earlier local export; this distribution adds license evidence.\n'
            'Retain attribution, indicate modifications, and share adaptations under CC BY-NC-SA 4.0.\n'
            'No warranties or Unbabel endorsement. Commercial services require separate authorization.\n'
            'Full original licenses/cards, tokenizer notices and audit: ../../../licenses/models/\n'
            'License: https://creativecommons.org/licenses/by-nc-sa/4.0/\n', encoding='utf-8')
    print('Exporting clean source and generating file inventory...', flush=True)
    raw = subprocess.check_output([sys.executable, '-X', 'utf8', str(ROOT/'scripts/prepare_github_source.py')], cwd=ROOT)
    source_report = json.loads(raw)
    shutil.copy2(ROOT/source_report['archive'], PAYLOAD/'source/ReadMaster-source.zip')
    shutil.copy2(ROOT/source_report['archive'], OUTPUT/'ReadMaster-source.zip')
    audit_payload(source_report['sha256'])


def audit_payload(source_sha256):
    forbidden_names = {'auth.json','credentials.json','models.json','token','stored_tokens','desktop-instance.json','address.txt'}
    def inspect(path):
        if not path.is_file(): return None
        rel = path.relative_to(PAYLOAD)
        if path.name in forbidden_names or path.suffix.lower() in {'.pdf','.sqlite','.db','.log'}:
            raise RuntimeError('Unexpected private/runtime file: '+str(rel))
        if rel.parts[0] == 'data' and rel.parts[1] != 'comet-models':
            raise RuntimeError('User data found in payload')
        return {'path':rel.as_posix(),'bytes':path.stat().st_size,'sha256':digest(path)}
    inventory=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for item in pool.map(inspect, sorted(PAYLOAD.rglob('*'))):
            if item:
                inventory.append(item)
                if len(inventory)%5000==0:print('Audited files: '+str(len(inventory)),flush=True)
    report = {'version':VERSION,'files':inventory,'bytes':sum(r['bytes'] for r in inventory),
              'private_data_check':'passed','model_bundle_count':2,'original_fp32_included':False,
              'webview2':json.loads((TOOLCHAIN/'webview2-download.json').read_text()),
              'source_archive_sha256':source_sha256}
    (OUTPUT/'payload-inventory.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Staged bytes: '+str(report['bytes']),flush=True)


def compile_installer():
    compiler = TOOLCHAIN/'InnoSetup/ISCC.exe'
    webview = TOOLCHAIN/'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
    expected = json.loads((TOOLCHAIN/'webview2-download.json').read_text())
    if digest(webview) != expected['sha256']:
        raise RuntimeError('WebView2 installer changed')
    vcredist = json.loads((TOOLCHAIN/'vcredist-download.json').read_text())
    if not vcredist['resolved_url'].startswith('https://download.visualstudio.microsoft.com/'):
        raise RuntimeError('VC++ download must use the official Microsoft host')
    if digest(TOOLCHAIN/'VC_redist.x64.exe') != vcredist['sha256']:
        raise RuntimeError('VC++ reference installer changed')
    print('Compressing installer into Release-sized slices...',flush=True)
    run([compiler, '/Qp', '/DPayload='+str(PAYLOAD), '/DOutput='+str(OUTPUT),
         '/DWebViewInstaller='+str(webview), '/DVCRedistURL='+vcredist['resolved_url'],
         '/DVCRedistSHA256='+vcredist['sha256'], ROOT/'installer/ReadMaster.iss'], WORK/'inno-build.log')
    full_name = 'ReadMaster-Full-'+VERSION+'-Windows-x64.exe'
    assets = []
    for path in sorted(OUTPUT.glob('ReadMaster-Full-*')):
        if path.stat().st_size >= 2*1024**3:
            raise RuntimeError('Release asset exceeds 2 GiB: '+path.name)
        assets.append({'name':path.name,'bytes':path.stat().st_size,'sha256':digest(path)})
    assert any(row['name']==full_name for row in assets)
    base_url='https://github.com/'+REPOSITORY+'/releases/download/v'+VERSION
    manifest = {'version':VERSION,'base_url':base_url,'setup':full_name,'assets':assets,
                'published':False,'signature':'unsigned ReadMaster binaries; hashes pinned in bootstrapper',
                'models':'mixed INT8, noncommercial CC BY-NC-SA 4.0',
                'microsoft_vc_runtime':vcredist}
    (OUTPUT/'release-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    resource = WORK/'payload.tsv'
    resource.write_text('\n'.join([VERSION,base_url,full_name]+[r['name']+'\t'+str(r['bytes'])+'\t'+r['sha256'] for r in assets])+'\n',encoding='utf-8')
    csc=Path(os.environ.get('SystemRoot','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    bootstrap=OUTPUT/('ReadMaster-Setup-'+VERSION+'-Windows-x64.exe')
    run([csc,'/nologo','/target:winexe','/platform:x64','/optimize+',
         '/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll',
         '/win32icon:'+str(ROOT/'assets/ReadMaster.ico'),
         '/resource:'+str(resource)+',payload.tsv','/out:'+str(bootstrap),ROOT/'installer/Bootstrapper.cs'])
    checks = [p for p in sorted(OUTPUT.iterdir()) if p.is_file() and p.name!='SHA256SUMS.txt']
    (OUTPUT/'SHA256SUMS.txt').write_text(''.join(digest(p)+'  '+p.name+'\n' for p in checks),encoding='utf-8')
    print(json.dumps({'output':str(OUTPUT),'bootstrapper':bootstrap.name,
                      'payload_download_bytes':sum(r['bytes'] for r in assets),'assets':len(assets)},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compile-only',action='store_true')
    parser.add_argument('--audit-only',action='store_true',help='Audit a completed staging directory, then compile.')
    args=parser.parse_args()
    if args.audit_only: audit_payload(digest(PAYLOAD/'source/ReadMaster-source.zip'))
    elif not args.compile_only: stage()
    compile_installer()
