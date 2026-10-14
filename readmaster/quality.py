"""Document-owned QE controller. Scores always come from actual COMET inference."""
import copy
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading

from .pipeline import (translation_schema, review_schema, validate_translations,
                       validate_review, CHECKLIST, adjacent_ids)

VERSION = 'comet-document-v6-expert'
# Acceptance policy changes do not change the actual model inputs or weights.
METRIC_INPUT_VERSION = 'comet-document-v2'
THRESHOLD = .7
REQUIRED_PERCENTAGE = 97
MINIMUM_ALLOWED_SCORE = .5
ACCEPTANCE_POLICY = {'threshold': THRESHOLD, 'threshold_operator': '>',
                     'required_percentage': REQUIRED_PERCENTAGE,
                     'minimum_allowed_score': MINIMUM_ALLOWED_SCORE, 'minimum_allowed_operator': '>='}


def score_summary(scores):
    values = list(scores.values())
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError('评分必须是有限数值')
    above = sum(v > THRESHOLD for v in values)
    below_floor = sum(v < MINIMUM_ALLOWED_SCORE for v in values)
    return {**ACCEPTANCE_POLICY, 'scored_units': len(values), 'above_threshold_count': above,
            'above_threshold_percentage': above * 100 / len(values) if values else None,
            'below_floor_count': below_floor,
            'score_gate_passed': bool(values) and above * 100 >= REQUIRED_PERCENTAGE * len(values) and not below_floor,
            'minimum_score': min(values) if values else None,
            'document_score': sum(values) / len(values) if values else None,
            'score_aggregation': 'arithmetic_mean; acceptance_requires_97_percent_gt_0.7_and_all_ge_0.5'}


def score_issue_ids(scores):
    summary = score_summary(scores)
    ratio_failed = summary['above_threshold_count'] * 100 < REQUIRED_PERCENTAGE * len(scores)
    return {ident for ident, value in scores.items()
            if value < MINIMUM_ALLOWED_SCORE or (ratio_failed and value <= THRESHOLD)}

PAPER_CHECKLIST = CHECKLIST + (' 数字/单位/百分比；公式变量/上下标/比较符号；引用/图表/定理编号；'
    'document.blocks.verified_numeric_source 依据原 PDF 的字形大小和基线恢复数字上标；'
    '遇到展平文本 103 与该字段 10^3 不同时，按有版面证据的 10^3 核查，不把正确指数改回整数。'
    '全文术语和缩写定义一致；漏译/重复；跨段指代/逻辑；实验结论的否定/条件/范围/语气。'
    '术语释义留在术语表，不在原文没有解释的句块中反复插入定义或括号说明。'
    '普通名词和动词应译为自然中文，不能因自动术语表而当成专名或代码漏译；按论文语境核实词义。'
    '原文是事实依据，XCOMET 预测只是待核实线索；误报不改，证据不足列 warning。')
_METRIC_LOCK = threading.Lock()


def snapshot(blocks, translations):
    """Stable document order and IDs, including untranslated non-body regions."""
    return {'version': VERSION, 'blocks': [
        {'id': b['id'], 'page': b['page'], 'source': b['source'],
         'verified_numeric_source': numeric_source(b),
         'translation': ''.join(translations[s['id']] for s in b['sentences'])
                        if b['sentences'] else b['source'],
         'sentences': [{'id': s['id'], 'source': s['source'], 'translation': translations[s['id']]}
                       for s in b['sentences']],
         'preserved': not bool(b['sentences'])} for b in blocks]}


