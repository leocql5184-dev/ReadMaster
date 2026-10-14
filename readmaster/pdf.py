"""Layout extraction. Original pixels stay intact; only prose receives overlays."""
import re
import pymupdf as fitz
from .pipeline import split_periods

LAYOUT_VERSION = 3
NUMERIC_LAYOUT_VERSION = 1
COMMON_HEADING = re.compile(r'^(?:(?:\d+(?:\.\d+)*\.?|[A-Z])\s+)?(?:abstract|introduction|background|related work|preliminaries|methods?|methodology|experiments?|experimental (?:setup|results)|results(?: and discussion)?|discussion|conclusions?|limitations|acknowledg(?:e)?ments|references|bibliography|appendix)\s*[:.]?$', re.I)


def layout_blocks(raw, page_number):
    """Separate heading lines before prose extraction; join adjacent title lines."""
    separated = []
    for index, block in enumerate(raw):
        ident = f'p{page_number:03d}_b{index+1:03d}'
        block = {**block, '_ident': ident}
        lines = block['lines']
        first = ''.join(s['text'] for s in lines[0]['spans']).strip() if lines else ''
        if len(lines) > 1 and COMMON_HEADING.fullmatch(first):
            heading_rect = fitz.Rect(lines[0]['bbox'])
            body_rect = fitz.Rect(lines[1]['bbox'])
            for line in lines[2:]:
                body_rect |= fitz.Rect(line['bbox'])
            separated.append({**block, 'lines': lines[:1], 'bbox': tuple(heading_rect), '_ident': ident + '_heading'})
            block = {**block, 'lines': lines[1:], 'bbox': tuple(body_rect)}
        separated.append(block)
    if page_number != 1:
        return separated
    candidates = []
    for index, block in enumerate(separated):
        spans = [s for line in block['lines'] for s in line['spans']]
        text = ' '.join(s['text'] for s in spans).strip()
        size = max((s['size'] for s in spans), default=0)
        if block['bbox'][1] < 200 and size >= 13 and len(re.findall(r'[A-Za-z]{2,}', text)) >= 2 and not COMMON_HEADING.fullmatch(text):
            candidates.append((index, size))
    if not candidates:
        return separated
    # Earliest large text near the top is the title, not the smaller author row.
    start, size = candidates[0]
    title = {**separated[start], '_title': True}
    end = start + 1
    while end < len(separated):
        other = separated[end]
        other_size = max((s['size'] for l in other['lines'] for s in l['spans']), default=0)
        text = ' '.join(s['text'] for l in other['lines'] for s in l['spans'])
        if (abs(other_size-size) > 1 or other['bbox'][1]-title['bbox'][3] > size
                or other['bbox'][1] < title['bbox'][1] or COMMON_HEADING.fullmatch(text.strip())):
            break
        title['lines'] = title['lines'] + other['lines']
        title['bbox'] = tuple(fitz.Rect(title['bbox']) | fitz.Rect(other['bbox']))
        end += 1
    return separated[:start] + [title] + separated[end:]


def numeric_power_offsets(lines, source):
    """Record a numeric superscript only when the original glyph geometry proves it."""
    marked_lines = []
    for line in lines:
        parts, previous = [], None
        for span in line['spans']:
            value = span['text']
            if (previous and re.fullmatch(r'[+-]?\d+', value)
                    and re.search(r'\d$', previous['text'])
                    and not previous.get('flags', 0) & 1 and span.get('flags', 0) & 1
                    and span['size'] < previous['size'] * .85
                    and previous['origin'][1] - span['origin'][1] > previous['size'] * .15
                    and abs(span['bbox'][0] - previous['bbox'][2]) < previous['size'] * .5):
                parts.append('\x01')
            parts.append(value)
            previous = span
        marked_lines.append(''.join(parts))
    marked = re.sub(r'(?<=\w)-\n(?=[a-z])', '', '\n'.join(marked_lines))
    marked = re.sub(r'\s+', ' ', marked).strip().replace('ﬁ', 'fi').replace('ﬂ', 'fl')
    if marked.replace('\x01', '') != source:
        return []
    offsets = []
    for index, char in enumerate(marked):
        if char == '\x01':
            offsets.append(index - len(offsets))
    return offsets


def refresh_numeric_layout(doc, path):
    """Enrich existing documents; never replace sentences, translations or progress."""
    if doc.get('numeric_layout_version') == NUMERIC_LAYOUT_VERSION:
        return
    extracted = {b['id']: b for b in extract_pdf(path)['blocks']}
    for block in doc['blocks']:
        fresh = extracted.get(block['id'])
        if fresh and fresh['source'] == block['source']:
            block['numeric_power_offsets'] = fresh['numeric_power_offsets']
    doc['numeric_layout_version'] = NUMERIC_LAYOUT_VERSION


