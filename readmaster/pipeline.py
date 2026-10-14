"""Immutable period splitting and bounded translation/evaluation/acceptance."""
import copy
import hashlib
import json
import re


def split_periods(text, prefix="s"):
    """Literal ASCII period boundaries, including abbreviations/decimals by request.

    End offsets are exclusive Python Unicode code-point offsets. Whitespace is
    retained in slices, so concatenating source reproduces the input exactly.
    """
    result, start = [], 0
    for match in re.finditer(r"\.", text):
        end = match.end()
        result.append({"id": f"{prefix}_{len(result)+1:03d}", "source": text[start:end], "start": start, "end": end})
        start = end
    if start < len(text):
        tail = text[start:]
        if tail.strip():
            result.append({"id": f"{prefix}_{len(result)+1:03d}", "source": tail, "start": start, "end": len(text)})
        elif result:
            result[-1]["source"] += tail
            result[-1]["end"] = len(text)
    return result


def translation_schema():
    return {"type": "object", "properties": {"translations": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
        "required": ["id", "text"], "additionalProperties": False}}},
        "required": ["translations"], "additionalProperties": False}


def review_schema():
    props = {"id": {"type": "string"}, "severity": {"type": "string", "enum": ["blocking", "warning"]},
             "evidence": {"type": "string"}, "reason": {"type": "string"}, "suggestion": {"type": "string"}}
    return {"type": "object", "properties": {"passed": {"type": "boolean"}, "issues": {
        "type": "array", "items": {"type": "object", "properties": props,
        "required": list(props), "additionalProperties": False}}},
        "required": ["passed", "issues"], "additionalProperties": False}


def glossary_schema():
    props = {"source": {"type": "string"}, "target": {"type": "string"}, "definition": {"type": "string"}}
    return {"type": "object", "properties": {"terms": {"type": "array", "items": {
        "type": "object", "properties": props, "required": list(props), "additionalProperties": False}}},
        "required": ["terms"], "additionalProperties": False}


def adjacent_ids(left, right):
    a, _, ai = left.rpartition('_')
    b, _, bi = right.rpartition('_')
    return a == b and ai.isdigit() and bi.isdigit() and int(bi) == int(ai) + 1


def validate_translations(output, sentences):
    entries = output.get("translations")
    if not isinstance(entries, list):
        raise ValueError("翻译结果缺少 translations")
    expected = {s["id"] for s in sentences}
    found = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("译文条目必须包含 id 和 text")
        ident, value = entry.get("id"), entry.get("text")
        if ident not in expected or ident in found:
            raise ValueError("句块编号缺失、重复或越界")
        if not isinstance(value, str) or not value.strip() or len(value) > 20000:
            raise ValueError("译文为空或过长")
        found[ident] = value.strip()
    if set(found) != expected:
        raise ValueError("译文没有覆盖全部请求句块")
    for left, right in zip(sentences, sentences[1:]):
        number = re.search(r'(\d+)\.$', left['source'].rstrip())
        tail = re.match(r'(\d+)', right['source'])
        if number and tail and adjacent_ids(left['id'], right['id']):
            a, b = found[left['id']], found[right['id']]
            if not a.endswith(number.group(1) + '.') or not b.startswith(tail.group(1)):
                raise ValueError(f"小数衔接错误：{left['id']} 译文须止于 {number.group(1)}.，{right['id']} 须以 {tail.group(1)} 开头；不得提前重复数字或插入中文句号/词语")
    return found


def validate_review(output, sentences):
    if not isinstance(output.get("passed"), bool) or not isinstance(output.get("issues"), list):
        raise ValueError("评估结果格式错误")
    sources = {s["id"]: s["source"] for s in sentences}
    for issue in output["issues"]:
        if not isinstance(issue, dict):
            raise ValueError("评估问题条目必须是对象")
        if issue.get("id") not in sources or issue.get("severity") not in ("blocking", "warning"):
            raise ValueError("评估问题编号或严重程度错误")
        for key in ("evidence", "reason", "suggestion"):
            if not isinstance(issue.get(key), str) or not issue[key].strip():
                raise ValueError("评估必须给出证据和具体建议")
        # Exact quote, normalized only for whitespace. No invented source evidence.
        quote = " ".join(issue["evidence"].split())
        if quote not in " ".join(sources[issue["id"]].split()):
            raise ValueError(f"评估证据不属于指定原句 {issue['id']}：请在这个 id 的 source 中选择逐字子串")
    blockers = [i for i in output["issues"] if i["severity"] == "blocking"]
    if output["passed"] == bool(blockers):
        raise ValueError("评估通过标志与阻塞问题不一致")
    return output


CHECKLIST = "忠实原文；保留否定、条件、数值和语气强度；术语一致；中文基本可读；内容不能跨编号转移。纯风格偏好只列 warning。"