def units_for(blocks, translations):
    """Rejoin mechanical period fragments for decimals and common abbreviations."""
    units = []
    for b in blocks:
        pending = []
        for i, s in enumerate(b['sentences']):
            pending.append(s)
            nxt = b['sentences'][i+1] if i+1 < len(b['sentences']) else None
            decimal = nxt and re.search(r'\d\.$', s['source'].rstrip()) and re.match(r'\d', nxt['source'])
            abbreviation = nxt and re.search(r'(?i)(?:\b(?:fig|eq|sec|dr|mr|prof|al|vs|i\.e|e\.g)|\b[A-Za-z])\.$', s['source'].rstrip())
            if decimal or abbreviation:
                continue
            units.append({'id': pending[0]['id'], 'ids': [p['id'] for p in pending],
                          'src': ''.join(p['source'] for p in pending),
                          'mt': ''.join(translations[p['id']] for p in pending)})
            pending = []
    # A page break may split a sentence around a floating figure. Rejoin only
    # a prose tail and a lowercase continuation on the immediately next page,
    # with matching column/font geometry. Figure captions remain separate.
    owners = {s['id']:b for b in blocks for s in b['sentences']}
    complete, pending = [], None
    for unit in units:
        block = owners[unit['ids'][0]]
        caption = bool(re.match(r'(?i)^\s*(?:figure|fig\.|table|algorithm)\s+\d', block['source']))
        if pending is not None and not caption:
            previous = owners[pending['ids'][-1]]
            before_box, after_box = previous.get('bbox'), block.get('bbox')
            matching_layout = (before_box and after_box and
                abs(before_box[0]-after_box[0])<20 and
                abs((before_box[2]-before_box[0])-(after_box[2]-after_box[0]))<50 and
                abs(previous.get('font_size',0)-block.get('font_size',0))<.75)
            if (block.get('kind')=='prose' and block['page']==previous['page']+1 and matching_layout
                    and unit['ids'][0]==block['sentences'][0]['id'] and re.match(r'^\s*[a-z]',unit['src'])):
                pending['ids'].extend(unit['ids'])
                pending['src'] = pending['src'].rstrip()+' '+unit['src'].lstrip()
                pending['mt'] += unit['mt']
                pending = None
                continue
            pending = None
        complete.append(unit)
        if (not caption and block.get('kind')=='prose' and
                unit['ids'][-1]==block['sentences'][-1]['id'] and
                not re.search(r'[.?!:;]\s*$',unit['src'])):
            pending = unit
    return complete


def translate_batch(agent, sentences, context, terms, max_revisions=0,
                    emit=lambda *a: None, cancelled=lambda: False):
    """Save first translation before doing any document-level acceptance."""
    from .agents import InvalidApiOutput
    if cancelled():
        raise InterruptedError('已暂停')
    emit('translate', {'revisions': 0})
    try:
        out = agent.call('translate', {'sentences': sentences, 'context': context,
                                     'terms': terms, 'target_language': 'zh-CN'}, translation_schema())
        current = validate_translations(out, sentences)
        history = [{'role': 'translate', 'result': out}]
    except InvalidApiOutput:
        boundaries = [i for i in range(1, len(sentences)) if not (
            adjacent_ids(sentences[i-1]['id'], sentences[i]['id']) and
            re.search(r'\d\.$', sentences[i-1]['source'].rstrip()) and re.match(r'\d', sentences[i]['source']))]
        if not boundaries:
            raise
        middle = min(boundaries, key=lambda i: abs(i-len(sentences)/2))
        parts = [translate_batch(agent, part, context, terms, emit=emit, cancelled=cancelled)
                 for part in (sentences[:middle], sentences[middle:])]
        current = {k: v for part in parts for k, v in part['translations'].items()}
        history = [h for part in parts for h in part['history']]
    return {'translations': current, 'status': 'needs_review', 'history': history,
            'issues': [], 'revisions': 0, 'reason': '初译已保存，等待全文质量评估'}


