import copy
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import pymupdf as fitz
from readmaster.pdf import extract_pdf, layout_blocks, refresh_layout, COMMON_HEADING, table_regions, search_pdf


def block(text, y, size=10, x=60, width=450):
    box = (x, y, x + width, y + size)
    return {'type': 0, 'bbox': box, 'lines': [{'bbox': box, 'spans': [
        {'text': text, 'size': size, 'font': 'Times-Bold' if size > 10 else 'Times', 'bbox': box}]}]}


class PdfLayoutTests(unittest.TestCase):
    def test_native_search_bounds_cover_word_not_paragraph(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'search.pdf'
            pdf=fitz.open();page=pdf.new_page()
            page.insert_text((60,90),'First target and another target in this paragraph.',fontsize=12)
            box=list(page.get_text('blocks')[0][:4]);pdf.save(path);pdf.close()
            hits=search_pdf(path,'target',[{'id':'b1','page':1,'bbox':box}])
            self.assertEqual(len(hits['b1']),2)
            self.assertTrue(all(r[2]-r[0] < (box[2]-box[0])/3 for r in hits['b1']))
            self.assertEqual(search_pdf(path,'missing',[{'id':'b1','page':1,'bbox':box}]),{})
    def test_three_line_table_includes_header_but_not_caption_or_prose(self):
        pdf = fitz.open()
        page = pdf.new_page()
        for y in (60, 80, 125):
            page.draw_line((60,y),(400,y))
        page.insert_text((65,74),'Model Chr. Var. Emp. Uti. Nat. Sup. Pref. Res. Coh. Gui. Total',fontsize=9)
        page.insert_text((65,100),'Baseline 7.8 6.9 7.8 6.8 7.7 7.1 7.6 7.2 7.9 7.2 74.4',fontsize=9)
        page.insert_text((60,145),'Table 1: Comparison of automatic evaluation results.',fontsize=10)
        page.insert_text((60,175),'This paragraph discusses the results outside the table.',fontsize=10)
        raw=[b for b in page.get_text('dict')['blocks'] if b['type']==0]
        regions=table_regions(page,raw)
        self.assertTrue(any(r.contains(fitz.Point(100,70)) for r in regions))
        self.assertFalse(any(r.contains(fitz.Point(100,145)) for r in regions))
        self.assertFalse(any(r.contains(fitz.Point(100,175)) for r in regions))
        pdf.close()

    def test_table_migration_invalidates_gate_without_losing_prose(self):
        extracted=self.extract([block('This is a complete paragraph with enough words to translate correctly.',230)])
        old=copy.deepcopy(extracted)
        old.update(state='complete',quality={'status':'accepted'},pdf_export={'state':'ready'},task={'total_units':2})
        old['blocks'][0]['translations']={s['id']:'saved' for s in old['blocks'][0]['sentences']}
        old['blocks'][0]['status']='accepted'
        table=copy.deepcopy(old['blocks'][0]); table['id']='table'; table['sentences']=[{'id':'table_001','source':'Var.'}]
        old['blocks'].append(table)
        preserved={**table,'sentences':[],'translations':{},'status':'preserved','preserved_reason':'table'}
        extracted['blocks'].append(preserved)
        refresh_layout(old,extracted)
        self.assertEqual(old['blocks'][0]['translations'],{'p001_b001_001':'saved'})
        self.assertEqual(old['blocks'][1]['sentences'],[])
        self.assertEqual(old['quality']['status'],'not_evaluated')
        self.assertEqual(old['pdf_export']['state'],'stale')
        self.assertNotIn('task',old)

    def extract(self, raw):
        page = SimpleNamespace(rect=fitz.Rect(0, 0, 612, 792),
                               get_text=lambda *a, **k: {'blocks': raw},
                               find_tables=lambda: SimpleNamespace(tables=[]))
        pdf = MagicMock(is_encrypted=False, metadata={})
        pdf.__len__.return_value = 1
        pdf.__iter__.return_value = iter([page])
        pdf.get_toc.return_value = []
        with patch('readmaster.pdf.fitz.open', return_value=pdf):
            return extract_pdf('test.pdf')

    def test_title_all_lines_one_sentence_even_with_period(self):
        raw = [block('A.I. Learning What Interventions Reveal:', 90, 14),
               block('Auditable Cognitive Guidance', 106, 14, 200, 200),
               block('Anonymous submission', 145, 12),
               block('This is a complete paragraph with enough words to translate correctly.', 230)]
        result = self.extract(raw)
        titles = [b for b in result['blocks'] if b['kind'] == 'title']
        self.assertEqual(len(titles), 1)
        self.assertEqual(len(titles[0]['sentences']), 1)
        self.assertIn('Auditable Cognitive Guidance', titles[0]['sentences'][0]['source'])
        self.assertNotIn('Anonymous', titles[0]['source'])
        self.assertEqual(titles[0]['bbox'][3], 120)

    def test_heading_separated_without_losing_body_or_position(self):
        heading = block('Introduction', 200, 12, 150, 90)
        body = block('Cognitive guidance requires an interactive system to infer a human state.', 218)
        combined = {**heading, 'lines': heading['lines'] + body['lines'], 'bbox': (60, 200, 510, 228)}
        result = self.extract([combined])['blocks']
        self.assertEqual(result[0]['source'], 'Introduction')
        self.assertEqual(result[0]['sentences'], [])
        self.assertEqual(result[0]['bbox'], list(heading['bbox']))
        self.assertNotIn('Introduction', result[1]['source'])
        self.assertEqual(result[1]['bbox'], list(body['bbox']))
        self.assertTrue(result[1]['sentences'])

    def test_common_headings_but_not_prose_prefix(self):
        for text in ('1 Introduction', '2.1 Background', 'ABSTRACT', 'Related Work', 'Conclusion'):
            self.assertTrue(COMMON_HEADING.fullmatch(text))
        self.assertFalse(COMMON_HEADING.fullmatch('Background knowledge enables understanding.'))

    def test_migration_keeps_unaffected_translations_and_resets_changed_title(self):
        extracted = self.extract([block('Whole Paper Title', 90, 14),
                                  block('This is a complete paragraph with enough words to translate correctly.', 230)])
        old = copy.deepcopy(extracted)
        old['state'] = 'complete'
        old['processing_progress'] = {'state': 'complete'}
        for b in old['blocks']:
            if b['sentences']:
                b['status'] = 'accepted'
                b['translations'] = {s['id']: 'saved translation' for s in b['sentences']}
        old['blocks'][0]['source'] = 'Partial Paper Title'
        refresh_layout(old, extracted)
        self.assertEqual(old['state'], 'partial')
        self.assertEqual(old['blocks'][0]['status'], 'pending')
        self.assertEqual(old['blocks'][0]['translations'], {})
        self.assertEqual(old['blocks'][1]['status'], 'accepted')
        self.assertTrue(old['blocks'][1]['translations'])
        self.assertNotIn('processing_progress', old)


if __name__ == '__main__':
    unittest.main()
