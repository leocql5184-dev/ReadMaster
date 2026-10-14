"""Publish translation batches at paragraph boundaries after a character threshold."""
CHAR_THRESHOLD = 3000


def paragraph_batches(blocks, threshold=CHAR_THRESHOLD):
    if threshold <= 0:
        raise ValueError('保存字符阈值必须大于零')
    group, size = [], 0
    for block in blocks:
        group.append(block)
        size += len(block['source'])
        if size >= threshold:
            yield group
            group, size = [], 0
    if group:
        yield group