class CometBackend:
    """Reuse a memory-bounded worker and cache unchanged source/translation pairs."""
    def __init__(self, root, directory, cancelled=lambda: False):
        self.root, self.directory = Path(root), Path(directory)
        self.cancelled = cancelled
        portable = self.root/'tools'/'comet'/'python.exe'
        self.python = Path(os.environ.get('READMASTER_COMET_PYTHON', str(portable if portable.is_file()
                           else self.root / '.venv-comet' / 'Scripts' / 'python.exe')))
        self.worker = self.root/'tools'/'comet'/'app'/'comet_worker.py' if portable.is_file() else self.root/'scripts'/'comet_worker.py'
        self.models = {'kiwi': os.environ.get('READMASTER_KIWI_MODEL', 'Unbabel/wmt22-cometkiwi-da'),
                       'xcomet': os.environ.get('READMASTER_XCOMET_MODEL', 'Unbabel/XCOMET-XL')}
        defaults = {'kiwi': ('Unbabel/wmt22-cometkiwi-da', '1ad785194e391eebc6c53e2d0776cada8f83179a'),
                    'xcomet': ('Unbabel/XCOMET-XL', '6a123c5e8e6dccab25e5fcffa3c8b417abadb462')}
        self.revisions = {}
        for kind, model in self.models.items():
            revision = os.environ.get('READMASTER_'+kind.upper()+'_REVISION')
            if Path(model).is_file():
                stat = Path(model).stat()
                revision = f'local:{stat.st_size}:{stat.st_mtime_ns}'
            elif not revision and model == defaults[kind][0]:
                revision = defaults[kind][1]
            elif not revision:
                raise ValueError('自定义 COMET 模型需指定 READMASTER_'+kind.upper()+'_REVISION，避免混用权重缓存')
            self.revisions[kind] = revision
        self.device = os.environ.get('READMASTER_COMET_DEVICE', 'cpu')

    def predict(self, kind, units):
        if not units:
            return []
        if not self.python.is_file():
            raise RuntimeError('COMET 隔离环境未安装，请运行 scripts/setup_comet.py；初译已保存')
        results, missing, indexes = [None]*len(units), [], []
        self.directory.mkdir(parents=True, exist_ok=True)
        for i, u in enumerate(units):
            signature = {'version': METRIC_INPUT_VERSION, 'model': self.models[kind], 'revision': self.revisions[kind], 'device': self.device,
                         'src': u['src'], 'mt': u['mt'], 'runtime': 'unbabel-comet-2.2.7-fp32'}
            if kind == 'xcomet':
                signature['runtime'] += '-complete-spans-v1'
            digest = hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            path = self.directory / (digest+'.json')
            if path.exists():
                value = json.loads(path.read_text(encoding='utf-8'))
                self._validate(value, kind, u)
                results[i] = value
            else:
                missing.append({'src': u['src'], 'mt': u['mt']})
                indexes.append((i, path))
        if missing:
            while not _METRIC_LOCK.acquire(timeout=.5):
                if self.cancelled():
                    raise InterruptedError('已暂停')
            try:
                if self.cancelled():
                    raise InterruptedError('已暂停')
                # Windows scanners can briefly retain a log handle after the
                # worker exits. Cleanup must not invalidate verified scores.
                with tempfile.TemporaryDirectory(dir=self.directory, ignore_cleanup_errors=True) as temp:
                    inp, out = Path(temp)/'input.json', Path(temp)/'output.json'
                    inp.write_text(json.dumps({'model': self.models[kind], 'device': self.device,
                        'revision': self.revisions[kind],
                        'cache_root': str(self.root/'data'/'comet-models'),
                        'data': missing}, ensure_ascii=False), encoding='utf-8')
                    from .comet_service import WORKER
                    reply = WORKER.invoke(self.python, self.worker, self.root, inp, out, self.cancelled)
                    if 'error' in reply:
                        raise RuntimeError(('COMETKiwi' if kind=='kiwi' else 'XCOMET')+'：'+reply['error'])
                    values = reply['results']
                    if len(values) != len(indexes):
                        raise ValueError('COMET 分数数量与输入片段不一致')
                    for (i, path), value in zip(indexes, values):
                        self._validate(value, kind, units[i])
                        tmp = path.with_suffix('.tmp')
                        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
                        tmp.replace(path)
                        results[i] = value
            finally:
                _METRIC_LOCK.release()
        return results

    @staticmethod
    def _validate(value, kind, unit):
        if not isinstance(value, dict):
            raise ValueError('COMET 返回条目必须是对象')
        score = value.get('score')
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise ValueError('COMET 返回无效分数')
        if kind == 'xcomet':
            if not isinstance(value.get('error_spans'), list):
                raise ValueError('XCOMET 缺少错误跨度')
            for span in value['error_spans']:
                if not isinstance(span, dict):
                    raise ValueError('XCOMET 错误条目必须是对象')
                start, end = span.get('start'), span.get('end')
                if (type(start) is not int or type(end) is not int or
                        not 0 <= start <= end <= len(unit['mt']) or
                        span.get('severity') not in ('minor', 'major', 'critical')):
                    raise ValueError('XCOMET 错误位置或严重程度无效')
                if span.get('text') != unit['mt'][start:end]:
                    raise ValueError('XCOMET 错误文本与译文位置不一致')
                confidence = span.get('confidence')
                if confidence is not None and (isinstance(confidence,bool) or not isinstance(confidence,(int,float))
                                                or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                    raise ValueError('XCOMET 置信度无效')


NUMBER = re.compile(r'(?<!\d)[+-]?\d+(?:,\s*\d{3})*(?:\.\d+)?(?:[eE][+-]?\d+|\s*\^\s*[+-]?\d+)?%?')


def numeric_source(block, start=0, end=None):
    """Recover powers proven by PDF glyph layout without changing reader text/IDs."""
    source = block['source']
    end = len(source) if end is None else end
    text = source[start:end]
    for offset in reversed(block.get('numeric_power_offsets', [])):
        if start < offset < end:
            text = text[:offset-start] + '^' + text[offset-start:]
    return text


def paper_checks(blocks, translations):
    """Mechanical checks complement contextual semantic checks, never score proxies."""
    issues = []
    def counts(text):
        text = text.replace('％', '%').replace('−', '-')
        # Hyphens in names (PaLM-8B), top-5 and numeric ranges are not minus signs.
        text = re.sub(r'(?<=[A-Za-z0-9])-\s*(?=\d)', ' ', text)
        return Counter(re.sub(r'[,\s]', '', m.group()) for m in NUMBER.finditer(text))
    for b in blocks:
        if not b['sentences']:
            continue
        mt = ''.join(translations[s['id']] for s in b['sentences'])
        source = numeric_source(b)
        if counts(source) != counts(mt):
            changed_ids = set()
            for unit in units_for([b], translations):
                members = [s for s in b['sentences'] if s['id'] in unit['ids']]
                src = numeric_source(b, members[0]['start'], members[-1]['end'])
                if counts(src) != counts(unit['mt']):
                    changed_ids.update(unit['ids'])
            if not changed_ids:
                changed_ids = {sentence['id'] for sentence in b['sentences']}
            for sentence in b['sentences']:
                if sentence['id'] in changed_ids:
                    issues.append({'id': sentence['id'], 'severity': 'blocking',
                        'evidence': sentence['source'].strip(),
                        'reason': '数字/百分比的值或出现次数与原文不一致',
                        'suggestion': '核对本句及关联片段的数字、单位、引用和小数衔接，保留原值和对应关系。'})
        for s in b['sentences']:
            # Explicit placeholders and bracketed numeric references are immutable.
            protected = re.findall(r'<(?:/?(?:v|formula|math|sup|sub|b|i)\d*[^>]*)>|\[\d+(?:\s*[,–-]\s*\d+)*\]', s['source'])
            for token in protected:
                if token not in translations[s['id']]:
                    issues.append({'id': s['id'], 'severity': 'blocking', 'evidence': token,
                        'reason': '引用编号或受保护标记未保留', 'suggestion': '原样恢复 '+token})
            for formula in re.findall(r'\b[A-Za-z][A-Za-z0-9_]*(?:\s*[=<>≤≥]\s*[A-Za-z0-9_+*/().-]+)', s['source']):
                formula = formula.rstrip('.,')
                if re.sub(r'\s+', '', formula) not in re.sub(r'\s+', '', translations[s['id']]):
                    issues.append({'id': s['id'], 'severity': 'blocking', 'evidence': formula,
                        'reason': '行内公式或比较符号不一致', 'suggestion': '核实并保留公式 '+formula})
            unit_names = {'ms': ('ms', '毫秒'), 'Hz': ('Hz', '赫兹'), 'GHz': ('GHz', '吉赫'),
                          'MHz': ('MHz', '兆赫'), 'kg': ('kg', '千克', '公斤')}
            for match in re.finditer(r'\b\d+(?:\.\d+)?\s*(ms|Hz|GHz|MHz|kg)\b', s['source']):
                unit = match.group(1)
                if not any(re.search(r'(?<![A-Za-z])'+re.escape(v)+r'(?![A-Za-z])', translations[s['id']])
                           for v in unit_names[unit]):
                    issues.append({'id': s['id'], 'severity': 'blocking', 'evidence': match.group(),
                        'reason': '数值单位丢失或发生变化', 'suggestion': '保留原单位含义：'+unit})
    return issues


def candidate_revisions(agent, blocks, current, terms, backend, scores, attempt, unit_ids=None):
    """Try faithful alternatives only after ordinary repair has stopped improving.

    Candidate IDs are temporary adapter IDs; only frozen reader IDs are saved.
    Selection uses actual Kiwi predictions and cannot override paper invariants.
    The chosen wording still goes through normal full review and acceptance.
    """
    blocked_ids = score_issue_ids(scores)
    low = sorted((u for u in units_for(blocks,current) if u['id'] in blocked_ids
                  and (unit_ids is None or u['id'] in unit_ids)),
                 key=lambda u:scores[u['id']])[:8]
    if not low:
        return {}, []
    requested = {sid for u in low for sid in u['ids']}
    original = [s for b in blocks for s in b['sentences'] if s['id'] in requested]
    variants, mapping = [], []
    styles = ('贴近原文结构', '按论文语境翻译为自然中文，普通名词动词不机械沿用旧词条', '简洁清楚的表达')
    for variant, style in enumerate(styles):
        for sentence in original:
            ident = f'candidate{variant}:{sentence["id"]}'
            variants.append({**sentence,'id':ident})
            mapping.append({'candidate_id':ident,'original_id':sentence['id'],'style':style})
    proposed = validate_translations(agent.call('revise', {
        'sentences':variants, 'document':snapshot(blocks,current), 'terms':terms,
        'context':'完整上下文见 reader_data.document；候选编号与原编号的对应见 candidate_sources。',
        'translations':[{'id':m['candidate_id'],'text':current[m['original_id']]} for m in mapping],
        'candidate_sources':mapping, 'candidate_attempt':attempt,
        'instruction':'为同一原句给出三种不同且忠实的候选表达，按照各自 style 改写。不要重复原译文或其他候选，不添加事实、解释或英文回译，不改变否定、条件、数值、公式、专名和缩写。每个候选只包含对应原编号的源内容，不跨编号搬运；临时编号仅用于候选选择。'},
        translation_schema()), variants)
    trials = []
    for unit in low:
        for variant in range(3):
            texts = {sid:proposed[f'candidate{variant}:{sid}'] for sid in unit['ids']}
            trials.append({**unit,'mt':''.join(texts[sid] for sid in unit['ids']),
                           'variant':variant,'translations':texts})
    predictions = backend.predict('kiwi',trials)
    if len(predictions)!=len(trials):
        raise ValueError('候选 COMETKiwi 分数数量不匹配')
    for trial,prediction in zip(trials,predictions):
        CometBackend._validate(prediction,'kiwi',trial)
        trial['score'] = prediction['score']
    chosen = {}
    for unit in low:
        for trial in sorted((t for t in trials if t['id']==unit['id']),key=lambda t:t['score'],reverse=True):
            if trial['score']<=scores[unit['id']]+.001:
                continue
            relevant = set(unit['ids'])
            if any(i['id'] in relevant for i in paper_checks(blocks,{**current,**chosen,**trial['translations']})):
                continue
            chosen.update(trial['translations'])
            break
    return chosen,trials


def evaluate_document(agent, blocks, terms, backend, cancelled=lambda: False,
                      checkpoint=lambda *a: None, notify=lambda text: None, previous=None, diversify=False):
    """Require >=97% of QE units > .7, all >=.5, and no blocking paper issues."""
    sentences = [s for b in blocks for s in b['sentences']]
    current = {s['id']: b['translations'][s['id']] for b in blocks for s in b['sentences']}
    if not sentences:
        raise ValueError('没有可评估的正文')
    if len({s['id'] for s in sentences}) != len(sentences):
        raise ValueError('全文句块编号重复')
    validate_translations({'translations': [{'id': s['id'], 'text': current[s['id']]} for s in sentences]}, sentences)
    history = list((previous or {}).get('history', []))
    seen = {h['fingerprint'] for h in history if h.get('fingerprint')}
    best_deficit, best_count = None, None
    last_issues, stalled, revisions = [], 0, int((previous or {}).get('revisions', 0))
    # A resumed stalled/cyclic version must get another chance with new feedback/model.
    initial = True
    alternative_runs = 0
    def diversify_stalled(result, unit_ids=None):
        nonlocal current, revisions, stalled, alternative_runs
        if not diversify or alternative_runs>=3 or not result.get('scores') or result.get('score_gate_passed'):
            return False
        if cancelled():
            raise InterruptedError('已暂停候选修订')
        notify('翻译 agent 生成忠实候选，并用 COMETKiwi 重新筛选停滞片段')
        alternative_runs += 1
        patch,trials = candidate_revisions(agent,blocks,current,terms,backend,result['scores'],alternative_runs,unit_ids)
        history.append({'role':'candidate_revisions','attempt':alternative_runs,'candidates':trials,
                        'selected_ids':list(patch)})
        if not patch or all(current[k]==v for k,v in patch.items()):
            return False
        current = {**current,**patch}
        revisions += 1
        stalled = 0
        checkpoint(current,{**result,'status':'needs_review','translations':current,'history':history,
            'revisions':revisions,'reason':'候选修订已保存，等待全文重新评分和审查',
            'scores':None,**score_summary({})})
        return True
    while True:
        if cancelled():
            raise InterruptedError('已暂停全文评估')
        document = snapshot(blocks, current)
        fingerprint = hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if not initial and fingerprint in seen:
            matching = next((h for h in reversed(history) if h.get('fingerprint') == fingerprint), {})
            result = {'status': 'needs_review', 'reason': '译文循环或没有实质变化',
                      'translations': current, 'history': history, 'issues': matching.get('issues', last_issues),
                      'revisions': revisions, 'scores': matching.get('scores'), 'version': VERSION,
                      **score_summary(matching.get('scores') or {})}
            if diversify_stalled(result):
                continue
            checkpoint(current, result)
            return result
        initial = False
        seen.add(fingerprint)
        units = units_for(blocks, current)
        notify('COMETKiwi 全文逐片段评分')
        predictions = backend.predict('kiwi', units)
        if len(predictions) != len(units):
            raise ValueError('COMETKiwi 分数数量不匹配')
        scores = {}
        for u, p in zip(units, predictions):
            CometBackend._validate(p, 'kiwi', u)
            scores[u['id']] = p['score']
        summary = score_summary(scores)
        rules = paper_checks(blocks, current)
        low = [u for u in units if scores[u['id']] <= THRESHOLD]
        affected = {i['id'] for i in rules}
        affected.update(i['id'] for i in last_issues if i['severity'] == 'blocking')
        flagged = [u for u in units if u in low or set(u['ids']) & affected]
        notify(f'XCOMET 定位问题：{len(flagged)} 个片段；未超过 {THRESHOLD:.1f}：{len(low)} 个')
        diagnosis = backend.predict('xcomet', flagged)
        if len(diagnosis) != len(flagged):
            raise ValueError('XCOMET 诊断数量不匹配')
        reports = []
        for u, p in zip(flagged, diagnosis):
            CometBackend._validate(p, 'xcomet', u)
            reports.append({**u, 'kiwi_score': scores[u['id']], 'xcomet': p})
        notify('评估 agent 对照全文进行论文专用检查并撰写修改意见')
        payload = {'sentences': sentences, 'translations': [{'id': s['id'], 'text': current[s['id']]} for s in sentences],
                   'document': document, 'terms': terms, 'xcomet_findings': reports,
                   'paper_findings': rules, 'checklist': PAPER_CHECKLIST, 'quality_threshold': THRESHOLD,
                   'instruction': f'核实 XCOMET 线索，结合完整文章撰写具体修改意见，同时检查全文论文专用项目。每个 kiwi_score <= {THRESHOLD} 的单元必须给出至少一条意见与具体的忠实替代表达，覆盖它的原句编号。低分并不证明误译：未发现语义错误时列 warning 并如实说明，只建议保持事实、否定、数字和术语含义的更自然或更精简表达；不要伪造错误，不要仅写“再核对”。数值门槛由程序独立执行。优先指出无原句依据的括号释义、片段拼接、词义或搭配问题。每项证据逐字引用其 id 原文。document 中 preserved=true 的区域按阅读器规则保留原版（如图表、公式、参考文献），不得因它仍是英语而判定漏译或请求新增编号。'}
        if cancelled():
            raise InterruptedError('已暂停全文评估')
        reviewed = validate_review(agent.call('evaluate', payload, review_schema()), sentences)
        issues = rules + reviewed['issues']
        # Low-but-allowed units remain diagnosed; only a failed document gate
        # creates score-only blockers. Real semantic/paper blockers always apply.
        blocked_ids = score_issue_ids(scores)
        score_blocked = [u for u in low if u['id'] in blocked_ids]
        for u in score_blocked:
            # Even an LLM pass cannot override a real score below threshold.
            if not any(i['severity'] == 'blocking' and i['id'] in u['ids'] for i in issues):
                s = next(s for s in sentences if s['id'] == u['id'])
                suggestions = [i['suggestion'] for i in issues if i['id'] in u['ids']]
                issues.append({'id': s['id'], 'severity': 'blocking', 'evidence': s['source'].strip(),
                    'reason': (f"COMETKiwi {scores[u['id']]:.4f} 低于最低允许分数 {MINIMUM_ALLOWED_SCORE:.1f}"
                               if scores[u['id']] < MINIMUM_ALLOWED_SCORE else
                               f"COMETKiwi {scores[u['id']]:.4f} 未超过 {THRESHOLD:.1f}；全文超过该分数的比例 {summary['above_threshold_percentage']:.2f}% 未达到 {REQUIRED_PERCENTAGE}%"),
                    'suggestion': '；'.join(suggestions) if suggestions else '结合全文及 XCOMET 线索重新核对完整评估单元，修正忠实性和表达，保留事实、术语与编号。'})
        blockers = [i for i in issues if i['severity'] == 'blocking']
        acceptance = None
        if not blockers:
            notify('独立验收 agent 对照全文核查最终译文')
            if cancelled():
                raise InterruptedError('已暂停全文验收')
            acceptance = validate_review(agent.call('accept', {
                'sentences': sentences, 'translations': payload['translations'],
                'document': document, 'terms': terms,
                'checklist': PAPER_CHECKLIST + ' preserved=true 的图表、独立公式和参考文献保留原版，不判为漏译。'},
                review_schema()), sentences)
            issues += acceptance['issues']
            blockers = [i for i in issues if i['severity'] == 'blocking']
        record = {'fingerprint': fingerprint, 'revision': revisions, 'scores': scores,
                  'units': [{'id': u['id'], 'ids': u['ids']} for u in units],
                  'translations': copy.deepcopy(current), 'issues': issues, 'xcomet': reports, 'acceptance': acceptance}
        history.append(record)
        result = {'status': 'needs_review' if blockers else 'accepted',
                  'reason': f'至少 {REQUIRED_PERCENTAGE}% 片段大于 {THRESHOLD:.1f}、全部不低于 {MINIMUM_ALLOWED_SCORE:.1f}，论文检查与独立验收通过' if not blockers else '全文质量未达标，继续定向修订',
                  'translations': current, 'history': history, 'issues': issues, 'revisions': revisions,
                  'scores': scores, 'version': VERSION, **summary}
        checkpoint(current, result)
        if not blockers:
            return result
        deficit = sum(max(0, THRESHOLD-v) for v in scores.values())
        improved = best_deficit is None or deficit < best_deficit-.001 or len(blockers) < best_count
        stalled = 0 if improved else stalled+1
        best_deficit = deficit if best_deficit is None else min(best_deficit, deficit)
        best_count = len(blockers) if best_count is None else min(best_count, len(blockers))
        previous_scored = next((h for h in reversed(history[:-1]) if h.get('scores') and h.get('translations')),None)
        if previous_scored and low:
            stationary = {u['id'] for u in low if previous_scored['scores'].get(u['id'],1)<=THRESHOLD
                          and all(previous_scored['translations'].get(sid)==current[sid] for sid in u['ids'])}
            if stationary and diversify_stalled(result,stationary):
                continue
        # Meaningful problems must improve; do not burn calls forever on score noise.
        if stalled >= 3:
            if diversify_stalled(result):
                continue
            result.update(reason='连续三轮分数未改善，保留未达标状态，可恢复或人工复核')
            checkpoint(current, result)
            return result
        last_issues = issues
        affected = {i['id'] for i in blockers}
        for u in units:
            if affected & set(u['ids']):
                affected.update(u['ids'])
        selected = [s for s in sentences if s['id'] in affected]
        # Use the whole document as context but ask for patches only.
        notify(f'翻译 agent 定向修订 {len(selected)} 个句块')
        if cancelled():
            raise InterruptedError('已暂停全文修订')
        revise_payload = {**payload, 'sentences': selected, 'issues': blockers,
                          'context': '完整文章的原文、译文与固定编号见 reader_data.document（直接调用时为 document）。',
                          'instruction': '核实原文，只修改请求编号；保留其他译文、数值、公式和术语。'}
        patched = validate_translations(agent.call('revise', revise_payload, translation_schema()), selected)
        current = {**current, **patched}
        validate_translations({'translations': [{'id': s['id'], 'text': current[s['id']]} for s in sentences]}, sentences)
        revisions += 1
        # Durable pending snapshot before potentially failing/cancelling next inference.
        pending = {**result, 'status': 'needs_review', 'translations': current, 'revisions': revisions,
                   'reason': '修订已保存，等待重新评分', 'scores': None, **score_summary({})}
        checkpoint(current, pending)
