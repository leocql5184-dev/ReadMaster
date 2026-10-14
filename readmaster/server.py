import argparse
import copy
import hashlib
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

from .agents import CodexAgent, ApiAgent, validate_schema, InvalidApiOutput, OutputLimitError, ResultValidationError, ROLE_PROMPTS
from .pdf import extract_pdf, render_page, search_pdf, refresh_layout, refresh_numeric_layout, LAYOUT_VERSION
from .quality import translate_batch, evaluate_document, CometBackend, VERSION as QUALITY_VERSION, THRESHOLD as QUALITY_THRESHOLD, ACCEPTANCE_POLICY
from .pipeline import run_batch, glossary_schema, translation_schema, validate_translations, validate_review
from . import progress as task_progress
from .checkpoints import paragraph_batches, CHAR_THRESHOLD
from .models import ModelStore, connection, decrypt_key

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
HOME = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
DATA = Path(os.environ.get("READMASTER_DATA", HOME / "data"))
DOCS, STOPS, GUARD = {}, {}, threading.RLock()
ACTIVE_TASKS = set()
WORKERS = threading.Semaphore(2)
KEY = ""
CONFIG = {"provider": "codex", "model": "", "max_revisions": 2, "endpoint": "https://api.openai.com/v1"}
VERSION = "0.5.0"
MODELS = None
LOGIN_PROCESS = None
SHUTTING_DOWN = threading.Event()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def save(doc):
    with GUARD:
        atomic_json(DATA / "documents" / doc["id"] / "document.json", doc)


def delete_document(ident):
    with GUARD:
        if ident not in DOCS:
            raise ValueError("文献不存在")
        if ident in ACTIVE_TASKS or DOCS[ident]['state'] == 'processing':
            raise ValueError("这篇论文仍在处理，请先暂停并等待当前调用结束后删除")
        if len(ident) != 24 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError("文献编号无效")
        documents = (DATA / 'documents').resolve()
        folder = (documents / ident).resolve()
        if folder != documents / ident:
            raise ValueError("文献目录无效")
        if folder.exists():
            shutil.rmtree(folder)
        del DOCS[ident]
        STOPS.pop(ident, None)


def codex_binary():
    bundled = HOME / "tools" / "codex" / "bin" / "codex.exe"
    return str(bundled) if bundled.exists() else shutil.which("codex")


def codex_status():
    binary = codex_binary()
    if not binary:
        return {"available": False, "logged_in": False}
    try:
        result = subprocess.run([binary, "login", "status"], capture_output=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"available": True, "logged_in": result.returncode == 0}
    except (OSError, subprocess.TimeoutExpired):
        return {"available": True, "logged_in": False}


def project_schema(value, schema):
    """Drop only undeclared decoration; required data and types still get checked."""
    if isinstance(value, dict) and schema.get('type') == 'object':
        props = schema.get('properties', {})
        return {k: project_schema(v, props[k]) for k, v in value.items() if k in props}
    if isinstance(value, list) and schema.get('type') == 'array':
        return [project_schema(v, schema['items']) for v in value]
    return value


