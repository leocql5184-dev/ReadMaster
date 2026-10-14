"""Run the pinned PDF engine in isolation and replay final reader translations.

No API credentials or fresh model requests are used during PDF generation.
Unmapped content is reported; an incomplete export is never a final artifact.
"""
import argparse
import asyncio
import importlib.metadata
import json
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from readmaster.babeldoc_replay import ApprovedReplay, matching_pdf_glyph, restore_formula_placeholders, numeric_superscript_text, restore_accent_order


async def export(job):
    from scripts.babeldoc_assets import configure
    configure()
    from pdf2zh_next.config.model import SettingsModel
    from pdf2zh_next.translator.base_translator import BaseTranslator
    from pdf2zh_next.translator.rate_limiter.qps_rate_limiter import QPSRateLimiter
    from pdf2zh_next import high_level
    from babeldoc.format.pdf import high_level as native
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator, ParagraphTranslateTracker
    from babeldoc.format.pdf.document_il.utils.layout_helper import get_char_unicode_string
    from babeldoc.format.pdf.document_il.il_version_1 import PdfParagraphComposition

    if importlib.metadata.version('babeldoc') != '0.5.24':
        raise RuntimeError('Unsupported BabelDOC version')
    snapshot = json.loads(Path(job['document']).read_text(encoding='utf-8'))
    replay = ApprovedReplay(snapshot)
    output = Path(job['output']).resolve()
    output.mkdir(parents=True, exist_ok=True)
    native_paragraphs = []
    recovered_glyphs = []
    original_characters = {}
    recovered_accent_order = []
    accent_probes = []
    recovered_symbol_order = []
    class InspectingParagraphFinder(native.ParagraphFinder):
        def process(self, docs):
            import pymupdf
            with pymupdf.open(job['input']) as original:
                for page in docs.page:
                    source_page = original[page.page_number]
                    characters = [{**char, 'size': span['size']} for block in source_page.get_text('rawdict')['blocks']
                                  for line in block.get('lines', []) for span in line['spans']
                                  for char in span['chars']]
                    original_characters[page.page_number] = characters
                    reader_boxes = [b['bbox'] for b in snapshot['blocks']
                                    if b['page'] == page.page_number+1 and b.get('sentences')]
                    for char in page.pdf_character:
                        if not re.fullmatch(r'\(cid:\d+\)', char.char_unicode or ''):
                            continue
                        box = char.box
                        rect = [box.x, source_page.rect.height-box.y2,
                                box.x2, source_page.rect.height-box.y]
                        cx, cy = (rect[0]+rect[2])/2, (rect[1]+rect[3])/2
                        if not any(a <= cx <= c and b <= cy <= d for a,b,c,d in reader_boxes):
                            continue
                        value = matching_pdf_glyph(rect, characters)
                        if value is not None:
                            recovered_glyphs.append({'page': page.page_number+1, 'bbox': rect,
                                                     'cid': char.char_unicode, 'unicode': value})
                            char.char_unicode = value
            return super().process(docs)

        def check_cid_paragraph(self, docs):
            from babeldoc.format.pdf.document_il.utils.paragraph_helper import is_cid_paragraph
            heights = {p['number']: p['height'] for p in snapshot['pages']}
            entries = []
            for page in docs.page:
                number = page.page_number+1
                for para in page.pdf_paragraph:
                    box = para.box
                    bbox = [box.x, heights[number]-box.y2, box.x2, heights[number]-box.y]
                    area = max(0, bbox[2]-bbox[0])*max(0, bbox[3]-bbox[1])
                    source = para.unicode or ''
                    # PDFMiner can append underscored identifier glyphs at the
                    # end. Repair only their order, with identical other text,
                    # exact counts and glyph-by-glyph physical PDF evidence.
                    if '_' in source:
                        chars = []
                        for composition in para.pdf_paragraph_composition:
                            if composition.pdf_line:
                                chars.extend(composition.pdf_line.pdf_character)
                            elif composition.pdf_formula:
                                chars.extend(composition.pdf_formula.pdf_character)
                            elif composition.pdf_character:
                                chars.append(composition.pdf_character)
                        verified = sum(matching_pdf_glyph(
                            [c.box.x, heights[number]-c.box.y2, c.box.x2, heights[number]-c.box.y],
                            [g for g in original_characters[page.page_number] if g['c'] == '_']) == '_'
                            for c in chars if c.char_unicode == '_')
                        candidates = []
                        for block in snapshot['blocks']:
                            if block['page'] != number or not block.get('sentences'):
                                continue
                            rect = block['bbox']
                            overlap = max(0,min(bbox[2],rect[2])-max(bbox[0],rect[0]))*max(0,min(bbox[3],rect[3])-max(bbox[1],rect[1]))
                            block_area = (rect[2]-rect[0])*(rect[3]-rect[1])
                            compact = lambda t: re.sub(r'\s+', '', t.replace('_',''))
                            if (area and block_area and min(overlap/area,overlap/block_area) >= .85
                                and verified == source.count('_') == block['source'].count('_')
                                and compact(source) == compact(block['source'])):
                                candidates.append(block['source'])
                        if len(candidates) == 1 and source != candidates[0]:
                            source = para.unicode = candidates[0]
                            recovered_symbol_order.append({'page':number, 'symbol':'_', 'verified':verified})
                    if 'ˆ' in source:
                        chars = []
                        for composition in para.pdf_paragraph_composition:
                            if composition.pdf_line:
                                chars.extend(composition.pdf_line.pdf_character)
                            elif composition.pdf_formula:
                                chars.extend(composition.pdf_formula.pdf_character)
                            elif composition.pdf_character:
                                chars.append(composition.pdf_character)
                        verified = sum(matching_pdf_glyph(
                            [c.box.x, heights[number]-c.box.y2, c.box.x2, heights[number]-c.box.y],
                            [g for g in original_characters[page.page_number] if g['c'] == 'ˆ']) == 'ˆ'
                            for c in chars if c.char_unicode == 'ˆ')
                        accent_probes.append({'page': number, 'verified_hats': verified,
                            'native': [[c.box.x, heights[number]-c.box.y2, c.box.x2, heights[number]-c.box.y]
                                       for c in chars if c.char_unicode == 'ˆ'],
                            'original': [g for g in original_characters[page.page_number]
                                         if g['c'] == 'ˆ']})
                        candidates = []
                        for block in snapshot['blocks']:
                            if block['page'] != number or not block.get('sentences'):
                                continue
                            rect = block['bbox']
                            overlap = max(0, min(bbox[2], rect[2])-max(bbox[0], rect[0]))*max(0, min(bbox[3], rect[3])-max(bbox[1], rect[1]))
                            block_area = (rect[2]-rect[0])*(rect[3]-rect[1])
                            if not area or not block_area or min(overlap/area, overlap/block_area) < .9:
                                continue
                            restored = restore_accent_order(source, block['source'], verified)
                            if restored is not None:
                                candidates.append(restored)
                        if len(candidates) == 1 and candidates[0] != source:
                            para.unicode = candidates[0]
                            recovered_accent_order.append({'page': number, 'bbox': bbox,
                                                           'verified_hats': verified})
                    preserved = False
                    for block in snapshot['blocks']:
                        if block['page'] != number or block.get('sentences'):
                            continue
                        rect = block['bbox']
                        overlap = max(0, min(bbox[2], rect[2])-max(bbox[0], rect[0]))*max(0, min(bbox[3], rect[3])-max(bbox[1], rect[1]))
                        if area and overlap/area >= .5:
                            preserved = True
                            break
                    entries.append({'page': number, 'source': para.unicode or '',
                                    'cid': is_cid_paragraph(para), 'bbox': bbox,
                                    'preserved_region': preserved})
            (output/'parsed-paragraphs.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
            # Figure labels can use undecodable embedded fonts. They are kept
            # as original glyphs, never translated. Bypass the coarse global
            # threshold only when every CID paragraph is in a reader-preserved
            # region; final exact source coverage is still mandatory.
            if all(e['preserved_region'] for e in entries if e['cid']):
                return False
            return super().check_cid_paragraph(docs)

    class ReplayTranslator(BaseTranslator):
        name = 'readmaster-replay'
        model = 'approved-translations'
        def __init__(self, settings):
            super().__init__(settings, QPSRateLimiter(100))
        def do_translate(self, text, rate_limit_params=None):
            # Only the customized IL boundary is allowed to replay translations.
            raise RuntimeError('Unexpected model request during PDF replay')

    class ReplayIL(ILTranslator):
        def translate(self, docs):
            for page in docs.page:
                # The layout model can detach a sentence's final punctuation.
                # Rejoin only when the full text uniquely matches whole reader
                # units; no partial sentence or translation slicing is allowed.
                paragraphs = page.pdf_paragraph
                # Rejoin adjacent native fragments only when their entire text
                # exactly equals frozen reader units (including split captions).
                index = 0
                while index < len(paragraphs):
                    first = paragraphs[index]
                    joined = first.unicode or ''
                    if replay.lookup(page.page_number+1, joined, commit=False) is None:
                        for end in range(index+1, min(len(paragraphs),index+8)):
                            joined += paragraphs[end].unicode or ''
                            if replay.lookup(page.page_number+1, joined, commit=False) is None:
                                continue
                            for tail in paragraphs[index+1:end+1]:
                                first.pdf_paragraph_composition.extend(tail.pdf_paragraph_composition)
                                first.box.x = min(first.box.x,tail.box.x)
                                first.box.y = min(first.box.y,tail.box.y)
                                first.box.x2 = max(first.box.x2,tail.box.x2)
                                first.box.y2 = max(first.box.y2,tail.box.y2)
                            first.unicode = joined
                            del paragraphs[index+1:end+1]
                            break
                    index += 1
                for index in range(len(paragraphs)-1, 0, -1):
                    tail, previous = paragraphs[index], paragraphs[index-1]
                    suffix = tail.unicode or ''
                    if not suffix or not re.fullmatch(r'[.?!;,:。！？；，：]+', suffix.strip()):
                        continue
                    joined = (previous.unicode or '') + suffix
                    if replay.lookup(page.page_number+1, joined, commit=False) is None:
                        continue
                    previous.unicode = joined
                    previous.pdf_paragraph_composition.extend(tail.pdf_paragraph_composition)
                    previous.box.x = min(previous.box.x, tail.box.x)
                    previous.box.y = min(previous.box.y, tail.box.y)
                    previous.box.x2 = max(previous.box.x2, tail.box.x2)
                    previous.box.y2 = max(previous.box.y2, tail.box.y2)
                    del paragraphs[index]
                for paragraph in page.pdf_paragraph:
                    self.translation_config.raise_if_cancelled()
                    source = paragraph.unicode or ''
                    native_paragraphs.append({'page': page.page_number+1, 'source': source})
                    used_before = set(replay.used)
                    target = replay.lookup(page.page_number+1, source)
                    if target is None:
                        for block in snapshot['blocks']:
                            if (block['page'] != page.page_number+1 or
                                not block['id'].endswith('_heading') or block.get('sentences')):
                                continue
                            prefix = block['source']
                            if source.startswith(prefix+' '):
                                translated = replay.lookup(page.page_number+1, source[len(prefix):])
                                if translated is not None:
                                    target = prefix+' '+translated
                                    break
                    if target is None:
                        continue
                    # Function-call brackets/commas are prose punctuation,
                    # sometimes misclassified as standalone native formulas.
                    compositions = []
                    for composition in paragraph.pdf_paragraph_composition:
                        formula = composition.pdf_formula
                        literal = get_char_unicode_string(formula.pdf_character) if formula else ''
                        identifier_underscores = (literal and re.fullmatch(r'[_\s]+', literal)
                            and source.count('_') == len(re.findall(r'(?<=[A-Za-z0-9])_(?=[A-Za-z0-9])', source)))
                        if literal and (re.fullmatch(r'[\[\],\s]+', literal) or identifier_underscores):
                            compositions.extend(PdfParagraphComposition(pdf_character=c)
                                                for c in formula.pdf_character)
                        else:
                            compositions.append(composition)
                    paragraph.pdf_paragraph_composition[:] = compositions
                    fonts = {f.font_id: f for f in page.pdf_font}
                    tracker = ParagraphTranslateTracker()
                    text, translate_input = self.pre_translate_paragraph(paragraph, tracker, fonts, {})
                    if text is None:
                        replay.used = used_before
                        continue
                    formula_placeholders = [p for p in translate_input.placeholders if hasattr(p, 'formula')]
                    formula_texts = [get_char_unicode_string(p.formula.pdf_character) for p in formula_placeholders]
                    aliases = {}
                    alias_probes = []
                    for text, placeholder in zip(formula_texts, formula_placeholders):
                        glyphs = []
                        height = snapshot['pages'][page.page_number]['height']
                        for c in placeholder.formula.pdf_character:
                            if (c.char_unicode or '').isspace():
                                continue
                            rect = [c.box.x, height-c.box.y2, c.box.x2, height-c.box.y]
                            originals = [g for g in original_characters[page.page_number]
                                         if matching_pdf_glyph(rect, [g]) == c.char_unicode]
                            if len(originals) != 1:
                                if re.fullmatch(r'[0-9∼~\s]+', text):
                                    alias_probes.append({'text': text, 'glyph': c.char_unicode,
                                                         'bbox': rect, 'candidates': len(originals)})
                                glyphs = []
                                break
                            g = originals[0]
                            glyphs.append({'text': g['c'], 'size': g['size'], 'bottom': -g['origin'][1]})
                        alternative = numeric_superscript_text(glyphs)
                        if alternative is not None:
                            aliases.setdefault(text, []).append(alternative)
                    restored = restore_formula_placeholders(target, [
                        (text, placeholder.placeholder)
                        for text, placeholder in zip(formula_texts, formula_placeholders)], aliases, source)
                    if restored is None:
                        replay.used = used_before
                        native_paragraphs[-1]['unsupported'] = 'formula_correspondence'
                        native_paragraphs[-1]['formula_texts'] = formula_texts
                        native_paragraphs[-1]['formula_aliases'] = aliases
                        native_paragraphs[-1]['alias_probes'] = alias_probes
                        native_paragraphs[-1]['approved_target'] = target
                        continue
                    # Preserve the original native formula objects, not font approximations.
                    target = restored
                    tracker.new_llm_translate_tracker()
                    self.post_translate_paragraph(paragraph, tracker, translate_input, target)

    settings = SettingsModel.model_validate({
        'basic': {'debug': True},
        'translation': {'output': str(output), 'lang_in': 'en', 'lang_out': 'zh',
                        'qps': 1, 'pool_max_workers': 1, 'no_auto_extract_glossary': True,
                        'ignore_cache': True},
        'pdf': {'skip_scanned_detection': True, 'watermark_output_mode': 'no_watermark'},
        'translate_engine_settings': {'translate_engine_type': 'OpenAI',
                                     'openai_api_key': 'unused-replay', 'openai_model': 'unused-replay'}
    })
    original_factory = high_level.create_babeldoc_config
    # These version-pinned substitutions live only in this dedicated process.
    high_level.get_translator = lambda settings: ReplayTranslator(settings)
    high_level.get_term_translator = lambda settings: ReplayTranslator(settings)
    def factory(settings, file):
        config = original_factory(settings, file)
        config.debug = False  # Inline execution without visual debug overlays.
        return config
    high_level.create_babeldoc_config = factory
    native.ILTranslator = ReplayIL
    native.ParagraphFinder = InspectingParagraphFinder
    paths = {}
    async for event in high_level.do_translate_async_stream(settings, job['input']):
        if event['type'] == 'error':
            raise RuntimeError(str(event.get('error', 'PDF engine failed')))
        if event['type'] == 'finish':
            result = event['translate_result']
            paths = {name: str(getattr(result, name)) for name in ('mono_pdf_path', 'dual_pdf_path')
                     if getattr(result, name, None)}
    report = {**replay.report(), 'paths': paths, 'native_paragraphs': native_paragraphs,
              'model_calls': 0, 'recovered_glyphs': recovered_glyphs,
              'recovered_accent_order': recovered_accent_order, 'accent_probes': accent_probes,
              'recovered_symbol_order': recovered_symbol_order}
    (output / 'replay-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'native_paragraphs'}), flush=True)
    if not report['complete']:
        raise RuntimeError('PDF replay is incomplete; see replay-report.json')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', required=True, type=Path)
    args = parser.parse_args()
    job = json.loads(args.job.read_text(encoding='utf-8'))
    asyncio.run(export(job))


if __name__ == '__main__':
    main()
