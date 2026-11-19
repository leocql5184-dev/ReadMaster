"""Fresh-process offline reload and fixed-paper score drift validation."""
import csv
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'.build/kiwi-quantization'
BUNDLE = ROOT/'data/comet-models/cometkiwi-int8/manifest.json'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path,data):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temp.replace(path)


def peak_memory():
    import ctypes
    from ctypes import wintypes
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
    return dict(peak_working_set_bytes=counters.peak_working_set,peak_pagefile_bytes=counters.peak_pagefile)


def worker(kind):
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    cache = ROOT/'data/comet-models' if kind == 'fp32' else OUT/'empty-cache'
    os.environ['HF_HOME'] = str(cache/'home')
    os.environ['HF_HUB_CACHE'] = str(cache/'hub')
    import torch
    import comet_worker
    torch.set_num_threads(6)
    torch.set_num_interop_threads(1)
    rows = read(OUT/'evaluation-inputs.json')
    model = str(BUNDLE) if kind == 'int8' else str(ROOT/'data/comet-models/hub/models--Unbabel--wmt22-cometkiwi-da/snapshots/1ad785194e391eebc6c53e2d0776cada8f83179a/checkpoints/model.ckpt')
    loaded = []
    original_load = torch.load
    def guarded(path,*args,**kwargs):
        name = getattr(path,'name',path) if not isinstance(path,(str,os.PathLike)) else path
        resolved = Path(name).resolve()
        if kind == 'int8' and not resolved.is_relative_to(BUNDLE.parent.resolve()):
            raise RuntimeError('Quantized validation attempted to read weights outside bundle')
        loaded.append(str(resolved))
        return original_load(path,*args,**kwargs)
    torch.load = guarded
    start = time.monotonic()
    actual = comet_worker.run(dict(model=model,device='cpu',cache_root=str(cache),
                                  data=[{'src':r['src'],'mt':r['mt']} for r in rows]))
    save(OUT/(kind+'-evaluation.json'),dict(results=actual,seconds_including_load=time.monotonic()-start,
                                         loaded_weights=loaded,empty_cache=kind=='int8',
                                         **peak_memory()))


