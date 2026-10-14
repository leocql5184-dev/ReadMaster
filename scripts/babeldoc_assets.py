"""Redirect only engine assets to the packaged cache before other imports."""
import os
import shutil
from pathlib import Path


def configure():
    root = Path(__file__).resolve().parents[1]
    candidates = [root.parent/'assets', root/'tools/babeldoc/assets']
    assets = next((p for p in candidates if (p/'fonts').is_dir() and (p/'models').is_dir()), None)
    if assets is None:
        return
    # Bundle only redistributable assets. Working PDFs and caches are written
    # under the user's library, so packaging tools never collect their papers.
    default_data = assets.parents[2]/'data'
    cache = Path(os.environ.get('READMASTER_DATA', default_data))/'babeldoc-cache'
    for name in ('fonts', 'models', 'tiktoken'):
        origin = assets/name
        for file in origin.rglob('*'):
            if not file.is_file():
                continue
            destination = cache/name/file.relative_to(origin)
            if not destination.exists() or destination.stat().st_size != file.stat().st_size:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, destination)
    import babeldoc.const as const
    const.CACHE_FOLDER = cache
    const.TIKTOKEN_CACHE_FOLDER = cache/'tiktoken'
    const.TIKTOKEN_CACHE_FOLDER.mkdir(exist_ok=True)
    os.environ['TIKTOKEN_CACHE_DIR'] = str(const.TIKTOKEN_CACHE_FOLDER)
