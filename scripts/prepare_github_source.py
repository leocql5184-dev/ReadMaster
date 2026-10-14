"""Export an allowlisted source snapshot without Git history or local runtime data."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = {'readmaster', 'web', 'scripts', 'tests', 'prompts', 'licenses'}
SOURCE_EXTENSIONS = {'.py', '.js', '.cjs', '.html', '.css', '.md', '.txt', '.json'}
PRIVATE_NOTES = {'版本备份.md', '产品概述.md', 'PRODUCT.md', '竞品调研与产品评估.md',
                 '实现方案.md', 'Codex多智能体技术路线.md', '翻译流程决策.md',
                 '优化待办.md', 'DESIGN.md'}
PATTERNS = {
    'credential token': re.compile(r'(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|hf_[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{30,}|eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,})'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'personal home path': re.compile(r'[A-Za-z]:[/\\]+Users[/\\]+[^/\\\s\'"<>]+' + '|/' + r'(?:Users|home)/[^/\s]+'),
}


def allowed(path):
    parts = path.parts
    if any(part.startswith('.') for part in parts):
        return str(path) == '.gitignore'
    if any(part in {'__pycache__', 'paper_sets', 'node_modules'} for part in parts):
        return False
    if len(parts) == 1:
        return path.name in {'LICENSE', 'launcher.py'} or (
            path.suffix in {'.md', '.txt'} and path.name not in PRIVATE_NOTES)
    return (parts[0] in SOURCE_DIRS and path.suffix in SOURCE_EXTENSIONS
            or parts[:2] == ('assets', 'readme') and path.suffix == '.svg'
            or path.as_posix() in {'assets/ReadMaster.ico', 'tools/codex/codex-package.json'})


def main():
    candidates = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT
    ).decode('utf-8').split('\0')
    files = sorted({Path(name) for name in candidates if name and allowed(Path(name))})
    contents = {}
    for relative in files:
        source = ROOT / relative
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
            raise RuntimeError(f'Unsafe source path: {relative}')
        data = source.read_bytes()
        if relative.suffix != '.ico':
            text = data.decode('utf-8-sig')
            for label, pattern in PATTERNS.items():
                if pattern.search(text):
                    # Never include the matched secret in diagnostics.
                    raise RuntimeError(f'{label} detected in {relative}')
        contents[relative] = data
    destination = ROOT / 'output' / ('github-source-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    destination.mkdir(parents=True, exist_ok=False)
    for relative, data in contents.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    archive = destination.with_suffix('.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
        for relative in contents:
            zipped.write(destination / relative, 'ReadMaster/' + relative.as_posix())
    report = {'source_folder': str(destination.relative_to(ROOT)),
              'archive': str(archive.relative_to(ROOT)), 'files': len(contents),
              'archive_bytes': archive.stat().st_size,
              'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
              'includes_git_history': False, 'pattern_scan': 'passed',
              'excluded': ['local credentials and sessions', 'runtime data and papers',
                           'models and environments', 'builds and backups', 'local backup notes']}
    destination.with_suffix('.audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
