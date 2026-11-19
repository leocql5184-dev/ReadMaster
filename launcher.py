import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--comet-mode', choices=('quantized', 'fp32', 'auto'))
    parser.add_argument('--check-models', metavar='REPORT_JSON')
    options, remaining = parser.parse_known_args()
    if options.comet_mode:
        os.environ['READMASTER_COMET_MODE'] = options.comet_mode
    else:
        os.environ.setdefault('READMASTER_COMET_MODE', 'quantized')
    sys.argv[1:] = remaining
    if options.check_models:
        from readmaster.quality import CometBackend
        root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
        report = {'mode': os.environ['READMASTER_COMET_MODE'], 'root': str(root),
                  'frozen': bool(getattr(sys, 'frozen', False)),
                  'scope': 'routing and file presence; weight hashes checked on inference'}
        try:
            backend = CometBackend(root, root / 'data/model-check-cache')
            if not backend.python.is_file() or not backend.worker.is_file():
                raise ValueError('COMET 运行环境未安装，请运行 scripts/setup_comet.py')
            report.update(passed=True, models=backend.models, revisions=backend.revisions,
                          device=backend.device, python=str(backend.python), worker=str(backend.worker))
        except (ValueError, OSError) as exc:
            report.update(passed=False, error=str(exc))
        Path(options.check_models).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0 if report['passed'] else 1
    # Keep the headless service entry point for integrations and development.
    if "--browser" in sys.argv or "--no-browser" in sys.argv:
        if "--browser" in sys.argv:
            sys.argv.remove("--browser")
        from readmaster.server import main as run
    else:
        from readmaster.desktop import main as run
    run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
