"""Progress for a selected page range, independent of saved reading position."""
import copy
import math
import time


def begin(blocks, start_page, end_page, model, now=None, previous=None, speed_key=None):
    job = {"started_at": time.time() if now is None else now, "finished_at": None,
            "start_page": start_page, "end_page": end_page, "model": model,
            "total_units": sum(len(b["sentences"]) for b in blocks), "done_units": 0,
            "total_weight": sum(len(b["source"]) for b in blocks), "done_weight": 0,
            "sample_weight": 0, "sample_seconds": 0, "active_started_at": None,
            "phase": "queued", "page": start_page, "speed_key": speed_key}
    # Reuse measured speed, never old completed work or time spent paused.
    if (speed_key and previous and previous.get('speed_key') == speed_key
            and previous.get('sample_weight', 0) > 0 and previous.get('sample_seconds', 0) > 0):
        job['prior_weight'] = previous['sample_weight']
        job['prior_seconds'] = previous['sample_seconds']
    elif speed_key and previous and previous.get('speed_key') == speed_key:
        job['prior_weight'] = previous.get('prior_weight', 0)
        job['prior_seconds'] = previous.get('prior_seconds', 0)
    return job


def activate(job, group, now=None):
    job["active_started_at"] = time.time() if now is None else now
    job["page"] = group[0]["page"]
    job['preview_weight'] = job['preview_seconds'] = 0


def preview(job, group, live_calls, now=None):
    """Estimate after the first saved batch, without waiting for its sibling."""
    now = time.time() if now is None else now
    if live_calls and job.get('active_started_at') is not None:
        job['preview_weight'] = job.get('preview_weight', 0) + sum(len(b['source']) for b in group)
        job['preview_seconds'] = max(0, now-job['active_started_at'])


def advance(job, group, live_calls, now=None):
    now = time.time() if now is None else now
    weight = sum(len(b["source"]) for b in group)
    duration = max(0, now - job["active_started_at"])
    job["done_units"] += sum(len(b["sentences"]) for b in group)
    job["done_weight"] += weight
    # Cache-only batches do not predict the speed of uncached model calls.
    if live_calls and duration >= 1:
        job["sample_weight"] += weight
        job["sample_seconds"] += duration
    job["active_started_at"] = None
    job['preview_weight'] = job['preview_seconds'] = 0


def snapshot(job, state, now=None, blocks=None):
    if not job:
        if blocks is None:
            return None
        job = begin([], 1, max((b['page'] for b in blocks), default=1), '', now=now)
    now = time.time() if now is None else now
    result = copy.deepcopy(job)
    total = job["total_units"]
    done = min(total, job["done_units"])
    finished = job.get("finished_at") is not None
    result["percent"] = min(100 if finished else 99, math.floor(done / total * 100)) if total else (100 if finished else 0)
    result["eta_seconds"] = None
    result["overrun"] = False
    estimating_draft = job.get('estimate_scope') == 'initial_translation'
    can_estimate = not estimating_draft or job.get('phase') in ('queued', 'terms', 'translate')
    weight = job.get('sample_weight',0) + job.get('preview_weight',0)
    duration = job.get('sample_seconds',0) + job.get('preview_seconds',0)
    if not weight or duration < 1:
        weight, duration = job.get('prior_weight',0), job.get('prior_seconds',0)
    remaining = max(0, job['total_weight']-job['done_weight'])
    if blocks is not None:
        remaining = sum(len(b['source']) for b in blocks
                        if job['start_page'] <= b['page'] <= job['end_page']
                        and any(not b.get('translations',{}).get(s['id']) for s in b['sentences']))
    if state == "processing" and weight and remaining and can_estimate:
        seconds = remaining * duration / weight
        if job["active_started_at"] is not None:
            # A preview already excludes saved work; subtract only time since it.
            seconds -= max(0, now - job["active_started_at"] - job.get('preview_seconds',0))
        if seconds > 0:
            result["eta_seconds"] = math.ceil(seconds)
        else:
            result["overrun"] = True
    result["state"] = "complete" if finished and done == total and state not in ("paused", "failed", "needs_review", "partial") else state
    if blocks is not None:
        sentences = [(b,s) for b in blocks for s in b['sentences']]
        saved = [(b,s) for b,s in sentences if b.get('translations',{}).get(s['id'])]
        result.update(total_units=len(sentences), done_units=len(saved),
                      percent=math.floor(len(saved)*100/len(sentences)) if sentences else 0,
                      draft_complete=bool(sentences) and len(saved)==len(sentences),
                      progress_scope='document')
        if result['draft_complete']:
            result.update(state='complete', percent=100, eta_seconds=None, overrun=False)
    return result