def main():
    from analyze_kiwi_quantization import drift
    from xcomet_quantized import sha256
    from scipy.stats import spearmanr
    manifest = read(BUNDLE)
    controls = read(OUT/'samples.json')
    with (ROOT/'.build/xcomet-paper-comparison/scores.csv').open(encoding='utf-8-sig',newline='') as f:
        paper = list(csv.DictReader(f))
    inputs = []
    lookup = {}
    def add(src,mt):
        key = (src,mt)
        if key not in lookup:
            lookup[key] = len(inputs)
            inputs.append(dict(src=src,mt=mt))
        return lookup[key]
    control_indices = [add(r['src'],r['mt']) for r in controls]
    paper_indices = {branch:[add(r['source'],r[branch]) for r in paper] for branch in ('initial','fp32','int8')}
    save(OUT/'evaluation-inputs.json',inputs)
    for kind in ('fp32','int8'):
        print('Running fresh offline '+kind+' scorer on '+str(len(inputs))+' unique pairs',flush=True)
        with (OUT/(kind+'-evaluation.log')).open('w',encoding='utf-8') as log:
            subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker',kind],cwd=ROOT,
                           stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    reference = read(OUT/'fp32-evaluation.json')
    candidate = read(OUT/'int8-evaluation.json')
    base = [r['score'] for r in reference['results']]
    quant = [r['score'] for r in candidate['results']]
    assert len(base) == len(quant) == len(inputs)
    expected = read(OUT/(manifest['recipe']+'.json'))['scores']
    reload_delta = max(abs(quant[i]-score) for i,score in zip(control_indices,expected))
    baseline_expected = read(OUT/'baseline.json')
    baseline_delta = max(abs(base[i]-score) for i,score in zip(control_indices,baseline_expected))
    unique_paper = sorted({i for group in paper_indices.values() for i in group})
    stats = drift([quant[i] for i in unique_paper],[base[i] for i in unique_paper])
    # Engineering tolerances, declared before inspecting the fixed-paper results.
    # These measure fidelity to the original scorer, not translation correctness.
    fidelity_passed = (stats['mae'] <= .01 and stats['p95'] <= .03 and stats['max_abs'] <= .06
                       and stats['flips_07']/stats['n'] <= .05)
    correlation = float(spearmanr([base[i] for i in unique_paper],[quant[i] for i in unique_paper]).statistic)
    branch_stats = {}
    records = []
    for branch,indices in paper_indices.items():
        a,b = [quant[i] for i in indices],[base[i] for i in indices]
        def summary(scores):
            count = sum(s > .7 for s in scores)
            return dict(mean=sum(scores)/len(scores),above_07=count,below_05=sum(s < .5 for s in scores),
                        score_gate_passed=count/len(scores)>=.97 and min(scores)>=.5)
        branch_stats[branch] = dict(fp32=summary(b),int8=summary(a),drift=drift(a,b),
                                   old_baseline_max_delta=max(abs(base[i]-float(r[branch+'_kiwi'])) for i,r in zip(indices,paper)))
        for row,i in zip(paper,indices):
            records.append(dict(branch=branch,id=row['id'],source=row['source'],translation=row[branch],
                                fp32=base[i],int8=quant[i],delta=quant[i]-base[i],
                                crossed_07=(base[i]>.7)!=(quant[i]>.7),crossed_05=(base[i]<.5)!=(quant[i]<.5)))
    report = dict(recipe=manifest['recipe'],manifest_sha256=sha256(BUNDLE),
                  passed=reload_delta<1e-6 and baseline_delta<1e-6 and fidelity_passed,
                  roundtrip_max_delta=reload_delta,baseline_reproduction_max_delta=baseline_delta,
                  fidelity_passed=fidelity_passed,limits=dict(mae=.01,p95=.03,max_abs=.06,flip_07_fraction=.05),
                  unique_paper_pairs=len(unique_paper),paper_overlap_with_diagnostics=len(set(unique_paper)&set(control_indices)),
                  paper_drift=stats,spearman=correlation,branches=branch_stats,
                  source_bytes=read(OUT/'sensitivity.json')['source_bytes'],weights_bytes=manifest['weights_bytes'],
                  bundle_bytes=sum(p.stat().st_size for p in BUNDLE.parent.rglob('*') if p.is_file() and p.name!='verified.json'),
                  protected_layers=manifest['protected_layers'],
                  fp32_runtime={k:v for k,v in reference.items() if k!='results'},
                  int8_runtime={k:v for k,v in candidate.items() if k!='results'},
                  scope='Fixed existing translations scored locally; no new translation/revision, no human gold labels. Three versions share most text, not 600 independent samples.')
    save(OUT/'validation.json',report)
    with (OUT/'scores.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer = csv.DictWriter(f,fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    body = ['<!doctype html><html lang="zh"><meta charset="utf-8"><title>COMETKiwi 量化评分对照</title><style>body{font:16px/1.6 system-ui;margin:24px;color:#17212b}table{border-collapse:collapse;width:100%}td,th{padding:10px;border:1px solid #ccd3dc;vertical-align:top}tr.flip{background:#fff1ce}td.text{min-width:260px;white-space:pre-wrap}</style><h1>固定译文：COMETKiwi 原版与 INT8 评分对照</h1><p>三份译文各 200 个单元，大部分文本重复；黄色表示跨过 0.7 或 0.5。分数变化不是译文变化。</p><table><tr><th>译文版本 / ID</th><th>原文</th><th>固定译文</th><th>原版分数</th><th>INT8 分数</th><th>差值</th></tr>']
    for r in records:
        css = 'flip' if r['crossed_07'] or r['crossed_05'] else ''
        body.append(f'<tr class="{css}"><td>{html.escape(r["branch"]+" / "+r["id"])}</td><td class="text">{html.escape(r["source"])}</td><td class="text">{html.escape(r["translation"])}</td><td>{r["fp32"]:.6f}</td><td>{r["int8"]:.6f}</td><td>{r["delta"]:+.6f}</td></tr>')
    (OUT/'comparison.html').write_text(''.join(body)+'</table></html>',encoding='utf-8')
    lines = ['# COMETKiwi 混合 INT8：固定译文评分对照','',
             '只改变评分器，译文保持不变；原版评分不是人工金标准。使用之前论文的初译、原版 XCOMET 修订稿和 INT8 XCOMET 修订稿，各 200 个单元。',
             f'去重后 {len(unique_paper)} 个不同原文/译文对；与 32 条配方诊断样本重合 {report["paper_overlap_with_diagnostics"]} 对。三份译文不能算 600 个独立样本。','',
             '| 译文版本 | 原版 Kiwi 均分 | INT8 Kiwi 均分 | 平均绝对分差 | 跨 0.7 数量 | 原版/INT8 超过 0.7 数量 |',
             '|---|---:|---:|---:|---:|---:|']
    for name,s in branch_stats.items():
        lines.append(f'| {name} | {s["fp32"]["mean"]:.6f} | {s["int8"]["mean"]:.6f} | {s["drift"]["mae"]:.6f} | {s["drift"]["flips_07"]} | {s["fp32"]["above_07"]}/{s["int8"]["above_07"]} |')
    lines += ['',f'配方：`{manifest["recipe"]}`；保留 FP32 编码层（从 0 开始）：{manifest["protected_layers"]}，评分头保留 FP32，其余线性层和嵌入层 INT8。',
              f'原权重 {report["source_bytes"]:,} 字节，量化权重 {manifest["weights_bytes"]:,} 字节，完整包约 {report["bundle_bytes"]/1e9:.3f} GB。',
              f'独立进程、空 HF 缓存，只加载量化包；32 条导出前/重载后最大分差 {reload_delta:.9f}。',
              f'去重论文集：平均绝对分差 {stats["mae"]:.6f}，P95 {stats["p95"]:.6f}，最大 {stats["max_abs"]:.6f}，Spearman {correlation:.6f}。',
              f'0.7 阈值翻转 {stats["flips_07"]}/{stats["n"]}，其中升到阈值以上 {stats["promoted_07"]}、降到阈值以下 {stats["demoted_07"]}；0.5 阈值翻转 {stats["flips_05"]}。',
              '', '接入容差预设为 MAE ≤ 0.01、P95 ≤ 0.03、最大绝对分差 ≤ 0.06、跨 0.7 比例 ≤ 5%；这是本地工程标准，不是学术质量认证。',
              f'容差通过：{fidelity_passed}；重载与容差整体通过：{report["passed"]}。仅全部通过后写入自动接入标记。',
              '', '## 限制', '',
              '只测试这篇英译中论文与小规模诊断集。没有重新翻译、没有重新运行完整审校链，也没有改变 0.7/0.5 或 97% 门槛。跨阈值可能改变以后送交 XCOMET 和修订的片段；本结果不证明端到端译文无损。',
              '初译、两份修订稿在两个评分器下均需按实际 score_gate_passed 判断，具体见 validation.json。运行耗时含启动、加载、校验和预测，不是纯推理加速比。',
              '原始逐项数据：scores.csv；可读对照：comparison.html；敏感性与各候选配方：sensitivity.json。',
              '', '依据：[官方模型卡](https://huggingface.co/Unbabel/wmt22-cometkiwi-da)；本机固定 unbabel-comet 2.2.7、PyTorch 2.6.0+cpu 的实现。']
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('passed','recipe','weights_bytes','roundtrip_max_delta','paper_drift','branches')}),flush=True)
    if not report['passed']:
        raise RuntimeError('Validation failed; bundle NOT activated automatically')
    save(BUNDLE.with_name('verified.json'),report)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--worker']:
        worker(sys.argv[2])
    else:
        main()
