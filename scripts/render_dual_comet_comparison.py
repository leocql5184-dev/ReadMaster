"""Build local character-level comparison and complete extracted differences."""
import csv
import difflib
import html
import json
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.build/edumirror-dual-comparison'


def read(name):
    return json.loads((OUT/name).read_text(encoding='utf-8'))


def esc(text):
    return html.escape(str(text))


def mdtext(text):
    # Prompt examples contain angle-bracket placeholders, not HTML tags.
    return str(text).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')


def character_diff(a,b):
    left,right=[],[]
    for tag,i,j,k,l in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
        left.append(esc(a[i:j]) if tag=='equal' else '<del>'+esc(a[i:j])+'</del>')
        right.append(esc(b[k:l]) if tag=='equal' else '<ins>'+esc(b[k:l])+'</ins>')
    return ''.join(left),''.join(right)


def main():
    rows=read('all-sentences.json')
    differences=read('differences.json')
    summary=read('summary.json')
    judgments={r['id']:r for r in read('judgments.json')}
    assert set(judgments)=={r['id'] for r in differences},'Every difference requires a judgment'
    counts=Counter(j['category'] for j in judgments.values())
    common=read('common-issues.json') if (OUT/'common-issues.json').exists() else []
    statistics=['| 固定译文 | 原版 Kiwi 均分 | 量化 Kiwi 均分 | 原版 Kiwi >0.7 | 量化 Kiwi >0.7 |',
                '|---|---:|---:|---:|---:|']
    labels={'initial':'共同初译','fp32':'原版双模型修订稿','int8':'量化双模型修订稿'}
    for name,metrics in summary['summaries'].items():
        a,b=metrics['fp32'],metrics['int8']
        statistics.append(f'| {labels[name]} | {a["document_score"]:.6f} | {b["document_score"]:.6f} | {a["above_threshold_percentage"]:.2f}% | {b["above_threshold_percentage"]:.2f}% |')
    intro=['# EduMirror：原版双模型与量化双模型逐句对照','',
           f'PDF 共 56 页。本次处理 {len(rows)} 个可翻译句块；有 {len(differences)} 个最终句块不同，其中 {summary["whitespace_only"]} 个仅空白不同。',
           '两条路线共用初译、术语和提取结果，分别使用原版 Kiwi+XCOMET、混合 INT8 Kiwi+XCOMET，最多两轮修订。',
           '正文及可提取的附录正文纳入；图像、表格单元格、独立公式、参考文献、页眉和不符合正文条件的短块按阅读器规则保留。',
           '两版文本进行逐字符对齐。以下逐项判断依据英文原句及相邻上下文，属于本次文本核对，不是人工专家金标准。单次大模型运行的随机性仍会影响措辞，不能把每个差异都归因于量化。','']+statistics
    intro+=['',f'程序状态：原版 {summary["status"]["fp32"]}；量化 {summary["status"]["int8"]}。未通过的结果不宣称合格。',
            '修订过程使用 Kiwi 分数作筛选，分数上升不是独立质量证明。全文分数按实际评分单元统计；合并单元的分数在成员句块旁重复展示，score_unit 可核对归属。','',
            '差异分类：'+json.dumps(dict(counts),ensure_ascii=False),'']
    md=intro+['## 两条路线共同出现的问题','']
    common_html='<h2>两条路线共同出现的问题</h2>'
    for issue in common:
        md += [f'### 第 {issue["page"]} 页：{issue["title"]}','','**完整原文：** '+mdtext(issue['source']),'','**共同初译：** '+mdtext(issue['initial']),'','**两条路线最终文本：** '+mdtext(issue['final']),'','**判断：** '+mdtext(issue['judgment']),'']
        common_html+=f'<details class="item"><summary><b>第 {issue["page"]} 页：{esc(issue["title"])}</b></summary><p><b>原文：</b>{esc(issue["source"])}</p><p><b>初译：</b>{esc(issue["initial"])}</p><p><b>两份定稿：</b>{esc(issue["final"])}</p><p><b>判断：</b>{esc(issue["judgment"])}</p></details>'
    md+=['## 全部差异摘录','']
    cards=[]
    for row in rows:
        sid=row['id'];different=sid in judgments
        a,b=character_diff(row['fp32'],row['int8'])
        j=judgments.get(sid,{})
        judgment=j.get('judgment','两条路线的最终文本逐字相同。')
        category=j.get('category','完全相同')
        cards.append(f'<article class="item" data-changed="{int(different)}" data-category="{esc(category)}"><header><b>第 {row["page"]} 页 · {esc(sid)}</b><span>{esc(category)}</span></header><p class="source">{esc(row["source"])}</p><div class="versions"><section><h3>原版双模型</h3><p>{a}</p><small>原版 Kiwi {row["fp32_fp32"]:.4f} · 量化 Kiwi {row["fp32_int8"]:.4f}</small></section><section><h3>量化双模型</h3><p>{b}</p><small>原版 Kiwi {row["int8_fp32"]:.4f} · 量化 Kiwi {row["int8_int8"]:.4f}</small></section></div><p class="judgment"><b>判断：</b>{esc(judgment)}</p><details><summary>共同初译 / 评分单元</summary><p>{esc(row["initial"])}</p><small>{esc(row["score_unit"])}</small></details></article>')
        if j.get('context'):
            cards[-1]=cards[-1].replace('</article>','<details open><summary>核对所用的相邻上下文</summary><p>'+esc(j['context'])+'</p></details></article>')
        if different:
            md += [f'### 第 {row["page"]} 页 · {sid} · {category}','',
                   '**原文：** '+mdtext(row['source']),'','**原版双模型：** '+mdtext(row['fp32']),'',
                   '**量化双模型：** '+mdtext(row['int8']),'','**判断：** '+mdtext(judgment),'',
                   f'同一原版 Kiwi 评分：{row["fp32_fp32"]:.6f} → {row["int8_fp32"]:.6f}。','']
            if j.get('context'):
                md+=['**上下文：** '+mdtext(j['context']),'']
    (OUT/'DIFFERENCES.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
    with (OUT/'differences.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['id','page','source','initial','fp32','int8','category','judgment']
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        writer.writerows({**r,**judgments[r['id']]} for r in differences)
    options=''.join(f'<option>{esc(c)}</option>' for c in sorted(counts))
    table='<table><tr><th>固定译文</th><th>原版 Kiwi 均分</th><th>量化 Kiwi 均分</th></tr>'
    for name,m in summary['summaries'].items():
        table+=f'<tr><td>{labels[name]}</td><td>{m["fp32"]["document_score"]:.6f}</td><td>{m["int8"]["document_score"]:.6f}</td></tr>'
    table+='</table>'
    document='''<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>EduMirror 双模型量化对照</title><style>
body{font:16px/1.75 system-ui,"Microsoft YaHei",sans-serif;background:#f4f6f8;color:#192735;margin:0}main{max-width:1380px;margin:auto;padding:28px}h1{font-size:28px}h2{font-size:20px}h3{font-size:15px;margin:0;color:#456}p{white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse;background:white}th,td{border:1px solid #ccd4dc;padding:8px 18px}nav{position:sticky;top:0;background:#f4f6f8ee;padding:12px 0;z-index:1;display:flex;flex-wrap:wrap;gap:14px;align-items:center}input,select{font:inherit;padding:6px;max-width:100%}.item{background:white;border:1px solid #d8e0e6;border-radius:8px;margin:16px 0;padding:20px}.item header{display:flex;justify-content:space-between;gap:20px}.source{background:#f1f5f8;padding:12px}.versions{display:grid;grid-template-columns:1fr 1fr;gap:24px}.versions section{min-width:0}.versions p{margin:8px 0}del{background:#fee1df;color:#8a211b;text-decoration:none}ins{background:#d4f1df;color:#11582f;text-decoration:none}.judgment{border-top:1px solid #e1e6eb;padding-top:12px}small{color:#536372}.hint{color:#536372;max-width:1000px}.hidden{display:none}details{margin-top:10px}button{font:inherit}@media(max-width:760px){main{padding:14px}.versions{grid-template-columns:1fr}.item{padding:14px}h1{font-size:24px}}@media print{nav{display:none}.item{break-inside:avoid}body{background:white}main{padding:0}}</style></head><body><main>'''
    document+=f'<h1>EduMirror：原版双模型与量化双模型</h1><p>共 {len(rows)} 个可翻译句块，最终差异 {len(differences)} 个。红色为原版中的差异，绿色为量化版中的差异。</p><p class="hint">同一初译、同一术语表、每条路线最多两轮修订。两列分数使用同一组评分器交叉计算。仅一次运行，措辞差异可能同时受模型随机性影响。图像、参考文献等保留原文；未通过数值验收的译文仍需复核。</p>'+table
    document+=common_html+'<h2>逐句逐字符对照</h2><nav><label><input type="checkbox" id="only" checked>只看差异</label><label>分类 <select id="category"><option value="">全部分类</option>'+options+'</select></label><input id="search" placeholder="搜索原文、译文或编号" aria-label="搜索"><span id="count"></span></nav>'+''.join(cards)
    document+='''</main><script>const items=[...document.querySelectorAll('article.item')],only=document.getElementById('only'),category=document.getElementById('category'),search=document.getElementById('search'),count=document.getElementById('count');function filter(){let n=0;const q=search.value.toLowerCase();for(const x of items){const show=(!only.checked||x.dataset.changed==='1')&&(!category.value||x.dataset.category===category.value)&&(!q||x.textContent.toLowerCase().includes(q));x.classList.toggle('hidden',!show);if(show)n++;}count.textContent='显示 '+n+' 条';}for(const el of [only,category,search])el.addEventListener('input',filter);filter();</script></body></html>'''
    (OUT/'comparison.html').write_text(document,encoding='utf-8')
    for arm in ('fp32','int8'):
        lines=['# '+labels[arm],'', '状态：'+summary['status'][arm]+'；供对照阅读，不代表已通过验收。','']
        for r in rows:
            lines.extend([f'<!-- page {r["page"]}, {r["id"]} -->',mdtext(r[arm]),''])
        (OUT/(arm+'-translation.md')).write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(rows=len(rows),differences=len(differences),categories=dict(counts)),ensure_ascii=False))


if __name__=='__main__':
    main()
