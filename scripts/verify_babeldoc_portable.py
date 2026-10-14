"""Check engine relocation without system Python/Node or user login files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent.parent


def main():
    original = ROOT/'tools/babeldoc'
    destination = ROOT/'.build'/('便携 验证-'+uuid.uuid4().hex[:8])
    runtime = destination/'tools/babeldoc'
    # Immutable engine files can share storage; caches go to a separate data
    # directory. This verifies resolution after moving to a Unicode/space path.
    shutil.copytree(original, runtime, copy_function=os.link,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    environment = os.environ.copy()
    environment['PATH'] = str(Path(environment.get('SystemRoot', 'C:/Windows'))/'System32')
    environment['READMASTER_DATA'] = str(destination/'data')
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'OPENAI_API_KEY', 'DEEPSEEK_API_KEY'):
        environment.pop(key, None)
    result = subprocess.run([str(runtime/'python.exe'),
                             str(runtime/'app/scripts/babeldoc_template.py')],
                            cwd=destination, env=environment, capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=180,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    (destination/'worker.log').write_text(result.stdout+'\n'+result.stderr, encoding='utf-8')
    report = {'directory': str(destination), 'system_python_required': False,
              'unicode_and_spaces': True, 'pdf_export_tested': False,
              'passed': result.returncode == 0}
    if report['passed']:
        template = json.loads(result.stdout.strip().splitlines()[-1])
        report['babeldoc'] = template['version']
        report['passed'] = template['version'] == '0.5.24'
    (ROOT/'.build/babel-portable-relocation.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