class CachedAgent:
    def __init__(self, inner, directory, config):
        self.inner, self.directory, self.config = inner, Path(directory), config
        self.directory.mkdir(parents=True, exist_ok=True)
        self.live_calls = 0
        self.counter_lock = threading.Lock()

    def call(self, role, payload, schema):
        signature = {"role": role, "payload": payload, "schema": schema, "prompt_version": 5,
                     "provider": self.config["provider"], "model": self.config["model"], "endpoint": self.config.get("endpoint"),
                     "backend": self.config.get('backend', 'readmaster')}
        if role in ("evaluate", "accept"):
            signature["review_prompt"] = ROLE_PROMPTS[role]
        digest = hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        path = self.directory / f"{digest}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        # Malformed model output is a separate bounded repair, not a translation
        # revision. Never cache it or weaken the source-evidence requirement.
        request = {**payload, 'expected_ids': [s['id'] for s in payload['sentences']]} if role in ('translate', 'revise') else payload
        partial = {}
        targets = {s['id'] for s in payload.get('sentences', [])}
        for attempt in range(3):
            with self.counter_lock:
                self.live_calls += 1
            try:
                value = self.inner.call(role, request, schema)
            except OutputLimitError:
                raise
            except InvalidApiOutput as exc:
                if attempt == 2:
                    raise
                request = {**payload, 'validation_feedback': str(exc),
                           'format_instruction': '重新生成完整 JSON 对象，严格按指定结构返回，不使用 Markdown。'}
                continue
            value = project_schema(value, schema)
            if role in ('translate', 'revise') and isinstance(value, dict) and isinstance(value.get('translations'), list):
                entries = value['translations']
                requested = {s['id'] for s in request.get('sentences', [])}
                counts = {}
                for entry in entries:
                    if isinstance(entry, dict) and isinstance(entry.get('id'), str):
                        counts[entry['id']] = counts.get(entry['id'], 0) + 1
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    ident, text = entry.get('id'), entry.get('text')
                    if isinstance(ident, str) and ident in targets and ident in requested:
                        if counts[ident] == 1 and isinstance(text, str) and text.strip() and len(text) <= 20000:
                            partial[ident] = text
                        else:
                            partial.pop(ident, None)
                value = {'translations': [{'id': s['id'], 'text': partial[s['id']]}
                                           for s in payload['sentences'] if s['id'] in partial]}
            try:
                validate_schema(value, schema)
                if not isinstance(value, dict):
                    raise ValueError("结果必须是 JSON 对象")
                if role in ("translate", "revise"):
                    validate_translations(value, payload["sentences"])
                elif role in ("evaluate", "accept"):
                    validate_review(value, payload["sentences"])
                elif not isinstance(value.get("terms"), list):
                    raise ValueError("术语结果格式错误")
                break
            except ValueError as exc:
                if attempt == 2:
                    raise ResultValidationError("模型结果校验仍未通过：" + str(exc)) from exc
                request = {**payload, "validation_feedback": str(exc),
                           "invalid_response": value,
                           "format_instruction": "重新核对并返回完整结果。evidence 只能逐字引用问题 id 自己的 source，不能引用 context 或相邻 id，也不能省略或改写。"}
                if role in ('translate', 'revise'):
                    missing = [s for s in payload['sentences'] if s['id'] not in partial]
                    if missing:
                        request.update(sentences=missing, expected_ids=[s['id'] for s in missing],
                                       format_instruction='仅返回 expected_ids 中的编号，每个恰好一次，不返回已完成或上下文中的编号。',
                                       completed_translations=value['translations'], all_source_sentences=payload['sentences'])
                    else:
                        # All IDs exist, but a semantic/decimal repair is required.
                        partial.clear()
        atomic_json(path, value)
        return value


def add_log(doc, message, role=""):
    with GUARD:
        doc["events"].append({"time": time.strftime("%H:%M:%S"), "message": message, "role": role})
        doc["events"] = doc["events"][-150:]
        save(doc)


def outline_blocks(doc):
    return [{'id': f'toc_{i+1:04d}', 'source': item['title'], 'page': item['page'],
             'sentences': [{'id': f'toc_{i+1:04d}', 'source': item['title']}], 'outline_index': i}
            for i, item in enumerate(doc.get('outline', [])) if not item.get('translation')]


def translate_outline(agent, doc, stop, outline_only=False):
    for group in paragraph_batches(outline_blocks(doc)):
        if stop.is_set():
            raise InterruptedError('已暂停')
        if outline_only:
            task_progress.activate(doc['task'], group)
        doc['active_role'], doc['active_page'] = 'translate', group[0]['page']
        doc['task']['phase'] = 'translate'
        add_log(doc, '翻译中英双语目录', 'translate')
        calls_before = agent.live_calls
        sentences = [s for b in group for s in b['sentences']]
        value = agent.call('translate', {'sentences': sentences,
            'context': 'Paper outline:\n' + '\n'.join(item['title'] for item in doc['outline']),
            'terms': doc['terms'],
            'instruction': '将论文书签标题翻译成中文。保留标题编号、专有名称和缩写的英文拼写及编号对应关系。术语表只用于理解，不把术语定义展开进标题，不添加章节内容。'}, translation_schema())
        translations = validate_translations(value, sentences)
        with GUARD:
            for block in group:
                item = doc['outline'][block['outline_index']]
                item['translation'] = translations[block['id']]
                item['translation_status'] = 'translated'
            if outline_only:
                task_progress.advance(doc['task'], group, agent.live_calls-calls_before)
            save(doc)


