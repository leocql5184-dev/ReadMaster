"""Exercise verified Kiwi and XCOMET bundles through the real portable worker."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from readmaster.quality import CometBackend
from readmaster.comet_service import WORKER


def read(path):
    return Path(path).read_text(encoding='utf-8')


def main():
    out = ROOT/'.build/kiwi-quantization'
    os.environ['READMASTER_COMET_DEVICE'] = 'cpu'
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    for key in ('READMASTER_KIWI_MODEL','READMASTER_XCOMET_MODEL','READMASTER_COMET_PYTHON'):
        os.environ.pop(key,None)
    inputs = json.loads(read(out/'evaluation-inputs.json'))
    expected = json.loads(read(out/'int8-evaluation.json'))['results']
    baseline = json.loads(read(out/'fp32-evaluation.json'))['results']
    # Exercise the largest drift, threshold-nearest, and ordinary samples.
    worst = max(range(len(inputs)),key=lambda i:abs(expected[i]['score']-baseline[i]['score']))
    nearest = min(range(len(inputs)),key=lambda i:abs(baseline[i]['score']-.7))
    indices = list(dict.fromkeys([0,1,2,32,worst,nearest]))
    units = [inputs[i] for i in indices]
    WORKER.close()
    try:
        with tempfile.TemporaryDirectory(dir=out,ignore_cleanup_errors=True) as folder:
            backend = CometBackend(ROOT,Path(folder))
            assert all(Path(p).name=='manifest.json' for p in backend.models.values()),backend.models
            actual = backend.predict('kiwi',units)
            max_delta = max(abs(a['score']-expected[i]['score']) for a,i in zip(actual,indices))
            assert max_delta < 1e-6,max_delta
            kiwi_worker = WORKER.last_report
            with patch.object(WORKER,'invoke',side_effect=AssertionError('Cache hit invoked worker')):
                assert backend.predict('kiwi',units) == actual
            xrows = json.loads(read(ROOT/'.build/xcomet-sensitivity/samples.json'))
            xexpected = json.loads(read(ROOT/'.build/xcomet-sensitivity/mixed_protect_top4_heads.json'))['results'][0]
            xactual = backend.predict('xcomet',[xrows[0]])[0]
            assert abs(xactual['score']-xexpected['score']) < 1e-6
            signature = lambda spans:[(s['start'],s['end'],s['severity']) for s in spans]
            assert signature(xactual['error_spans']) == signature(xexpected['spans'])
            report = dict(passed=True,models=backend.models,python=str(backend.python),
                          kiwi_samples=len(indices),kiwi_max_delta=max_delta,kiwi_worker=kiwi_worker,
                          two_model_worker=WORKER.last_report,cache_hit_verified=True,
                          xcomet_regression_score_delta=abs(xactual['score']-xexpected['score']))
            (out/'backend.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False),flush=True)
    finally:
        WORKER.close()


if __name__ == '__main__':
    main()
