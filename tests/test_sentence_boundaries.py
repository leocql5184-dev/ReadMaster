import unittest

from readmaster.pipeline import split_sentences, validate_translations
from readmaster.pdf import refresh_layout


class SentenceBoundaryTests(unittest.TestCase):
    def check_parts(self, text, expected):
        parts = split_sentences(text)
        self.assertEqual([s['source'].strip() for s in parts], expected)
        self.assertEqual(''.join(s['source'] for s in parts), text)
        for s in parts:
            self.assertEqual(text[s['start']:s['end']], s['source'])

    def test_decimal_abbreviation_and_variables(self):
        self.check_parts('Fig. 3 shows x_i = 0.25, e.g. a sample. Next.',
                         ['Fig. 3 shows x_i = 0.25, e.g. a sample.', 'Next.'])

    def test_math_and_tail(self):
        self.check_parts('Use $x = 1.2 + a.b$ here. Then \\(v_i = 0.5\\) works.  ',
                         ['Use $x = 1.2 + a.b$ here.', 'Then \\(v_i = 0.5\\) works.'])

    def test_numbered_items(self):
        self.check_parts('Overview: (1) First item (2) Second item',
                         ['Overview:', '(1) First item', '(2) Second item'])
        self.check_parts('介绍：（1）第一项（2）第二项。', ['介绍：', '（1）第一项', '（2）第二项。'])

    def test_equation_reference_not_item(self):
        self.check_parts('Use Eq. (1) to compute f(2) and x. Next.',
                         ['Use Eq. (1) to compute f(2) and x.', 'Next.'])

    def test_unicode_punctuation(self):
        self.check_parts('😀 有效吗？有效！ Next... Done.', ['😀 有效吗？', '有效！', 'Next...', 'Done.'])

    def test_formula_omission_rewrite_duplicate_rejected(self):
        sentences = split_sentences('Use $x_i = 0.5$ here.')
        for text in ['在此使用。', '在此使用 $x_i = 5$。', '$x_i = 0.5$ $x_i = 0.5$']:
            with self.assertRaises(ValueError):
                validate_translations({'translations': [{'id': 's_001', 'text': text}]}, sentences)
        validate_translations({'translations': [{'id': 's_001', 'text': '在此使用 $x_i = 0.5$。'}]}, sentences)

    def test_same_ids_with_changed_boundaries_do_not_reuse_translations(self):
        old = {'id': 'b', 'source': 'same', 'sentences': [{'id': 'b_001', 'source': 'old'}],
               'translations': {'b_001': '旧译文'}, 'status': 'accepted', 'history': [], 'issues': []}
        fresh = {**old, 'sentences': [{'id': 'b_001', 'source': 'new'}], 'translations': {}, 'status': 'pending'}
        doc = {'blocks': [old], 'state': 'idle'}
        refresh_layout(doc, {'blocks': [fresh]})
        self.assertEqual(doc['blocks'][0]['translations'], {})
        self.assertEqual(doc['quality']['status'], 'not_evaluated')


if __name__ == '__main__':
    unittest.main()