def process_document(ident, start_page=1, end_page=None, outline_only=False, model_config=None, model_key=None):
    doc, stop = DOCS[ident], STOPS[ident]
    config = copy.deepcopy(model_config if model_config is not None else CONFIG)
    key = KEY if model_key is None else model_key
    folder = DATA / "documents" / ident
    acquired = False
    try:
        while not WORKERS.acquire(timeout=.5):
            if stop.is_set():
                raise InterruptedError("已暂停")
        acquired = True
        refresh_numeric_layout(doc, folder / 'original.pdf')
        inner = CodexAgent(folder / "runs", config, codex_binary(), stop.is_set, lambda text: add_log(doc, text, 'reconnect')) if config["provider"] == "codex" else ApiAgent(config, key, stop.is_set, lambda text: add_log(doc, text, 'reconnect'))
        engine = os.environ.get('READMASTER_TRANSLATION_ENGINE', 'babeldoc')
        if engine in ('babeldoc', 'babeldoc-candidate'):
            from .babeldoc_agent import BabelDOCTranslationAgent
            inner = BabelDOCTranslationAgent(inner, HOME)
            config['backend'] = inner.backend
        elif engine != 'readmaster':
            raise ValueError('未知翻译引擎配置')
        agent = CachedAgent(inner, folder / "cache", config)
        if not outline_only:
            doc['task']['estimate_scope'] = 'initial_translation'
        eligible = [b for b in doc["blocks"] if b["sentences"] and start_page <= b["page"] <= (end_page or len(doc["pages"]))]
        # Already translated/previously accepted blocks can be reevaluated without retranslation.
        eligible = [b for b in eligible if any(not b['translations'].get(s['id']) for s in b['sentences'])]
        if not doc["terms"] and eligible and not outline_only:
            doc["task"]["phase"] = "terms"
            doc["active_role"] = "terms"
            add_log(doc, "提取论文技术术语", "terms")
            text = "\n".join(b["source"] for b in doc["blocks"] if b["sentences"])[:16000]
            terms = agent.call("terms", {"source": text}, glossary_schema()).get("terms", [])
            verified, seen = [], set()
            for item in terms:
                if not isinstance(item, dict):
                    continue
                source = item.get("source", "")
                if isinstance(source, str) and source.strip() and source in text and source.casefold() not in seen:
                    if not all(isinstance(item.get(k), str) for k in ("target", "definition")):
                        continue
                    seen.add(source.casefold())
                    verified.append({**item, "id": f"term_{len(verified)+1:03d}"})
            doc["terms"] = verified
            save(doc)
        translate_outline(agent, doc, stop, outline_only)
        if outline_only:
            return
        groups = list(paragraph_batches(eligible))
        doc['cache_policy'] = {'source_chars': CHAR_THRESHOLD, 'boundary': 'paragraph', 'unit': 'unicode_characters'}
        from .translation_schedule import run_waves
        from .quality import units_for
        backend = CometBackend(HOME, folder / 'quality-cache', stop.is_set)
        wave_calls = 0

        def begin_wave(wave):
            nonlocal wave_calls
            wave_calls = agent.live_calls
            flat = [b for group in wave for b in group]
            task_progress.activate(doc['task'], flat)
            doc['active_role'] = doc['task']['phase'] = 'translate'
            doc['active_page'] = flat[0]['page']
            for block in flat:
                block['status'] = 'processing'
            save(doc)
            add_log(doc, f'并发翻译 {len(wave)} 批；已完成批次在后台评分', 'translate')

        def translate_group(group):
            if stop.is_set():
                raise InterruptedError("已暂停")
            sentences = [s for b in group for s in b["sentences"]]
            indexes = [doc["blocks"].index(b) for b in group]
            nearby = doc["blocks"][max(0, min(indexes)-1):max(indexes)+2]
            context = "\n\n".join(b["id"] + ": " + b["source"] for b in nearby)
            return translate_batch(agent, sentences, context, doc["terms"], cancelled=stop.is_set)

        def commit_group(group, result):
            with GUARD:
                for block in group:
                    ids = {s["id"] for s in block["sentences"]}
                    block["translations"] = {sid: text for sid, text in result["translations"].items() if sid in ids}
                    block["status"] = result["status"]
                    block["issues"] = [i for i in result["issues"] if i["id"] in ids]
                    block["revisions"] = result["revisions"]
                    block["reason"] = result["reason"]
                doc.setdefault('translation_checkpoints', []).append({
                    'time': time.time(), 'source_chars': sum(len(b['source']) for b in group),
                    'block_ids': [b['id'] for b in group], 'status': result['status']})
                atomic_json(folder / "history" / f"{group[0]['id']}.json", result)
                task_progress.preview(doc['task'], group, agent.live_calls-wave_calls)
                save(doc)
            add_log(doc, f"已保存 {sum(len(b['source']) for b in group)} 个原文字符、{len(group)} 个正文段落 · 初译，等待全文评估")
        def finish_wave(completed):
            flat = [b for group in completed for b in group]
            if flat:
                task_progress.advance(doc['task'], flat, agent.live_calls - wave_calls)
                save(doc)

        def score_wave(blocks):
            translations = {sid: value for b in blocks for sid, value in b['translations'].items()}
            backend.predict('kiwi', units_for(blocks, translations))

        run_waves(groups, translate_group, commit_group, score_wave,
                  begin_wave, finish_wave, stop.is_set)
        missing = [s['id'] for b in doc['blocks'] for s in b['sentences']
                   if not b['translations'].get(s['id'])]
        if missing:
            doc['quality'] = {'version': QUALITY_VERSION, 'status': 'awaiting_full_document',
                              **ACCEPTANCE_POLICY, 'missing_ids': missing}
            add_log(doc, '所选页码初译已保存，等待其余正文翻译后进行全文评估')
            return

        def quality_notify(message):
            doc['active_role'] = ('revise' if message.startswith('翻译 agent') else
                                  'accept' if message.startswith('独立验收 agent') else 'evaluate')
            doc['task']['phase'] = doc['active_role']
            add_log(doc, message, doc['active_role'])

        def quality_checkpoint(translations, result):
            with GUARD:
                quality = {k: v for k, v in result.items() if k not in ('translations', 'history')}
                quality['history_file'] = 'history/document-quality.json'
                doc['quality'] = quality
                if result['status'] == 'accepted' and doc.get('task'):
                    doc['task']['done_units'] = doc['task']['total_units']
                    doc['task']['done_weight'] = doc['task']['total_weight']
                for block in doc['blocks']:
                    if not block['sentences']:
                        continue
                    ids = {s['id'] for s in block['sentences']}
                    block['translations'] = {sid: translations[sid] for sid in ids}
                    # All blocks remain provisional until the whole-document gate passes.
                    block['status'] = result['status']
                    block['issues'] = [i for i in result['issues'] if i['id'] in ids]
                    block['reason'] = result['reason']
                    block['revisions'] = result['revisions']
                atomic_json(folder / 'history' / 'document-quality.json', result)
                save(doc)

        # Invalidate old acceptance before real QE, including on dependency/inference failure.
        for block in doc['blocks']:
            if block['sentences']:
                block['status'] = 'needs_review'
                block['reason'] = '等待 COMET 全文评估'
        history_path = folder / 'history' / 'document-quality.json'
        previous = json.loads(history_path.read_text(encoding='utf-8')) if history_path.exists() else None
        doc['quality'] = {'version': QUALITY_VERSION, 'status': 'evaluating', **ACCEPTANCE_POLICY}
        save(doc)
        result = evaluate_document(agent, doc['blocks'], doc['terms'],
            backend,
            cancelled=stop.is_set, checkpoint=quality_checkpoint, notify=quality_notify, previous=previous, diversify=True)
        if result['status'] != 'accepted':
            doc['state'] = 'needs_review'
        add_log(doc, result['reason'])
    except InterruptedError:
        doc["state"] = "paused"
        if doc.get('quality',{}).get('status') == 'evaluating':
            doc['quality']['status'] = 'paused'
        add_log(doc, "已暂停，可继续处理；已完成结果保留")
    except Exception as exc:
        logging.exception("processing failed")
        doc["state"], doc["error"] = "failed", str(exc)[:240]
        if doc.get('quality',{}).get('status') in ('evaluating','needs_review'):
            doc['quality'].update(status='failed',error=doc['error'])
        add_log(doc, doc["error"])
    finally:
        if acquired:
            WORKERS.release()
        with GUARD:
            for block in doc["blocks"]:
                if block["status"] == "processing":
                    block["status"] = "pending"
            if doc["state"] == "processing":
                doc["state"] = "partial" if any(b["sentences"] and b["status"] != "accepted" for b in doc["blocks"]) else "complete"
            if doc.get("task"):
                doc["task"]["finished_at"] = time.time()
            doc["active_role"] = ""
            doc["updated"] = time.time()
            save(doc)
            ACTIVE_TASKS.discard(ident)


