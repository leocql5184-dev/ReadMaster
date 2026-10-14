import unittest
from readmaster.babeldoc_replay import ApprovedReplay, formula_pattern, matching_pdf_glyph, restore_formula_placeholders, numeric_superscript_text, restore_accent_order


def block(ident, page, source, translation, status='accepted'):
    return {'page': page, 'status': status, 'sentences': [{'id': ident, 'source': source}],
            'translations': {ident: translation}}


class ReplayTests(unittest.TestCase):
    def test_embedded_subscript_and_footnote_require_unchanged_source_context(self):
        self.assertEqual(restore_formula_placeholders('BUTLERg 的结果', [('g','{g}')], source='BUTLERg results use greedy decoding.'), 'BUTLER{g} 的结果')
        self.assertEqual(restore_formula_placeholders('5Micheli 使用 3553 个样本', [('5','{f}')], source='5Micheli used 3553 samples.'), '{f}Micheli 使用 3553 个样本')
        self.assertIsNone(restore_formula_placeholders('BUTLERg 的结果', [('g','{g}')], source='OTHERg results.'))
        self.assertIsNone(restore_formula_placeholders('BUTLERg 与 BUTLERg', [('g','{g}')], source='BUTLERg results.'))

    def test_accent_order_requires_all_glyphs_verified_and_identical_other_content(self):
        self.assertEqual(restore_accent_order('ˆ Define A.', 'Define ˆ A.', 1), 'Define ˆ A.')
        self.assertIsNone(restore_accent_order('ˆ Define A.', 'Define ˆ B.', 1))
        self.assertIsNone(restore_accent_order('ˆ Define A.', 'Define ˆ A.', 0))
        self.assertIsNone(restore_accent_order('ˆ Define A.', 'Define ˆˆ A.', 1))

    def test_numeric_powers_require_raised_smaller_glyphs_and_exact_target(self):
        glyphs = [{'text': '1', 'size': 10, 'bottom': 100}, {'text': '0', 'size': 10, 'bottom': 100},
                  {'text': '3', 'size': 7, 'bottom': 103}]
        self.assertEqual(numeric_superscript_text(glyphs), '10^3')
        self.assertEqual(restore_formula_placeholders('10^3 个样本', [('103', '{v}')], {'103':['10^3']}), '{v} 个样本')
        self.assertIsNone(restore_formula_placeholders('10^4 个样本', [('103', '{v}')], {'103':['10^3']}))
        glyphs[-1]['bottom'] = 97
        self.assertIsNone(numeric_superscript_text(glyphs))
        glyphs[-1].update(size=10, bottom=100)
        self.assertIsNone(numeric_superscript_text(glyphs))

    def test_nested_formula_tokens_are_assigned_once_without_modifying_other_text(self):
        text = '观测 ot ∈O，上下文 (ot)，映射 ct 7→at。'
        formulas = [('ot', '{v1}'), ('ot ∈ O', '{v2}'), ('ct 7→ at', '{v3}')]
        self.assertEqual(restore_formula_placeholders(text, formulas), '观测 {v2}，上下文 ({v1})，映射 {v3}。')
        self.assertIsNone(restore_formula_placeholders(text+'还有 ot。', formulas))
        self.assertIsNone(restore_formula_placeholders('观测 ot ∈O。', [('ot', '{v1}'), ('ot ∈ O', '{v2}')]))

    def test_cid_recovery_requires_unique_glyph_location_and_advance(self):
        glyph = {'c': '7', 'bbox': [10, 10, 15, 20]}
        self.assertEqual(matching_pdf_glyph([10, 12, 15, 19], [glyph]), '7')
        self.assertIsNone(matching_pdf_glyph([10, 12, 15, 19], [glyph, glyph]))
        self.assertIsNone(matching_pdf_glyph([11, 12, 16, 19], [glyph]))
        self.assertIsNone(matching_pdf_glyph([10, 22, 15, 29], [glyph]))
        self.assertIsNone(matching_pdf_glyph([10, 12, 15, 19], [{'c': '\ufffd', 'bbox': glyph['bbox']}]))

    def test_formula_tokens_do_not_match_inside_acronyms_numbers_or_section_references(self):
        self.assertEqual(formula_pattern('1').findall('1我们展示 GPT-3，见 Appendix A.1 和 21。'), ['1'])
        self.assertEqual(formula_pattern('L').findall('语言空间 L，LLMs。'), ['L'])
        self.assertEqual(formula_pattern('103 ∼ 105').findall('用 103∼105 个样本。'), ['103∼105'])
        self.assertIsNone(formula_pattern('103 ∼ 105').search('用 10^3∼10^5 个样本。'))
        self.assertEqual(formula_pattern('1,').findall('2022)1，通过提示生成动作。'), ['1，'])

    def test_merged_native_paragraph_uses_final_units_and_version_changes_on_revision(self):
        doc = {'blocks': [block('s1', 1, 'One.', '第一句。'), block('s2', 1, ' Two.', '第二句。')]}
        replay = ApprovedReplay(doc)
        self.assertEqual(replay.lookup(1, 'One.\nTwo.'), '第一句。第二句。')
        self.assertTrue(replay.report()['complete'])
        doc['blocks'][1]['translations']['s2'] = '修订后的第二句。'
        revised = ApprovedReplay(doc)
        self.assertNotEqual(replay.version, revised.version)
        self.assertEqual(revised.lookup(1, 'One. Two.'), '第一句。修订后的第二句。')

    def test_missing_or_ambiguous_mapping_is_never_published_as_complete(self):
        doc = {'blocks': [block('s1', 1, 'Same.', '甲。'), block('s2', 1, 'Same.', '乙。')]}
        replay = ApprovedReplay(doc)
        self.assertIsNone(replay.lookup(1, 'Same.'))
        self.assertFalse(replay.report()['complete'])
        self.assertEqual(replay.report()['missing_ids'], ['s1', 's2'])

    def test_wrong_page_partial_sentence_and_duplicate_render_rejected(self):
        replay = ApprovedReplay({'blocks': [block('s1', 1, 'A complete sentence.', '完整句子。')]})
        self.assertIsNone(replay.lookup(2, 'A complete sentence.'))
        self.assertIsNone(replay.lookup(1, 'A complete'))
        self.assertEqual(replay.lookup(1, 'A complete sentence.'), '完整句子。')
        with self.assertRaises(ValueError):
            replay.lookup(1, 'A complete sentence.')

    def test_needs_review_never_becomes_approved_pdf_content(self):
        replay = ApprovedReplay({'blocks': [block('s1', 1, 'One.', '错误译文。', 'needs_review')]})
        self.assertIsNone(replay.lookup(1, 'One.'))
        self.assertFalse(replay.report()['complete'])

    def test_detached_punctuation_probe_requires_whole_sentence_and_does_not_consume(self):
        replay = ApprovedReplay({'blocks': [block('s1', 1, 'A footnote.', '脚注。')]})
        self.assertIsNone(replay.lookup(1, 'A footnote', commit=False))
        self.assertEqual(replay.lookup(1, 'A footnote.', commit=False), '脚注。')
        self.assertEqual(replay.report()['rendered_units'], 0)
        self.assertEqual(replay.lookup(1, 'A footnote.'), '脚注。')
        self.assertTrue(replay.report()['complete'])

    def test_line_wrap_hyphen_matches_whole_frozen_source_but_not_arbitrary_edits(self):
        replay = ApprovedReplay({'blocks': [block('s1', 1, 'Consider the experiment.', '考虑该实验。')]})
        self.assertIsNone(replay.lookup(1, 'Con-sider the experiment.'))
        self.assertIsNone(replay.lookup(1, 'Con- sider another experiment.'))
        self.assertEqual(replay.lookup(1, 'Con- sider the experiment.'), '考虑该实验。')


if __name__ == '__main__':
    unittest.main()
