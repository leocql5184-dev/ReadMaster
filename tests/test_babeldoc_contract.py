import unittest

from readmaster.babeldoc_contract import BabelBatch
from readmaster.pipeline import split_periods


class BabelContractTests(unittest.TestCase):
    def test_reordered_output_keeps_original_ids_and_source_unchanged(self):
        source = split_periods('First. Second.', 'p001_b001')
        batch = BabelBatch(source)
        result = batch.decode([{'id': 1, 'output': '第二句。'}, {'id': 0, 'output': '第一句。'}])
        self.assertEqual(result['translations'], [
            {'id': 'p001_b001_001', 'text': '第一句。'},
            {'id': 'p001_b001_002', 'text': '第二句。'}])
        self.assertEqual(''.join(s['source'] for s in source), 'First. Second.')

    def test_bad_correspondence_never_guesses_translation_order(self):
        batch = BabelBatch([{'id': 's1', 'source': 'One.'}])
        for value in ([], [{'id': 1, 'output': '一。'}],
                      [{'id': 0, 'output': '一。'}, {'id': 0, 'output': '二。'}],
                      [{'id': True, 'output': '一。'}],
                      [{'id': '0', 'output': '一。'}], [{'id': 0, 'output': ''}]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                batch.decode(value)

    def test_decimal_boundary_still_has_to_match_reader(self):
        batch = BabelBatch(split_periods('Set it to 0.3.', 'p1'))
        with self.assertRaisesRegex(ValueError, '小数'):
            batch.decode([{'id': 0, 'output': '设为0.3。'}, {'id': 1, 'output': '3。'}])
        result = batch.decode([{'id': 0, 'output': '设为0.'}, {'id': 1, 'output': '3。'}])
        self.assertEqual(''.join(x['text'] for x in result['translations']), '设为0.3。')

    def test_formula_and_style_markers_must_be_preserved(self):
        batch = BabelBatch([{'id': 's1', 'source': "Use <style id='1'>x</style> and {v1}."}])
        with self.assertRaisesRegex(ValueError, '占位符'):
            batch.decode([{'id': 0, 'output': '使用x和公式。'}])
        result = batch.decode([{'id': 0, 'output': "使用<style id='1'>x</style>和{v1}。"}])
        self.assertEqual(result['translations'][0]['id'], 's1')


if __name__ == '__main__':
    unittest.main()
