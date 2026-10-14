"""Small labeled live check; never treats synthetic results as full-paper evidence."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from readmaster.agents import ApiAgent
from readmaster.models import ModelStore
from readmaster.pipeline import review_schema, validate_review
from readmaster.quality import snapshot, PAPER_CHECKLIST, VERSION


CASES = [
    ('meaning', 'The baseline outperforms our method.', '基线优于我们的方法。', '我们的方法优于基线。'),
    ('omission', 'We train the model and evaluate it on unseen data.', '我们训练模型，并在未见过的数据上评估它。', '我们训练模型。'),
    ('term', 'Recall measures the fraction of relevant items retrieved.', '召回率衡量相关项目中被检索出的比例。', '精确率衡量相关项目中被检索出的比例。'),
    ('number', 'We use 25 samples.', '我们使用 25 个样本。', '我们使用 52 个样本。'),
    ('formula', 'The constraint is x > y.', '约束为 x > y。', '约束为 x < y。'),
    ('negation', 'The method does not require labels.', '该方法不需要标签。', '该方法需要标签。'),
    ('condition', 'The guarantee holds only if the data are independent.', '仅当数据相互独立时，该保证才成立。', '无论数据是否独立，该保证均成立。'),
    ('modality', 'The method may improve accuracy.', '该方法可能提高准确率。', '该方法一定提高准确率。'),
    ('reference', 'The results are shown in Table 2.', '结果见表 2。', '结果见表 3。'),
    ('style', 'We use a small dataset.', '我们采用一个小型数据集。', None),
    ('format', 'We use 1,000 samples.', '我们使用 1000 个样本。', None),
]


def run():
    store = ModelStore(ROOT / 'data')
    profile = next(p for p in store.state['profiles'] if 'deepseek' in p.get('endpoint', '').lower())
    config, key = store.snapshot(profile['id'])
    agent = ApiAgent(config, key)
    report = {'version': VERSION, 'model': config['model'], 'synthetic': True,
              'scope': 'prompt acceptance only; no COMET inference or full-paper latency measurement', 'cases': []}
    for faulty in (False, True):
        blocks = []
        # Opaque IDs prevent the category labels from becoming model hints.
        expected = set()
        for i, (_, source, good, bad) in enumerate(CASES):
            ident = f'b{i:03}_001'
            blocks.append({'id': f'b{i:03}', 'page': 1, 'source': source,
                           'sentences': [{'id': ident, 'source': source}],
                           'translations': {ident: bad if faulty and bad else good}})
            if faulty and bad:
                expected.add(ident)
        translations = {k: v for b in blocks for k, v in b['translations'].items()}
        sentences = [s for b in blocks for s in b['sentences']]
        payload = {'sentences': sentences, 'translations': [{'id': k, 'text': v} for k, v in translations.items()],
                   'document': snapshot(blocks, translations), 'terms': [], 'checklist': PAPER_CHECKLIST}
        start = time.monotonic()
        result = validate_review(agent.call('accept', payload, review_schema()), sentences)
        found = {i['id'] for i in result['issues'] if i['severity'] == 'blocking'}
        item = {'faulty': faulty, 'seconds': round(time.monotonic() - start, 2),
                'expected': sorted(expected), 'blocking': sorted(found),
                'missed': sorted(expected - found), 'false_positives': sorted(found - expected), 'result': result}
        report['cases'].append(item)
        print(json.dumps({k: v for k, v in item.items() if k != 'result'}), flush=True)
    report['passed'] = all(not x['missed'] and not x['false_positives'] for x in report['cases'])
    path = ROOT / '.build/expert-review-live.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not report['passed']:
        raise SystemExit('Acceptance prompt fixture failed; inspect the local report.')


if __name__ == '__main__':
    run()
