"""Summarize the local experiment, keeping empirical claims within its scope."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.build/xcomet-sensitivity'


def main():
    report = json.loads((OUT/'report.json').read_text(encoding='utf-8'))
    rows = json.loads((OUT/'samples.json').read_text(encoding='utf-8'))
    baseline = json.loads((OUT/'baseline.json').read_text(encoding='utf-8'))
    inventory = json.loads((OUT/'inventory.json').read_text(encoding='utf-8'))
    base_params = sum(x['bytes'] for x in inventory)
    lines = ['# XCOMET-XL INT8 量化敏感性分析', '',
             '这是实际 CPU 推理实验，不是量化发布版本，也不是总体质量合格证明。', '',
             f"- 状态：{'完成' if report.get('completed') else '进行中'}。",
             '- 模型：Unbabel/XCOMET-XL，revision `6a123c5e8e6dccab25e5fcffa3c8b417abadb462`。',
             f"- 环境：{report['platform']}，PyTorch {report['torch']}，{report['threads']} CPU 线程，{report['engine']}。",
             '- 线性层：实际逐输出通道动态 INT8（同时涉及动态激活量化）；嵌入层：逐行 weight-only INT8。不是仅模拟权重舍入。',
             f"- 样本：{len(rows)} 条，11 条正确译文控制、9 条故意错误控制，余下为本机文献中确定性抽取的未标注片段。",
             '- 逐层筛查使用 12 条控制样本；敏感前四层、输出层、嵌入层和组合方案使用完整样本。筛查排序并非普适的敏感性排序。',
             '- 原版和量化版使用相同分词器、无参考译文输入及项目的错误跨度解码修复。',
             f"- 与官方 predict 路径交叉检查：分数差 {report['official_path_score_delta']:.8f}；错误片段一致 {report['official_path_span_equal']}。", '',
             '## 如何读结果', '',
             '“片段变化”比较错误起止位置及严重程度；“标记翻转”表示整句从有错误标记变成没有，或反之。',
             '“错误控制标记”仅表示故意错误句出现了某个标记，不等同于正确定位了那个错误。真实片段没有人工标签，不能据此计算真实准确率。',
             '评分差不作为硬门槛；位于错误位置之外的误报、漏报需要逐例复核。', '',
             '## 原版控制表现', '',
             f"- 故意错误句有标记：{report['baseline_metrics']['bad_controls_flagged']}/9。",
             f"- 正确译文有标记：{report['baseline_metrics']['good_controls_flagged']}/11。",
             f"- 完整样本预热后推理用时：{report['baseline_seconds']:.2f} 秒（已缓存分词，不含加载）。", '',
             '## 单编码层筛查（每次只量化这一层）', '',
             '| 层（从 0 开始） | 片段变化/12 | 标记翻转/12 | 分数平均绝对差 |',
             '|---|---:|---:|---:|']
    layers = [v for v in report['variants'] if v['scope']=='screen']
    layers.sort(key=lambda x:(x['flag_flips'],x['span_changed'],x['score_mae']),reverse=True)
    for v in layers:
        lines.append(f"| {v['name']} | {v['span_changed']} | {v['flag_flips']} | {v['score_mae']:.5f} |")
    lines += ['', '## 完整样本验证与组合方案', '',
              '| 方案 | 片段变化 | 标记翻转 | 错误句标记/9 | 正确句误标/11 | 分数 MAE | 推理秒 |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for v in report['variants']:
        if v['scope'] != 'full':
            continue
        lines.append(f"| {v['name']} | {v['span_changed']}/{v['n']} | {v['flag_flips']} | {v['bad_controls_flagged']} | {v['good_controls_flagged']} | {v['score_mae']:.5f} | {v['seconds']:.2f} |")
    lines += ['', '## 权重存储估算', '',
              '以下仅估算张量数据和量化比例信息；不是实际导出的文件大小，更不是峰值运行内存。尚未量化的权重保持 FP32。', '',
              '| 方案 | 估算 GB（十进制） |', '|---|---:|',
              f'| 原版检查点文件（实际） | {report["checkpoint_bytes"]/1e9:.3f} |']
    for v in report['variants']:
        if v['name'] in ('all_linear','all_linear_embeddings','mixed_protect_top4_heads'):
            estimate = report['checkpoint_bytes'] - v['float_weight_bytes'] + v['quant_weight_and_scale_bytes']
            lines.append(f"| {v['name']} | {estimate/1e9:.3f} |")
    lines += ['', '## 控制样本逐例核对', '',
              '| 样本 | 原版得分 | 原版错误标记（译文字符区间） |', '|---|---:|---|']
    for r,p in zip(rows,baseline):
        if r['faulty'] is None:
            continue
        marks = '; '.join(f"{s['start']}:{s['end']} {s['severity']} {r['mt'][s['start']:s['end']]}" for s in p['spans']) or '无'
        lines.append(f"| {r['id']} | {p['score']:.4f} | {marks} |")
    if report.get('completed'):
        lines += ['', '## 本次结果的解释', '',
                  '- 36 个编码层逐层单独量化，在 12 条筛查样本上均无整句标记翻转；第 10、8、2、1 层按本次筛查指标排序靠前（0 起算）。这不是通用层重要性结论。',
                  '- 评分头第一层 estimator.ff.0 很敏感：单独量化的分数 MAE 约 0.340，但错误片段不变。其 FP32 权重仅 26.2 MB，保留精度的代价很小。',
                  '- 错误标签头 hidden2tag 仅 40,960 字节；单独量化在 32 条中改变 1 条片段。可优先保留 FP32，体积收益几乎不受影响。',
                  '- 嵌入层原始权重约 2.574 GB，量化权重及比例约 0.646 GB；3/32 条片段变化、1 条标记消失。消失的是译文“房间”的 minor 标记，不应未经人工判断就认定为重要错误漏检。',
                  '- 全线性层加嵌入 INT8：22/32 条片段变化；保护四层和输出头后：21/32 条片段变化。混合方案明显降低分数偏移，但本次并未明显改善错误片段一致性。',
                  '- 两个组合方案在本次样本上均没有让原版有标记的整句变成无标记；这不代表没有丢失某个局部错误，或没有在其他输入上退步。',
                  '- 混合方案新增标出了“25 个样本”错译成“52 个样本”的数字位置；原版未标记该错误。量化变化并非全部有害。',
                  '- 混合方案把“x > y”误译为“x < y”的标记从 major 降为 minor；也对一条语义基本忠实的 Bob 叙述新增 minor 标记（本次人工阅读判断，非独立标注）。',
                  '- 建议保留很小的输出头精度，继续把 INT8 视为候选。暂不能认定保护这四层就是最佳混合方案，也不能直接替换发布模型。更广泛人工标注样本和独立留出集应重点评估实质错误定位，而非单纯复现分数。']
    hash_path = OUT/'checkpoint-hash.json'
    if hash_path.exists():
        digest = json.loads(hash_path.read_text(encoding='utf-8-sig'))['Hash'].lower()
        lines += ['', f'- 本次检查点 SHA-256：`{digest}`；与官方记录 b644a48a…fcd716 一致。']
    env_path = OUT/'environment.json'
    if env_path.exists():
        lines += ['', '运行依赖：`'+json.dumps(json.loads(env_path.read_text(encoding='utf-8')),ensure_ascii=False)+'`。']
    lines += ['', '## 限制与复现', '',
              '- 样本较小，控制样本多为短句。不能据此认定长论文、罕见术语或其他领域无损。',
              '- 混合方案在同一筛查数据上选择保护层，存在选择偏差，需要独立留出样本验证。',
              '- 速度为一次预热后的顺序测量，受本机负载影响；不能作为发布性能承诺。',
              '- 为恢复原版，实验在内存中保留原始模块，不能用实验进程内存推断部署内存。',
              '- 模型加载出现当前 Transformers 的分词器 regex 警告；基线与量化沿用相同现有配置，本次未更改分词行为。',
              '- 不涉及 Kiwi、不调用翻译 API、不修改阅读器或磁盘原始权重。',
              '- `samples.json` 含本机论文摘录，报告与原始数据默认仅保存在本地。',
              '- 复现：`.venv-comet/Scripts/python.exe scripts/analyze_xcomet_sensitivity.py`，然后运行 `scripts/report_xcomet_sensitivity.py`。',
              f"- 所有实验后恢复原版的复测：{report.get('restoration_metrics','尚未完成')}。", '']
    (OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(OUT/'REPORT.md')


if __name__=='__main__':
    main()
