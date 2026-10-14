"""Match native PDF paragraphs to frozen, approved reader units without guessing."""
import hashlib
import json
import re
import unicodedata


def normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def formula_pattern(text):
    """Match a formula literally without stealing digits/letters inside another token."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return None
    punctuation = {',': '，', ';': '；', ':': '：', '(': '（', ')': '）'}
    expression = r'\s*'.join('['+re.escape(c+punctuation[c])+']' if c in punctuation
                             else re.escape(c) for c in chars)
    if re.fullmatch(r'[A-Za-z0-9]', chars[0]):
        expression = r'(?<![A-Za-z0-9.])' + expression
    if re.fullmatch(r'[A-Za-z0-9]', chars[-1]):
        expression += r'(?![A-Za-z0-9.])'
    return re.compile(expression)


def matching_pdf_glyph(box, characters):
    """Recover a CID only from a unique glyph at the same physical location."""
    x0, y0, x1, y1 = box
    matches = []
    for character in characters:
        a, b, c, d = character['bbox']
        # PDFMiner and MuPDF have different vertical font metrics. Horizontal
        # glyph advance must agree tightly and the native centre must lie in
        # the MuPDF glyph box. Multiple candidates are never guessed.
        if abs(x0-a) > .25 or abs(x1-c) > .25 or not b <= (y0+y1)/2 <= d:
            continue
        value = character['c']
        if value and value.strip() and '\ufffd' not in value and '(cid:' not in value:
            matches.append(value)
    return matches[0] if len(matches) == 1 else None


def numeric_superscript_text(glyphs):
    """Express a printed numeric power only when size and raised baseline prove it."""
    if not glyphs or any(not re.fullmatch(r'[0-9∼~\s]', g['text']) for g in glyphs):
        return None
    size = max(g['size'] for g in glyphs)
    base = [g['bottom'] for g in glyphs if g['size'] >= size*.85 and g['text'].isdigit()]
    if not size or not base:
        return None
    baseline = sorted(base)[len(base)//2]
    output, raised, any_power = '', False, False
    for g in glyphs:
        small = g['size'] < size*.85
        if small:
            if not g['text'].isdigit() or g['bottom'] <= baseline+size*.15:
                return None
            if not raised:
                if not output or not output[-1].isdigit():
                    return None
                output += '^'
                any_power = True
        output += g['text']
        raised = small
    return output if any_power else None


def restore_accent_order(native_source, frozen_source, verified_hats):
    """Restore extraction order only with physical proof of every hat glyph."""
    count = native_source.count('ˆ')
    if not count or count != frozen_source.count('ˆ') or count != verified_hats:
        return None
    if normalized(native_source.replace('ˆ', '')) != normalized(frozen_source.replace('ˆ', '')):
        return None
    return frozen_source


def restore_formula_placeholders(target, formulas, aliases=None, source=''):
    """Assign nonoverlapping literal spans, longest formulas before variables."""
    groups = {}
    for text, placeholder in formulas:
        groups.setdefault(text, []).append(placeholder)
    assignments = []
    for text in sorted(groups, key=lambda s: len(normalized(s)), reverse=True):
        variants = list(dict.fromkeys([text, *(aliases or {}).get(text, [])]))
        patterns = [formula_pattern(s) for s in variants]
        if any(p is None for p in patterns):
            return None
        matches = [m for pattern in patterns for m in pattern.finditer(target)
                   if not any(m.start() < end and start < m.end()
                              for start, end, _ in assignments)]
        matches.sort(key=lambda m: m.start())
        # A native formula can be a subscript in an unchanged name (BUTLERg)
        # or a footnote attached to an author's name (5Micheli). Require the
        # exact surrounding source token, never loosen general token bounds.
        if not matches and re.fullmatch(r'[A-Za-z0-9]+', text) and source:
            contexts = list(re.finditer(r'(?<![A-Za-z0-9.])([A-Za-z0-9]*)('+
                                       re.escape(text)+r')([A-Za-z0-9]*)(?![A-Za-z0-9.])', source))
            contexts = [m for m in contexts if re.search(r'[A-Za-z]', m.group(1)+m.group(3))]
            anchored = []
            for context in contexts:
                pattern = re.compile(r'(?<![A-Za-z0-9.])'+re.escape(context.group(1))+
                                     '('+re.escape(text)+')'+re.escape(context.group(3))+
                                     r'(?![A-Za-z0-9.])')
                for m in pattern.finditer(target):
                    start, end = m.span(1)
                    if not any(start < b and a < end for a, b, _ in assignments):
                        anchored.append((start, end))
            anchored = sorted(set(anchored))
            if len(anchored) == len(groups[text]):
                assignments.extend((a, b, p) for (a,b),p in zip(anchored, groups[text]))
                continue
        if len(matches) != len(groups[text]):
            return None
        assignments.extend((m.start(), m.end(), placeholder)
                           for m, placeholder in zip(matches, groups[text]))
    for start, end, placeholder in sorted(assignments, reverse=True):
        target = target[:start] + placeholder + target[end:]
    return target


class ApprovedReplay:
    def __init__(self, document):
        self.by_page = {}
        self.used = set()
        self.expected = set()
        self.unapproved = set()
        snapshot = []
        for block in document['blocks']:
            if not block.get('sentences'):
                continue
            self.expected.update(s['id'] for s in block['sentences'])
            if block.get('status') != 'accepted':
                self.unapproved.update(s['id'] for s in block['sentences'])
                continue
            units = []
            for sentence in block['sentences']:
                ident = sentence['id']
                target = block.get('translations', {}).get(ident)
                if not isinstance(target, str) or not target.strip():
                    raise ValueError('验收译文不完整，不能生成最终 PDF')
                units.append((ident, normalized(sentence['source']), target))
                snapshot.append((ident, sentence['source'], target))
            self.by_page.setdefault(block['page'], []).extend(units)
        self.version = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False).encode()).hexdigest()

    def lookup(self, page, source, *, commit=True):
        target = normalized(source)
        if not target:
            return None
        units = self.by_page.get(page, [])
        matches = []
        for start in range(len(units)):
            text = ''
            for end in range(start, len(units)):
                text += units[end][1]
                if not target.startswith(text):
                    break
                if text == target:
                    matches.append(units[start:end+1])
                    break
        if not matches:
            # The native parser encodes a printed line-end hyphen as "Con- sider".
            # Try only this explicit extraction artifact, then require the entire
            # normalized text to match frozen units exactly and uniquely.
            joined = normalized(re.sub(r'(?<=\w)-\s+(?=[a-z])', '', source))
            if joined != target:
                for start in range(len(units)):
                    text = ''
                    for end in range(start, len(units)):
                        text += units[end][1]
                        if not joined.startswith(text):
                            break
                        if text == joined:
                            matches.append(units[start:end+1])
                            break
        if len(matches) != 1:
            return None
        match = matches[0]
        if any(unit[0] in self.used for unit in match):
            raise ValueError('同一原句被重复写入 PDF')
        if commit:
            self.used.update(unit[0] for unit in match)
        return ''.join(unit[2] for unit in match)

    def report(self):
        return {'translation_version': self.version, 'expected_units': len(self.expected),
                'rendered_units': len(self.used), 'missing_ids': sorted(self.expected-self.used),
                'unapproved_ids': sorted(self.unapproved),
                'complete': bool(self.expected) and self.used == self.expected}
