"""Fresh-process, offline validation of serialized XCOMET bundle."""
import json
import os
from pathlib import Path
import time
import ctypes
from ctypes import wintypes

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'.build/xcomet-quantized-validation'
OUT.mkdir(parents=True, exist_ok=True)
os.environ['HF_HOME'] = str(OUT/'empty-hf-home')
os.environ['HF_HUB_CACHE'] = str(OUT/'empty-hf-home/hub')
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
from xcomet_quantized import sha256


def peak_memory():
    class Counters(ctypes.Structure):
        _fields_ = [('cb',wintypes.DWORD),('faults',wintypes.DWORD)] + [(n,ctypes.c_size_t) for n in ('peak_working_set','working_set','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile')]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    current = ctypes.windll.kernel32.GetCurrentProcess
    current.restype = wintypes.HANDLE
    fn = ctypes.windll.psapi.GetProcessMemoryInfo
    fn.argtypes = [wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    if not fn(current(),ctypes.byref(counters),counters.cb):
        raise ctypes.WinError()
    return {'peak_working_set_bytes':counters.peak_working_set,'peak_pagefile_bytes':counters.peak_pagefile}


def main():
    import torch
    import comet_worker
    torch.set_num_threads(6)
    torch.set_num_interop_threads(1)
    manifest = ROOT/'data/comet-models/xcomet-xl-int8/manifest.json'
    samples = json.loads((ROOT/'.build/xcomet-sensitivity/samples.json').read_text(encoding='utf-8'))
    expected = json.loads((ROOT/'.build/xcomet-sensitivity/mixed_protect_top4_heads.json').read_text(encoding='utf-8'))['results']
    # Guard against accidentally loading the original 14GB checkpoint.
    load = torch.load
    loaded = []
    def guarded(path,*args,**kwargs):
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(manifest.parent.resolve()):
            raise RuntimeError('Validation attempted to load weights outside the INT8 bundle')
        loaded.append(str(resolved))
        return load(path,*args,**kwargs)
    torch.load = guarded
    started = time.monotonic()
    actual = comet_worker.run({'model':str(manifest),'device':'cpu','cache_root':str(OUT/'empty-cache'),
                               'data':[{'src':r['src'],'mt':r['mt']} for r in samples]})
    elapsed = time.monotonic()-started
    deltas = [abs(a['score']-b['score']) for a,b in zip(actual,expected)]
    sig = lambda spans:[(s['start'],s['end'],s['severity']) for s in spans]
    changes = sum(sig(a['error_spans'])!=sig(b['spans']) for a,b in zip(actual,expected))
    report = dict(passed=len(actual)==len(expected) and max(deltas)<1e-6 and changes==0,
                  manifest_sha256=sha256(manifest), n=len(actual), max_score_delta=max(deltas),
                  changed_spans=changes, seconds_including_load=elapsed, loaded_weight_files=loaded,
                  empty_hf_cache=True, **peak_memory(),
                  bundle_bytes=sum(p.stat().st_size for p in manifest.parent.rglob('*') if p.is_file()),
                  scope='Serialization/offline reload equivalence on 32 prior samples; not independent quality validation.')
    (OUT/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)
    if not report['passed']:
        raise RuntimeError('INT8 round-trip predictions differ from sensitivity experiment')
    manifest.with_name('verified.json').write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
