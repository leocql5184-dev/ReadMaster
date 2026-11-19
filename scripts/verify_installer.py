"""Install/upgrade/infer/uninstall the real distribution in an isolated directory."""
import json
import hashlib
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from readmaster.quality import CometBackend
from readmaster.comet_service import WORKER


def main():
    work=ROOT/'.build/installer-validation'
    home=work/'安装 测试'
    work.mkdir(exist_ok=True)
    if home.exists(): raise RuntimeError('Verification destination already exists; preserve/review it before a new run.')
    setup=ROOT/'output/ReadMaster-0.5.0.1/ReadMaster-Full-0.5.0.1-Windows-x64.exe'
    with setup.open('rb') as stream:
        setup_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    report={'installer':setup.name,'installer_sha256':setup_hash,'tests':{},'fresh_windows_vm':False,
            'prerequisite_test_scope':'Existing WebView2 and VC++ runtime; missing-runtime OS installation not exercised.'}
    def save():
        (work/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    def command(args,timeout=1200):
        result=subprocess.run([str(a) for a in args],timeout=timeout,
                              capture_output=True,text=True,encoding='utf-8',errors='replace',
                              creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:raise RuntimeError(f'Command failed: {Path(args[0]).name}, exit={result.returncode}, {result.stderr[-500:]}')
        return result
    print('Installing the actual complete offline payload...',flush=True)
    args=[setup,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOICONS','/MERGETASKS=!desktopicon','/DIR='+str(home)]
    command(args+['/LOG='+str(work/'install.log')])
    report['tests']['install']=True;save()
    assert 'Microsoft VC++ x64 runtime is already present.' in (work/'install.log').read_text(encoding='utf-8-sig')
    for key in list(os.environ):
        if key.startswith('READMASTER_') or key in ('HF_TOKEN','HUGGING_FACE_HUB_TOKEN','PYTHONHOME','PYTHONPATH'):
            os.environ.pop(key,None)
    os.environ.update(READMASTER_COMET_MODE='quantized',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                      HF_HOME=str(work/'empty-hf-home'),HF_HUB_CACHE=str(work/'empty-hf-home/hub'),
                      TORCH_HOME=str(work/'empty-torch-home'),HF_TOKEN='')
    command([home/'ReadMaster.exe','--check-models',work/'model-check.json'])
    selection=json.loads((work/'model-check.json').read_text(encoding='utf-8'))
    assert selection['passed'] and selection['frozen'] and selection['mode']=='quantized'
    assert all(Path(p).is_relative_to(home) for p in selection['models'].values())
    report['tests']['installed_quantized_routing']=True;save()
    print('Running actual installed Kiwi and XCOMET with empty offline caches...',flush=True)
    backend=CometBackend(home,work/'qe-cache')
    sample=[{'src':'The model does not require labeled data.','mt':'该模型不需要标注数据。'},
            {'src':'The experiment used 24 participants.','mt':'实验有 24 名参与者。'}]
    try:
        values={kind:backend.predict(kind,sample) for kind in ('kiwi','xcomet')}
        assert all(len(rows)==2 and all(math.isfinite(r['score']) for r in rows) for rows in values.values())
        report['inference']={'models':backend.models,'python':str(backend.python),'results':values,
                             'worker':WORKER.last_report,'offline':True}
        assert WORKER.last_report['resident_models']==2
        report['tests']['installed_offline_inference']=True;save()
    finally: WORKER.close()
    command([home/'tools/babeldoc/python.exe',home/'tools/babeldoc/app/scripts/babeldoc_template.py'])
    command([home/'tools/codex/bin/codex.exe','--version'])
    report['tests']['installed_babeldoc_and_codex']=True;save()
    print('Checking the installed desktop and local page...',flush=True)
    app=subprocess.Popen([str(home/'ReadMaster.exe')],cwd=home,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        url=None
        for _ in range(120):
            if app.poll() is not None: raise RuntimeError('Installed desktop exited early')
            if (home/'data/address.txt').is_file():
                url=(home/'data/address.txt').read_text(encoding='utf-8')
                with urllib.request.urlopen(url+'/api/health',timeout=3) as response: health=json.load(response)
                if health['ok']:break
            time.sleep(.25)
        assert url
        with urllib.request.urlopen(url,timeout=5) as response:
            assert 'ReadMaster' in response.read().decode('utf-8')
        time.sleep(2)
        request=urllib.request.Request(url+'/api/shutdown',b'{}',{'X-ReadMaster':'1'},method='POST')
        with urllib.request.urlopen(request,timeout=5) as response:assert response.status==200
        assert app.wait(timeout=20)==0
    finally:
        if app.poll() is None:app.terminate();app.wait(timeout=10)
    report['tests']['installed_desktop_start_and_exit']=True;save()
    retained=home/'data/retention-test.txt';retained.write_text('Synthetic user data; must survive upgrade and uninstall.',encoding='utf-8')
    print('Upgrading the same install and checking user-data retention...',flush=True)
    command(args+['/LOG='+str(work/'upgrade.log')])
    assert retained.is_file()
    command([home/'ReadMaster.exe','--check-models',work/'upgrade-model-check.json'])
    report['tests']['upgrade_keeps_user_data']=True;save()
    print('Uninstalling only the isolated test install...',flush=True)
    if not home.resolve().is_relative_to((ROOT/'.build').resolve()):raise RuntimeError('Unsafe uninstall target')
    command([home/'unins000.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/LOG='+str(work/'uninstall.log')])
    assert retained.is_file()
    assert not (home/'ReadMaster.exe').exists()
    assert not (home/'data/comet-models/cometkiwi-int8/weights.pt').exists()
    report['tests']['uninstall_keeps_user_data']=True
    report['passed']=True;save()
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
