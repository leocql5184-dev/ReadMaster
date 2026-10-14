"""Prepare an isolated CPU runtime without changing the reader/BabelDOC packages."""
import argparse
from pathlib import Path
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    root = args.root.resolve()
    environment = root / '.venv-comet'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    subprocess.run([str(python), '-m', 'pip', 'install', '--index-url', 'https://download.pytorch.org/whl/cpu', 'torch==2.6.0'], check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(root/'requirements-comet.txt')], check=True)
    subprocess.run([str(python), '-c', 'import torch, comet; print("COMET runtime ready", torch.__version__)'], check=True)
    print('模型需单独下载；受限模型需要在 Hugging Face 接受条件并登录。')


if __name__ == '__main__':
    main()
