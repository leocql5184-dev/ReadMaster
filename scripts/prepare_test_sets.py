"""Create paper-derived JSON fixtures, keeping the user's source PDF untouched."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from readmaster.pdf import extract_pdf
import pymupdf

parser=argparse.ArgumentParser()
parser.add_argument('pdf')
args=parser.parse_args()
data=extract_pdf(args.pdf)
out=Path('tests/paper_sets')
out.mkdir(parents=True,exist_ok=True)
sets={'abstract':[1],'figure-and-body':[2,3],'table-and-results':[5,6,7,8], 'references':[11,12,13], 'appendix':[14,16,32]}
for name,pages in sets.items():
    subset={'name':name,'pages':pages,'blocks':[b for b in data['blocks'] if b['page'] in pages]}
    (out/f'{name}.json').write_text(json.dumps(subset,ensure_ascii=False,indent=2),encoding='utf-8')
    with pymupdf.open(args.pdf) as original, pymupdf.open() as selected:
        for page in pages:
            selected.insert_pdf(original, from_page=page-1, to_page=page-1)
        selected.save(out/f'{name}.pdf', garbage=4, deflate=True)
summary={'total_pages':len(data['pages']),'total_blocks':len(data['blocks']),
         'translatable_blocks':sum(bool(b['sentences']) for b in data['blocks']),
         'per_page':{p['number']:sum(b['page']==p['number'] and bool(b['sentences']) for b in data['blocks']) for p in data['pages']}}
(out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary))
