"""Shared API output allowance, with bounded provider validation negotiation."""
import re
import threading
import urllib.parse

TARGET = 2 * 128 * 1024
_learned = {}
_lock = threading.Lock()


def budget(endpoint, model):
    host = urllib.parse.urlsplit(endpoint).hostname
    parameter = 'max_completion_tokens' if host == 'api.openai.com' else 'max_tokens'
    # Only the documented GPT-5 variants, not every future GPT-5 family model.
    limit = 128000 if host == 'api.openai.com' and re.fullmatch(r'gpt-5(?:-mini|-nano)?(?:-2025-08-07)?', model) else TARGET
    with _lock:
        return _learned.get((endpoint, model), (parameter, min(TARGET, limit)))


def negotiate(endpoint, model, parameter, current, text):
    """Only use explicit output-parameter validation, never context-window numbers."""
    if 'context' in text or '上下文' in text:
        return None
    if not any(p in text for p in ('max_tokens', 'max_completion_tokens')):
        return None
    alternative = 'max_tokens' if parameter == 'max_completion_tokens' else 'max_completion_tokens'
    if parameter in text and alternative in text and any(s in text for s in ('unsupported', 'not supported', 'use ', 'instead')):
        result = (alternative, current)
    else:
        patterns = (
            r'between\s+\d+\s+and\s+(\d[\d,]*)',
            r'(?:at most|less than or equal to|cannot exceed|must not exceed|maximum(?: allowed)?(?: value)?(?: is| of)?|<=)\s*[\"\']?(\d[\d,]*)',
            r'upper (?:bound|limit)(?: is| of)?\s*[:=]?\s*(\d[\d,]*)',
        )
        limits = [int(m.group(1).replace(',', '')) for pattern in patterns for m in re.finditer(pattern, text)]
        limits = [n for n in limits if 1 <= n < current]
        if not limits:
            return None
        result = (parameter, min(limits))
    with _lock:
        _learned[(endpoint, model)] = result
    return result
