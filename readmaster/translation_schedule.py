"""Two translation batches per wave; one bounded background scoring queue."""
from concurrent.futures import ThreadPoolExecutor, as_completed


def run_waves(groups, translate, commit, score, begin, finish, cancelled):
    pending = []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='translation') as translations, \
         ThreadPoolExecutor(max_workers=1, thread_name_prefix='kiwi') as scoring:
        for offset in range(0, len(groups), 2):
            if cancelled():
                raise InterruptedError('已暂停')
            # Bound queued scoring snapshots; allow inference to overlap the
            # next wave without retaining a whole extra document in memory.
            if len(pending) >= 2:
                pending.pop(0).result()
            wave = groups[offset:offset + 2]
            begin(wave)
            futures = {translations.submit(translate, group): group for group in wave}
            completed, errors = [], []
            for future in as_completed(futures):
                group = futures[future]
                try:
                    result = future.result()
                    commit(group, result)
                    completed.append(group)
                except Exception as exc:
                    errors.append(exc)
            finish(completed)
            if errors:
                raise errors[0]
            if cancelled():
                raise InterruptedError('已暂停')
            # Snapshot is made by the owner thread before the next wave starts.
            pending.append(scoring.submit(score, [b.copy() for g in wave for b in g]))
        for future in pending:
            future.result()