def run_batch(agent, sentences, context, terms, max_revisions=2, emit=lambda *a: None, cancelled=lambda: False):
    from .agents import InvalidApiOutput
    try:
        return _run_batch(agent, sentences, context, terms, max_revisions, emit, cancelled)
    except InvalidApiOutput:
        if len(sentences) < 2:
            raise
        boundaries = [i for i in range(1, len(sentences))
                      if not (adjacent_ids(sentences[i-1]['id'], sentences[i]['id'])
                              and re.search(r'\d\.$', sentences[i-1]['source'].rstrip())
                              and re.match(r'\d', sentences[i]['source']))]
        if not boundaries:
            raise
        midpoint = min(boundaries, key=lambda i: abs(i-len(sentences)/2))
        results = [run_batch(agent, part, context, terms, max_revisions, emit, cancelled)
                   for part in (sentences[:midpoint], sentences[midpoint:])]
        return {'status': 'accepted' if all(r['status'] == 'accepted' for r in results) else 'needs_review',
                'translations': {k: v for r in results for k, v in r['translations'].items()},
                'issues': [i for r in results for i in r['issues']],
                'history': [h for r in results for h in r['history']],
                'revisions': max(r['revisions'] for r in results),
                'reason': '输出超长或校验失败后分组处理，每组独立评估与验收'}


def _run_batch(agent, sentences, context, terms, max_revisions=2, emit=lambda *a: None, cancelled=lambda: False):
    """Agents are role-specific stateless calls. Only this controller commits data.

    A shared global revision budget bounds both evaluation and acceptance loops.
    Original sentences never come from an agent output.
    """
    if not 0 <= max_revisions <= 5:
        raise ValueError("修订上限须在 0 到 5 之间")
    base = {"sentences": [{"id": s["id"], "source": s["source"]} for s in sentences],
            "context": context, "terms": terms, "target_language": "zh-CN"}
    history, translations, revisions, seen = [], {}, 0, set()

    def call(role, payload, schema):
        if cancelled():
            raise InterruptedError("处理已暂停")
        emit(role, {"revisions": revisions})
        result = agent.call(role, payload, schema)
        history.append({"role": role, "revision": revisions, "result": copy.deepcopy(result)})
        return result

    translations = validate_translations(call("translate", base, translation_schema()), sentences)
    last_review = None
    while True:
        current = [{"id": s["id"], "text": translations[s["id"]]} for s in sentences]
        fingerprint = hashlib.sha256(json.dumps(current, ensure_ascii=False).encode()).hexdigest()
        if fingerprint in seen:
            return {"status": "needs_review", "translations": translations, "history": history,
                    "issues": (last_review or {}).get("issues", []), "revisions": revisions, "reason": "译文循环或没有实质变化"}
        seen.add(fingerprint)
        # Every new version is evaluated as a full contextual batch.
        reviewed = validate_review(call("evaluate", {**base, "translations": current, "checklist": CHECKLIST}, review_schema()), sentences)
        last_review = reviewed
        if reviewed["passed"]:
            # Acceptance receives no prior pass flag, score, history, or reviewer verdict.
            reviewed = validate_review(call("accept", {**base, "translations": current, "checklist": CHECKLIST}, review_schema()), sentences)
            last_review = reviewed
            if reviewed["passed"]:
                return {"status": "accepted", "translations": translations, "history": history,
                        "issues": reviewed["issues"], "revisions": revisions, "reason": "独立验收通过（机器检查）"}
        if revisions >= max_revisions:
            return {"status": "needs_review", "translations": translations, "history": history,
                    "issues": reviewed["issues"], "revisions": revisions, "reason": "达到修订上限"}
        issues = [i for i in reviewed["issues"] if i["severity"] == "blocking"]
        affected = {i["id"] for i in issues}
        # A decimal is one meaning-bearing unit despite immutable period IDs.
        # Expand feedback to the connected fragments so a repair cannot duplicate
        # a suffix already moved into the adjacent translation.
        changed = True
        while changed:
            changed = False
            for left, right in zip(sentences, sentences[1:]):
                if (adjacent_ids(left['id'], right['id'])
                        and re.search(r'\d\.$', left['source'].rstrip()) and re.match(r'\d', right['source'])):
                    pair = {left['id'], right['id']}
                    if pair & affected and not pair <= affected:
                        affected |= pair
                        changed = True
        selected = [s for s in sentences if s["id"] in affected]
        revisions += 1
        patched = validate_translations(call("revise", {**base, "sentences": selected,
            "translations": current, "issues": issues, "instruction": "核对意见是否符合原文，只修订请求编号，不改写英文。"}, translation_schema()), selected)
        translations.update(patched)
