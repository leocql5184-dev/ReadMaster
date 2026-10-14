"""Prepare and verify the isolated PDF engine without changing the reader runtime."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--floating', action='store_true', help='Use version ranges instead of the validated Windows lock file.')
    args = parser.parse_args()
    directory = ROOT / '.build' / 'babel-runtime'
    python = directory / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not args.check_only:
        if not python.exists():
            venv.EnvBuilder(with_pip=True).create(directory)
        requirements=ROOT/'requirements-babeldoc-lock.txt'
        if args.floating or not requirements.is_file():
            requirements=ROOT/'requirements-babeldoc.txt'
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True)
    if not python.exists():
        raise SystemExit('BabelDOC runtime has not been prepared.')
    probe = (
        'import json, importlib.metadata as m; '
        'from pdf2zh_next.high_level import do_translate_async_stream; '
        'from pdf2zh_next.config.model import SettingsModel; '
        'print(json.dumps({"versions": {p:m.version(p) for p in '
        '("pdf2zh-next","babeldoc","pymupdf")}, '
        '"stream_api": callable(do_translate_async_stream)}))'
    )
    result = subprocess.run([str(python), '-c', probe], capture_output=True,
                            text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        report = ROOT / '.build' / 'babel-runtime-probe-error.txt'
        report.write_text(result.stderr, encoding='utf-8')
        raise SystemExit(f'Engine import check failed; see {report}')
    report = json.loads(result.stdout.strip().splitlines()[-1])
    report['runtime'] = str(python)
    report['model_calls_tested'] = False
    (ROOT / '.build' / 'babel-runtime-probe.json').write_text(
        json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
