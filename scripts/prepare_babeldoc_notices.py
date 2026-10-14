"""Record the actual engine/font distributions, notices and asset integrity."""
import hashlib
from importlib.metadata import distributions
import json
import re
from pathlib import Path
from fontTools.ttLib import TTFont
from babeldoc.assets import embedding_assets_metadata as metadata

ROOT=Path(__file__).resolve().parent.parent


def main():
    runtime=ROOT/'tools/babeldoc'
    licenses=runtime/'licenses'
    licenses.mkdir(exist_ok=True)
    packages=[]
    for dist in distributions(path=[str(runtime/'Lib/site-packages')]):
        packages.append({'name':dist.metadata['Name'],'version':dist.version,
                         'license':dist.metadata.get('License-Expression') or dist.metadata.get('License'),
                         'project_urls':dist.metadata.get_all('Project-URL') or [],
                         'license_files':[str(f) for f in dist.files or [] if 'license' in str(f).lower()]})
    (runtime/'dependencies.json').write_text(json.dumps(sorted(packages,key=lambda d:d['name'].lower()),
                                               ensure_ascii=False,indent=2),encoding='utf-8')
    fonts=[]
    for file in sorted((runtime/'assets/fonts').glob('*.ttf')):
        with TTFont(file) as font:
            notices={str(kind):sorted({n.toUnicode() for n in font['name'].names if n.nameID==kind})
                     for kind in (0,13,14)}
        fonts.append({'file':file.name,'embedded_notices':notices,
                      'source':'https://github.com/funstory-ai/BabelDOC-Assets/tree/main/fonts',
                      'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
    (licenses/'Font-embedded-notices.json').write_text(json.dumps(fonts,ensure_ascii=False,indent=2),encoding='utf-8')
    assets=[]
    token_urls=re.findall(r'https://[^"\s]+',
        (runtime/'Lib/site-packages/tiktoken_ext/openai_public.py').read_text(encoding='utf-8'))
    for file in sorted((runtime/'assets').rglob('*')):
        if file.is_file():
            content=file.read_bytes()
            if file.parent.name=='fonts':
                expected=metadata.EMBEDDING_FONT_METADATA[file.name]['sha3_256']
                source=metadata.FONT_URL_BY_UPSTREAM['github'](file.name)
            elif file.name=='doclayout_yolo_docstructbench_imgsz1024.onnx':
                expected=metadata.DOCLAYOUT_YOLO_DOCSTRUCTBENCH_IMGSZ1024ONNX_SHA3_256
                source=metadata.DOC_LAYOUT_ONNX_MODEL_URL['huggingface']
            elif file.name=='ch_PP-OCRv4_det_infer.onnx':
                expected=metadata.TABLE_DETECTION_RAPIDOCR_MODEL_SHA3_256
                source=metadata.TABLE_DETECTION_RAPIDOCR_MODEL_URL['huggingface']
            else:
                expected=metadata.TIKTOKEN_CACHES[file.name]
                source=next(url for url in token_urls if hashlib.sha1(url.encode()).hexdigest()==file.name)
            actual=hashlib.sha3_256(content).hexdigest()
            if actual!=expected:
                raise RuntimeError('Engine resource does not match pinned upstream digest: '+file.name)
            assets.append({'file':file.relative_to(runtime).as_posix(),'bytes':file.stat().st_size,
                           'sha256':hashlib.sha256(content).hexdigest(),'source':source,
                           'upstream_sha3_256_verified':actual})
    (runtime/'assets-manifest.json').write_text(json.dumps(assets,indent=2),encoding='utf-8')
    print(json.dumps({'packages':len(packages),'fonts':len(fonts),'assets':len(assets)}))


if __name__=='__main__':
    main()