def table_regions(page, raw):
    """Find gridded tables plus caption-anchored academic horizontal-rule tables."""
    try:
        regions = [fitz.Rect(t.bbox) for t in page.find_tables().tables]
    except Exception:
        regions = []
    captions = []
    for block in raw:
        text = ' '.join(s['text'] for line in block['lines'] for s in line['spans'])
        if re.match(r'^\s*Table\s+(?:\d+|[A-Z]\d*|[IVX]+)\s*[.:]', text, re.I):
            captions.append(fitz.Rect(block['bbox']))
    if not captions or not hasattr(page, 'get_drawings'):
        return regions
    groups = []
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] != 'l':
                continue
            a, b = item[1:3]
            if abs(a.y-b.y) > 1 or abs(a.x-b.x) < 60:
                continue
            x0, x1, y = min(a.x,b.x), max(a.x,b.x), (a.y+b.y)/2
            group = next((g for g in groups if abs(g[0]-x0)<3 and abs(g[1]-x1)<3), None)
            if group is None:
                group = [x0, x1, []]
                groups.append(group)
            group[2].append(y)
    for x0, x1, positions in groups:
        relevant = sorted([c for c in captions if min(c.x1,x1)-max(c.x0,x0) > min(c.width,x1-x0)*.5], key=lambda c:c.y0)
        # Captions separate tables with identical widths in the same column.
        segments = [[]]
        for y in sorted(set(positions)):
            if segments[-1] and any(segments[-1][-1] < c.y0 < y for c in relevant):
                segments.append([])
            segments[-1].append(y)
        for ys in segments:
            if len(ys)<2 or ys[-1]-ys[0]<12:
                continue
            adjacent = any(0 <= c.y0-ys[-1] <= 40 or 0 <= ys[0]-c.y1 <= 40 for c in relevant)
            if adjacent:
                regions.append(fitz.Rect(x0,ys[0],x1,ys[-1]))
    return regions


def extract_pdf(path):
    document = fitz.open(path)
    if document.is_encrypted:
        raise ValueError("暂不支持加密 PDF，请先用阅读软件解除密码")
    if not 1 <= len(document) <= 200:
        raise ValueError("第一版支持 1–200 页 PDF")
    pages, blocks, ref_section = [], [], False
    for pi, page in enumerate(document):
        pages.append({"number": pi + 1, "width": page.rect.width, "height": page.rect.height})
        # PyMuPDF sorts blocks spatially; for two-column layouts separate columns.
        raw = [b for b in page.get_text("dict", sort=True)["blocks"] if b["type"] == 0]
        table_rects = table_regions(page, raw)
        abstract_y = next((b['bbox'][1] for b in raw if ''.join(s['text'] for l in b['lines'] for s in l['spans']).strip().upper() == 'ABSTRACT'), None)
        left, right, wide = [], [], []
        middle = page.rect.width / 2
        for b in raw:
            x0, y0, x1, y1 = b["bbox"]
            if x1 < middle + 12:
                left.append(b)
            elif x0 > middle - 12:
                right.append(b)
            else:
                wide.append(b)
        if len(left) >= 3 and len(right) >= 3:
            # Sort bands delimited by full-width blocks to preserve headings.
            raw = []
            prev = -1
            for w in sorted(wide, key=lambda b: b["bbox"][1]):
                end = w["bbox"][1]
                raw.extend(sorted([b for b in left if prev <= b["bbox"][1] < end], key=lambda b: b["bbox"][1]))
                raw.extend(sorted([b for b in right if prev <= b["bbox"][1] < end], key=lambda b: b["bbox"][1]))
                raw.append(w)
                prev = end
            raw.extend(sorted([b for b in left if b["bbox"][1] >= prev], key=lambda b: b["bbox"][1]))
            raw.extend(sorted([b for b in right if b["bbox"][1] >= prev], key=lambda b: b["bbox"][1]))
        raw = layout_blocks(raw, pi + 1)
        for bi, b in enumerate(raw):
            lines = b["lines"]
            line_texts = ["".join(s["text"] for s in line["spans"]) for line in lines]
            original = "\n".join(line_texts)
            text = re.sub(r"(?<=\w)-\n(?=[a-z])", "", original)
            text = re.sub(r"\s+", " ", text).strip().replace("ﬁ", "fi").replace("ﬂ", "fl")
            if not text:
                continue
            spans = [s for l in lines for s in l["spans"]]
            size = sum(s["size"] * len(s["text"]) for s in spans) / max(1, sum(len(s["text"]) for s in spans))
            if re.fullmatch(r"REFERENCES|References|BIBLIOGRAPHY", text):
                ref_section = True
            if re.match(r"^(?:[A-Z]\s+)?APPENDIX", text, re.I) or re.match(r"^[A-Z]\s+[A-Z][A-Z ]{4,}", text):
                ref_section = False
            words = re.findall(r"[A-Za-z]{2,}", text)
            top, bottom = b["bbox"][1], b["bbox"][3]
            header_footer = top < 28 or bottom > page.rect.height - 25 or re.fullmatch(r"\d+", text)
            equation = len(words) < 5 or sum(c.isalpha() for c in text) / max(1, len(text)) < .42
            # Conservative: retain short headings, formulas, references, tabular data.
            corrupted = any(ord(c) < 32 and c not in '\n\r\t' for c in original)
            rect = fitz.Rect(b['bbox'])
            tabular = any((rect & table).get_area() > rect.get_area() * .6 for table in table_rects)
            author_metadata = pi == 0 and abstract_y is not None and top < abstract_y and size < 13
            eligible = not header_footer and not equation and not ref_section and not corrupted and not tabular and not author_metadata and size >= 8 and len(words) >= 10
            title = b.get('_title', False)
            if title:
                eligible = not corrupted and not tabular
            elif COMMON_HEADING.fullmatch(text):
                eligible = False
            ident = b['_ident']
            blocks.append({"id": ident, "page": pi+1, "bbox": list(b["bbox"]), "source": text,
                "numeric_power_offsets": numeric_power_offsets(lines, text),
                "original": original, "font_size": size, "kind": "preserved" if tabular else "title" if title else "prose" if eligible else "preserved",
                "preserved_reason": "table" if tabular else None,
                "lines": [{"text": t, "bbox": list(l["bbox"]), "font_size": l['spans'][0]['size'] if l['spans'] else size}
                          for t, l in zip(line_texts, lines)],
                "sentences": ([{"id": ident + '_001', "source": text, "start": 0, "end": len(text)}] if title else split_periods(text, ident)) if eligible else [], "status": "pending" if eligible else "preserved",
                "translations": {}, "history": [], "issues": []})
    if not any(b["sentences"] for b in blocks):
        raise ValueError("没有找到可翻译的文字层；扫描 PDF 的 OCR 将在后续支持")
    result = {"layout_version": LAYOUT_VERSION, "numeric_layout_version": NUMERIC_LAYOUT_VERSION, "pages": pages, "blocks": blocks, "outline": [
        {"level": n, "title": t, "page": p} for n, t, p in document.get_toc()],
        "title": document.metadata.get("title") or ""}
    document.close()
    return result