def public_doc(doc):
    result = copy.deepcopy(doc)
    from .babeldoc_runtime import available
    result['pdf_export_available'] = available(HOME)
    if result.get('pdf_export', {}).get('state') == 'ready':
        from .babeldoc_replay import ApprovedReplay
        if result['pdf_export'].get('translation_version') != ApprovedReplay(doc).version:
            result['pdf_export']['state'] = 'stale'
    if not doc.get('quality') or doc['quality'].get('version') != QUALITY_VERSION:
        result['quality'] = {'version': QUALITY_VERSION, 'status': 'not_evaluated', **ACCEPTANCE_POLICY,
                             'reason': '可对已有译文进行 COMET 全文复核'}
    result["processing_progress"] = task_progress.snapshot(doc.get("task"), result["state"], blocks=doc['blocks'])
    result["stats"] = {state: sum(b["status"] == state for b in result["blocks"] if b["sentences"])
                       for state in ("accepted", "needs_review", "pending", "processing")}
    return result


def process_pdf_export(ident):
    from .babeldoc_export import export_document
    from .babeldoc_replay import ApprovedReplay
    doc, stop = DOCS[ident], STOPS[ident]
    try:
        with GUARD:
            snapshot = copy.deepcopy(doc)
        result = export_document(snapshot, DATA/'documents'/ident, HOME, stop.is_set)
        with GUARD:
            if ApprovedReplay(doc).version != result['translation_version']:
                raise RuntimeError('译文已更新，请重新生成 PDF')
            doc['pdf_export'] = {'state': 'ready', **result}
            save(doc)
    except InterruptedError:
        with GUARD:
            doc['pdf_export'] = {'state': 'paused', 'message': 'PDF 导出已暂停，可重新生成'}
            save(doc)
    except Exception as exc:
        logging.exception('PDF export failed')
        with GUARD:
            doc['pdf_export'] = {'state': 'failed', 'message': str(exc)[:240]}
            save(doc)
    finally:
        with GUARD:
            ACTIVE_TASKS.discard(ident)


