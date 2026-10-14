"""Verify isolated COMET imports after relocation, without copying user credentials."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    directory = ROOT/'.build'/('COMET 便携验证-'+uuid.uuid4().hex[:8])
    runtime = directory/'tools/comet'
    shutil.copytree(ROOT/'tools/comet',runtime,copy_function=os.link,
                    ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    env = dict(os.environ)
    env['PATH'] = str(Path(env.get('SystemRoot','C:/Windows'))/'System32')
    env['HF_HOME'] = str(directory/'private-hf-home')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    for key in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','HF_TOKEN','HUGGINGFACE_HUB_TOKEN'):
        env.pop(key,None)
    code = ('import json,torch,comet,importlib.metadata as m; '
            'from huggingface_hub import get_token; '
            'import comet_worker; '
            'print(json.dumps({"torch":torch.__version__,"comet":m.version("unbabel-comet"),'
            '"credential_present":bool(get_token()),"length_validation_available":'
            'callable(comet_worker.validate_lengths)}))')
    result = subprocess.run([str(runtime/'python.exe'),'-c',code],cwd=directory,env=env,
                            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    (directory/'worker.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Relocated COMET import failed; inspect '+str(directory/'worker.log'))
    report=json.loads(result.stdout.strip().splitlines()[-1])
    assert report['torch']=='2.6.0+cpu' and report['comet']=='2.2.7'
    assert report['credential_present'] is False and report['length_validation_available'] is True
    report.update(passed=True,directory=str(directory),unicode_and_spaces=True,
                  system_python_required=False,model_inference_tested=False)
    (ROOT/'.build/comet-portable-relocation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