def render_page(path, page_number, scale=1.65):
    with fitz.open(path) as document:
        return document[page_number-1].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")


def search_pdf(path, query, blocks):
    """Return native glyph bounds, never a whole paragraph's rectangle."""
    query = query.strip()
    if not query or len(query) > 200:
        return {}
    matches = {}
    with fitz.open(path) as document:
        for number, page in enumerate(document, 1):
            owners = [b for b in blocks if b['page'] == number]
            for hit in page.search_for(query):
                candidates = [( (fitz.Rect(b['bbox']) & hit).get_area(), b) for b in owners]
                area, block = max(candidates, key=lambda item:item[0], default=(0,None))
                if block and area > hit.get_area()*.5:
                    matches.setdefault(block['id'], []).append(list(hit))
    return matches


def refresh_layout(doc, extracted):
    """Keep translations only when original text and immutable sentence IDs match."""
    previous = {b['id']: b for b in doc['blocks']}
    for block in extracted['blocks']:
        old = previous.get(block['id'])
        if (block['sentences'] and old and old['source'] == block['source']
                and [s['id'] for s in old['sentences']] == [s['id'] for s in block['sentences']]):
            for key in ('status', 'translations', 'history', 'issues'):
                block[key] = old[key]
    table_ids = {s['id'] for b in extracted['blocks'] if b.get('preserved_reason') == 'table'
                 for s in previous.get(b['id'], {}).get('sentences', [])}
    doc['blocks'] = extracted['blocks']
    doc['layout_version'] = LAYOUT_VERSION
    if table_ids:
        # The score denominator and exported overlay changed. Require fresh QE,
        # keeping all unaffected prose translations and the prior history on disk.
        doc['quality'] = {'status': 'not_evaluated', 'reason': '表格已保留原版，正文等待重新评估'}
        if doc.get('pdf_export'):
            doc['pdf_export']['state'] = 'stale'
        if doc.get('state') == 'complete':
            doc['state'] = 'needs_review'
        doc.pop('task', None)
        doc.pop('processing_progress', None)
    if any(b['status'] == 'pending' for b in doc['blocks']):
        if doc['state'] == 'complete':
            doc['state'] = 'partial'
        doc.pop('processing_progress', None)
    return doc