class Handler(BaseHTTPRequestHandler):
    server_version = "ReadMaster/0.1"

    def log_message(self, *args):
        pass

    def response(self, code, body=b"", mime="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if mime.startswith("application/json") else "private, max-age=600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self.valid_host():
            return self.response(403, {"error": "仅允许本机访问"})
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/api/documents":
                with GUARD:
                    documents = sorted(DOCS.values(), key=lambda d: d.get('last_opened', d.get('updated', 0)), reverse=True)
                    return self.response(200, [{"id": d["id"], "name": d["name"], "state": d["state"], "pages": len(d["pages"]),
                        'last_opened': d.get('last_opened', d.get('updated', 0)),
                        'translated_blocks': sum(bool(b['translations']) for b in d['blocks']),
                        'reading_page': d.get('progress', {}).get('page', 1)} for d in documents])
            if path == "/api/settings":
                return self.response(200, {**CONFIG, "has_key": bool(KEY), "codex_available": bool(codex_binary())})
            if path == "/api/models":
                return self.response(200, MODELS.public())
            if path == "/api/codex/status":
                return self.response(200, codex_status())
            if path.startswith("/api/document/"):
                ident = path.split("/")[3]
                doc = DOCS.get(ident)
                if not doc:
                    return self.response(404, {"error": "文献不存在"})
                if '/export/' in path:
                    from .babeldoc_replay import ApprovedReplay
                    kind = path.rsplit('/', 1)[-1]
                    name = {'mono': 'mono_pdf_path', 'dual': 'dual_pdf_path'}.get(kind)
                    export = doc.get('pdf_export', {})
                    if (not name or export.get('state') != 'ready'
                            or export.get('translation_version') != ApprovedReplay(doc).version):
                        return self.response(409, {'error': '当前译文的 PDF 尚未生成'})
                    file = Path(export.get('paths', {}).get(name, '')).resolve()
                    if not file.is_relative_to((DATA/'documents'/ident/'exports').resolve()) or not file.is_file():
                        return self.response(404, {'error': '导出文件不存在'})
                    return self.response(200, file.read_bytes(), 'application/pdf')
                if path.endswith('/search'):
                    return self.response(200, search_pdf(DATA / 'documents' / ident / 'original.pdf',
                                                        query.get('q', [''])[0], doc['blocks']))
                if path.endswith("/pdf"):
                    return self.response(200, (DATA / "documents" / ident / "original.pdf").read_bytes(), "application/pdf")
                if path.endswith("/image"):
                    number = int(query.get("page", ["1"])[0])
                    if not 1 <= number <= len(doc["pages"]):
                        raise ValueError("页码越界")
                    scale = .24 if query.get("thumb") else 1.65
                    cache = DATA / "documents" / ident / f"page-{number}-{'thumb' if scale<1 else 'full'}.png"
                    with GUARD:
                        if not cache.exists():
                            cache.write_bytes(render_page(DATA / "documents" / ident / "original.pdf", number, scale))
                    return self.response(200, cache.read_bytes(), "image/png")
                with GUARD:
                    return self.response(200, public_doc(doc))
            if path == "/api/health":
                return self.response(200, {"ok": True, "version": VERSION})
            file = ROOT / "web" / ("index.html" if path == "/" else path.lstrip("/"))
            if not file.resolve().is_relative_to((ROOT / "web").resolve()) or not file.is_file():
                return self.response(404, {"error": "文件不存在"})
            mime = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}.get(file.suffix, "application/octet-stream")
            self.response(200, file.read_bytes(), mime)
        except (ValueError, OSError) as exc:
            self.response(400, {"error": str(exc)[:200]})

    def do_POST(self):
        global CONFIG, KEY
        origin = self.headers.get("Origin")
        if not self.valid_host() or self.headers.get("X-ReadMaster") != "1" or (origin and origin not in (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")):
            return self.response(403, {"error": "请求来源无效"})
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 80 * 1024 * 1024:
                raise ValueError("文件或请求大小超出限制（80 MB）")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("文件上传未完成")
            # Drain the bounded request body before closing a rejected POST;
            # otherwise Windows can reset the socket and hide the 409 response.
            if SHUTTING_DOWN.is_set() and path not in ("/api/pause", "/api/progress", "/api/shutdown"):
                return self.response(409, {"error": "正在保存并退出，请等待当前任务结束"})
            if path == "/api/import":
                if not raw.startswith(b"%PDF-"):
                    raise ValueError("请拖入有效的 PDF 文件")
                ident = hashlib.sha256(raw).hexdigest()[:24]
                with GUARD:
                    if ident in DOCS:
                        return self.response(200, {"id": ident, "cached": True})
                folder = DATA / "documents" / ident
                folder.mkdir(parents=True, exist_ok=True)
                pdf = folder / "original.pdf"
                pdf.write_bytes(raw)
                result = extract_pdf(pdf)
                filename = urllib.parse.parse_qs(parsed.query).get("name", ["论文.pdf"])[0]
                doc = {**result, "id": ident, "name": Path(filename).name[:180], "state": "ready", "terms": [],
                       "events": [], "error": "", "progress": {"page": 1}, "updated": time.time(), "split_strategy": "literal_period"}
                with GUARD:
                    DOCS[ident] = doc
                    save(doc)
                return self.response(200, {"id": ident, "cached": False})
            body = json.loads(raw)
            if path == "/api/shutdown":
                if ACTIVE_TASKS or any(d["state"] == "processing" for d in DOCS.values()):
                    raise ValueError("请先暂停任务，等待当前调用结束后退出")
                self.response(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path.startswith('/api/models/'):
                with GUARD:
                    if path == '/api/models/save':
                        ident = MODELS.save(body)
                        if MODELS.state['active_id'] == ident:
                            CONFIG, KEY = MODELS.snapshot(ident)
                        return self.response(200, {'id': ident, **MODELS.public()})
                    if path == '/api/models/activate':
                        profile = MODELS.find(body.get('id'))
                        if profile['provider'] == 'codex' and not codex_status()['logged_in']:
                            raise ValueError('请先登录 Codex，再使用这套配置')
                        CONFIG, KEY = MODELS.activate(profile['id'])
                        return self.response(200, MODELS.public())
                    if path == '/api/models/delete':
                        MODELS.delete(body.get('id'))
                        if MODELS.state['active_id'] is None:
                            KEY = ''
                        return self.response(200, MODELS.public())
                    if path == '/api/models/skip':
                        MODELS.skip_onboarding()
                        return self.response(200, MODELS.public())
                    if path == '/api/models/test':
                        config = connection(body)
                        if config['provider'] == 'codex':
                            if not codex_status()['logged_in']:
                                raise ValueError('Codex 尚未登录，请先完成浏览器授权')
                            return self.response(200, {'message': 'Codex 已登录；模型是否可用会在实际翻译时确认。'})
                        key = str(body.get('api_key', '')).strip()
                        if not key and body.get('id'):
                            previous = MODELS.find(body['id'])
                            key = decrypt_key(previous['encrypted_key']) if previous.get('encrypted_key') else ''
                        if not key or '\n' in key or '\r' in key:
                            raise ValueError('请输入有效 API Key')
                # Network probe must not hold the library/configuration lock.
                config['timeout'] = 30
                schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok'], 'additionalProperties': False}
                value = ApiAgent(config, key).call('probe', {'instruction': 'Return {"ok":true}.'}, schema)
                if value != {'ok': True}:
                    raise ValueError('接口返回了结果，但未通过结构化输出检查')
                return self.response(200, {'message': '连接成功，模型 JSON 输出已通过检查。'})
            if path == "/api/settings":
                provider = body.get("provider", "codex")
                if provider not in ("codex", "api"):
                    raise ValueError("模型连接方式无效")
                limit = int(body.get("max_revisions", 2))
                if not 0 <= limit <= 5:
                    raise ValueError("修订上限应为 0–5")
                with GUARD:
                    ident = MODELS.save({**body, 'name': 'Codex' if provider == 'codex' else 'API 配置',
                        'id': 'codex' if provider == 'codex' else (MODELS.state['active_id'] if CONFIG['provider'] == 'api' else None)})
                    CONFIG, KEY = MODELS.activate(ident)
                return self.response(200, {"ok": True})
            if path == "/api/login":
                if not codex_binary():
                    raise ValueError("未找到 Codex")
                # User explicitly clicks login. Credentials handled by official CLI.
                log = open(DATA / "login.log", "w", encoding="utf-8")
                subprocess.Popen([codex_binary(), "login"], stdout=log, stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                log.close()
                return self.response(200, {"ok": True, "message": "已启动 Codex 登录，请在浏览器中完成授权"})
            ident = body.get("id")
            with GUARD:
                if ident not in DOCS:
                    raise ValueError("文献不存在")
                doc = DOCS[ident]
                if SHUTTING_DOWN.is_set() and path not in ("/api/pause", "/api/progress", "/api/shutdown"):
                    return self.response(409, {"error": "正在保存并退出，请等待当前任务结束"})
                if path == "/api/delete":
                    delete_document(ident)
                    return self.response(200, {"ok": True})
                if path == '/api/export':
                    from .babeldoc_replay import ApprovedReplay
                    if ident in ACTIVE_TASKS:
                        return self.response(409, {'error': '这篇论文正在处理，请等待或暂停'})
                    replay = ApprovedReplay(doc)
                    if not replay.expected or replay.unapproved:
                        raise ValueError('请先完成正文翻译和验收，再导出最终 PDF')
                    STOPS[ident].clear()
                    doc['pdf_export'] = {'state': 'processing', 'translation_version': replay.version}
                    save(doc)
                    ACTIVE_TASKS.add(ident)
                    threading.Thread(target=process_pdf_export, args=(ident,), daemon=True).start()
                    return self.response(200, {'ok': True, 'message': '正在生成 PDF'})
                if path == "/api/start":
                    if ident in ACTIVE_TASKS or doc["state"] == "processing":
                        return self.response(409, {"error": "这篇论文正在处理"})
                    if CONFIG["provider"] == "api" and (not KEY or not CONFIG["model"]):
                        raise ValueError("请先在设置中填写模型和密钥")
                    if not MODELS.state['active_id']:
                        raise ValueError('请先在 Models 中选择模型连接')
                    outline_only = body.get('outline_only', False)
                    if not isinstance(outline_only, bool):
                        raise ValueError('目录处理参数无效')
                    if outline_only and not outline_blocks(doc):
                        return self.response(200, {'ok': True})
                    start, end = int(body.get("start_page", 1)), int(body.get("end_page", len(doc["pages"])))
                    if not 1 <= start <= end <= len(doc["pages"]):
                        raise ValueError("请输入有效的页码范围")
                    STOPS[ident] = threading.Event()
                    doc["state"], doc["error"] = "processing", ""
                    selected = [b for b in doc["blocks"] if b["sentences"] and start <= b["page"] <= end
                                and any(not b['translations'].get(s['id']) for s in b['sentences'])]
                    if outline_only:
                        selected = outline_blocks(doc)
                    speed_key = json.dumps({k: CONFIG.get(k) for k in ('provider', 'model', 'endpoint', 'max_revisions')}, sort_keys=True) + ':' + os.environ.get('READMASTER_TRANSLATION_ENGINE', 'babeldoc') + (':outline' if outline_only else ':'+QUALITY_VERSION+':draft-wave2')
                    doc["task"] = task_progress.begin(selected, start, end, CONFIG["model"] or "Codex 默认模型", previous=doc.get('task'), speed_key=speed_key)
                    doc["active_role"], doc["active_page"] = "", start
                    save(doc)
                    ACTIVE_TASKS.add(ident)
                    threading.Thread(target=process_document, args=(ident, start, end, outline_only, copy.deepcopy(CONFIG), KEY), daemon=True).start()
                    return self.response(200, {"ok": True})
                if path == "/api/pause":
                    if ident in STOPS:
                        STOPS[ident].set()
                    return self.response(200, {"ok": True, "message": "将在当前模型调用结束后暂停"})
                if path == "/api/progress":
                    page = int(body.get("page", 1))
                    if not 1 <= page <= len(doc["pages"]):
                        raise ValueError("页码越界")
                    doc["progress"] = {"page": page}
                    doc['last_opened'] = time.time()
                    save(doc)
                    return self.response(200, {"ok": True})
                raise ValueError("接口不存在")
        except (ValueError, OSError, KeyError, RuntimeError) as exc:
            self.response(400, {"error": str(exc)[:240]})
        except Exception:
            logging.exception("request failed")
            self.response(400, {"error": "文件解析或请求处理失败，请检查 PDF 是否损坏并重试"})


def create_server(port=0):
    """Load the local library and bind a server, without opening a browser."""
    global CONFIG, KEY, MODELS
    DATA.mkdir(parents=True, exist_ok=True)
    SHUTTING_DOWN.clear()
    logging.basicConfig(filename=DATA / "application.log", level=logging.INFO, encoding="utf-8")
    legacy = json.loads((DATA / 'settings.json').read_text(encoding='utf-8')) if (DATA / 'settings.json').exists() else None
    MODELS = ModelStore(DATA, legacy, any((DATA / 'documents').glob('*/document.json')))
    if MODELS.state['active_id']:
        try:
            CONFIG, KEY = MODELS.snapshot()
        except ValueError:
            CONFIG = {k: MODELS.find(MODELS.state['active_id'])[k] for k in ('provider', 'model', 'endpoint', 'max_revisions')}
            KEY = ''
    if legacy and not KEY:
        KEY = os.environ.get("READMASTER_API_KEY", "")
    for file in (DATA / "documents").glob("*/document.json"):
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
            if doc.get('layout_version', 1) < LAYOUT_VERSION:
                backup = file.with_name(f'document.before-layout-v{LAYOUT_VERSION}.json')
                if not backup.exists():
                    shutil.copy2(file, backup)
                refresh_layout(doc, extract_pdf(file.with_name('original.pdf')))
                atomic_json(file, doc)
            if doc["state"] == "processing":
                doc["state"] = "paused"
                for block in doc["blocks"]:
                    if block["status"] == "processing":
                        block["status"] = "pending"
            if doc.get('pdf_export', {}).get('state') == 'processing':
                doc['pdf_export']['state'] = 'paused'
                doc['pdf_export']['error'] = '上次 PDF 导出已中断，已保存译文保留，可重新生成'
                atomic_json(file, doc)
            DOCS[doc["id"]] = doc
            STOPS[doc['id']] = threading.Event()
        except (ValueError, KeyError):
            logging.exception("document load failed")
    http = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    # Closing the desktop window waits for in-flight imports/saves too.
    http.daemon_threads = False
    return http


def clear_address(url):
    try:
        if (DATA / "address.txt").read_text(encoding="utf-8") == url:
            (DATA / "address.txt").unlink()
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    if args.port == 0 and (DATA / "address.txt").exists():
        try:
            existing = (DATA / "address.txt").read_text(encoding="utf-8")
            if existing.startswith("http://127.0.0.1:"):
                with urllib.request.urlopen(existing + "/api/health", timeout=1) as reply:
                    health = json.load(reply)
                if health.get("version") in ("0.1.0", "0.1.1", "0.1.2", "0.1.3", "0.1.4", VERSION) and health.get("ok"):
                    if not args.no_browser:
                        webbrowser.open(existing)
                    return
        except Exception:
            pass
    server = create_server(args.port)
    url = f"http://127.0.0.1:{server.server_port}"
    (DATA / "address.txt").write_text(url, encoding="utf-8")
    if sys.stdout:
        print(url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        for stop in STOPS.values():
            stop.set()
    finally:
        server.server_close()
        clear_address(url)


if __name__ == "__main__":
    main()
