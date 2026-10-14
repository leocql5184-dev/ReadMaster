import copy
import math
from pathlib import Path
import tempfile
import unittest

from readmaster.pipeline import split_periods
from readmaster.quality import (evaluate_document, units_for, paper_checks, CometBackend,
                                translate_batch, VERSION, candidate_revisions, score_summary, score_issue_ids)
from scripts.comet_worker import normalize_spans
from readmaster.pdf import numeric_power_offsets


def block(name, source, texts):
    sentences = split_periods(source, name)
    return {'id': name, 'page': 1, 'source': source, 'sentences': sentences,
            'translations': dict(zip((s['id'] for s in sentences), texts))}


PASS = {'passed': True, 'issues': []}


class Agent:
    def __init__(self, replies):
        self.replies, self.calls = iter(replies), []

    def call(self, role, payload, schema):
        self.calls.append((role, copy.deepcopy(payload)))
        if role == 'accept':
            return copy.deepcopy(PASS)
        expected, answer = next(self.replies)
        assert expected == role, (expected, role)
        return answer


class Backend:
    def __init__(self, scores):
        self.scores, self.calls = iter(scores), []

    def predict(self, kind, units):
        self.calls.append((kind, copy.deepcopy(units)))
        if kind == 'kiwi':
            return [{'score': v} for v in next(self.scores)]
        return [{'score': .7, 'error_spans': []} for u in units]


class QualityTests(unittest.TestCase):
    def test_97_percent_gate_strict_point_seven_and_inclusive_point_five(self):
        scores = {str(i): .8 if i<97 else .5 for i in range(100)}
        self.assertTrue(score_summary(scores)['score_gate_passed'])
        self.assertEqual(score_summary(scores)['above_threshold_percentage'],97)
        self.assertEqual(score_issue_ids(scores),set())
        scores['99'] = .499999
        self.assertFalse(score_summary(scores)['score_gate_passed'])
        self.assertEqual(score_issue_ids(scores),{'99'})
        scores['99'] = .7
        scores['96'] = .7
        self.assertFalse(score_summary(scores)['score_gate_passed'])
        self.assertEqual(score_summary(scores)['above_threshold_count'],96)
        scores['96'] = .700001
        self.assertTrue(score_summary(scores)['score_gate_passed'])
        self.assertFalse(score_summary({})['score_gate_passed'])
        with self.assertRaises(ValueError):
            score_summary({'a': math.nan})

    def test_ratio_uses_exact_counts_without_rounding_up(self):
        self.assertFalse(score_summary({str(i):.8 if i<32 else .6 for i in range(33)})['score_gate_passed'])
        self.assertTrue(score_summary({str(i):.8 if i<33 else .6 for i in range(34)})['score_gate_passed'])

    def test_allowed_low_units_still_receive_xcomet_and_independent_acceptance(self):
        blocks = [block(f'a{i}', 'It may help.', ['它可能有帮助。']) for i in range(100)]
        scores = [.8]*97+[.5]*3
        agent = Agent([('evaluate',PASS)])
        backend = Backend([scores])
        result = evaluate_document(agent,blocks,[],backend)
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(result['minimum_score'],.5)
        self.assertEqual(result['above_threshold_percentage'],97)
        self.assertEqual(len(backend.calls[1][1]),3)
        self.assertEqual(agent.calls[-1][0],'accept')

    def test_semantic_error_in_allowed_low_unit_still_requires_repair(self):
        blocks = [block(f'a{i}', 'It may help.', ['它可能有帮助。']) for i in range(100)]
        blocks[-1]['translations']['a99_001'] = '它有帮助。'
        issue = {'id':'a99_001','severity':'blocking','evidence':'may','reason':'漏译不确定性','suggestion':'恢复可能性'}
        agent = Agent([('evaluate',{'passed':False,'issues':[issue]}),
                       ('revise',{'translations':[{'id':'a99_001','text':'它可能有帮助。'}]}),('evaluate',PASS)])
        result = evaluate_document(agent,blocks,[],Backend([[.8]*97+[.6]*3,[.8]*97+[.6]*3]),diversify=True)
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(result['revisions'],1)
        self.assertEqual([s['id'] for s in agent.calls[1][1]['sentences']],['a99_001'])

    def test_score_oscillation_below_run_best_does_not_reset_stall(self):
        b = block('a', 'It may help.', ['它可能有帮助。'])
        replies = []
        for text in ['这可能有帮助。','这或许有所帮助。','它也许能有所帮助。']:
            replies.extend([('evaluate',PASS),('revise',{'translations':[{'id':'a_001','text':text}]})])
        replies.append(('evaluate',PASS))
        result = evaluate_document(Agent(replies),[b],[],Backend([[.6],[.59],[.598],[.595]]))
        self.assertEqual(result['status'],'needs_review')
        self.assertIn('连续三轮',result['reason'])
        self.assertEqual(result['revisions'],3)
        self.assertEqual(len(result['history']),4)

    def test_low_score_keeps_honest_warning_and_forwards_concrete_suggestion(self):
        b = block('a', 'It may help.', ['它可能有帮助。'])
        warning = {'id':'a_001','severity':'warning','evidence':'It may help.',
                   'reason':'未确认误译，尝试另一种忠实表达','suggestion':'可译为“这或许有所帮助。”'}
        agent = Agent([('evaluate',{'passed':True,'issues':[warning]}),
                       ('revise',{'translations':[{'id':'a_001','text':'这或许有所帮助。'}]}),('evaluate',PASS)])
        result = evaluate_document(agent,[b],[],Backend([[.6],[.82]]))
        self.assertEqual(result['status'],'accepted')
        forwarded = agent.calls[1][1]['issues'][0]
        self.assertEqual(forwarded['suggestion'], warning['suggestion'])
        self.assertIn('COMETKiwi',forwarded['reason'])
        self.assertEqual(result['history'][0]['issues'][0]['severity'],'warning')

    def test_paper_numbers_include_footnotes_and_abutting_abbreviations(self):
        b = block('a', 'We use cases2, performance3 and ELI5; see Sec. 3.3 and Grant No. 2107048.',
                  ['我们使用案例2、性能3和 ELI5；见 Sec.', '3.', '3 和 Grant No.', '2107048。'])
        # Period fragments can retain their own spacing without changing numbers.
        self.assertEqual(paper_checks([b], b['translations']), [])
        bad = {**b['translations'], 'a_004':'2107049。'}
        self.assertTrue(paper_checks([b], bad))

    def test_spaced_thousands_are_same_value_but_changed_digit_is_blocked(self):
        b = block('a', 'We train for 4, 000 and 2, 000 steps.', ['训练分别进行4,000和2,000步。'])
        self.assertEqual(paper_checks([b], b['translations']), [])
        self.assertTrue(paper_checks([b], {'a_001':'训练分别进行4,000和3,000步。'}))

    def test_model_name_hyphens_are_not_negative_values(self):
        b = block('a', 'PaLM- 8B returns the top-5 results with values from 1-3 and a minimum of -2.',
                  ['PaLM-8B返回前5个结果，数值从1至3，最小值为-2。'])
        self.assertEqual(paper_checks([b], b['translations']), [])
        self.assertTrue(paper_checks([b], {'a_001':'PaLM-8B返回前5个结果，数值从1至3，最小值为2。'}))

    def test_numeric_power_requires_original_pdf_geometry(self):
        source = 'We used 103 samples.'
        base = {'text':'We used 10','size':10,'flags':0,'origin':[0,20],'bbox':[0,10,50,22]}
        exponent = {'text':'3','size':7,'flags':1,'origin':[50,16],'bbox':[50,9,54,18]}
        tail = {'text':' samples.','size':10,'flags':0,'origin':[54,20],'bbox':[54,10,100,22]}
        offsets = numeric_power_offsets([{'spans':[base,exponent,tail]}], source)
        self.assertEqual(offsets, [10])
        b = block('a', source, ['我们使用10^3个样本。'])
        self.assertTrue(paper_checks([b], b['translations']))
        b['numeric_power_offsets'] = offsets
        self.assertEqual(paper_checks([b], b['translations']), [])
        self.assertTrue(paper_checks([b], {'a_001':'我们使用10^4个样本。'}))
        normal = {**exponent,'size':10,'flags':0,'origin':[50,20]}
        self.assertEqual(numeric_power_offsets([{'spans':[base,normal,tail]}], source), [])

    def test_candidate_ranking_rejects_numeric_error_and_restores_original_ids(self):
        b=block('a','The experiment used 32 samples.',['实验使用32份样本。'])
        class CandidateAgent:
            def call(self,role,payload,schema):
                return {'translations':[{'id':s['id'],'text':text} for s,text in zip(payload['sentences'],
                    ['实验使用64个样本。','该实验使用32个样本。','实验所用样本数为32。'])]}
        patch,trials=candidate_revisions(CandidateAgent(),[b],b['translations'],[],Backend([[.99,.83,.81]]),{'a_001':.6},1)
        self.assertEqual(patch,{'a_001':'该实验使用32个样本。'})
        self.assertEqual(len(trials),3)
        self.assertEqual(trials[0]['score'],.99)

    def test_diversified_revision_returns_to_full_document_acceptance(self):
        b=block('a','It may help.',['它可能有帮助。'])
        candidates={'translations':[{'id':f'candidate{i}:a_001','text':text} for i,text in enumerate(
            ['它或许有所帮助。','这可能有帮助。','这或许有用。'])]}
        agent=Agent([('evaluate',PASS),('revise',{'translations':[{'id':'a_001','text':'它可能有帮助。'}]}),
                     ('revise',candidates),('evaluate',PASS)])
        result=evaluate_document(agent,[b],[],Backend([[.6],[.68,.82,.75],[.82]]),diversify=True)
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(result['translations'],{'a_001':'这可能有帮助。'})
        self.assertEqual(agent.calls[-1][0],'accept')
        self.assertEqual(len(result['history'][1]['candidates']),3)

    def test_document_context_and_targeted_revision_even_when_llm_passes(self):
        blocks = [block('a', 'It may help.', ['它有帮助。']), block('b', 'It works.', ['它有效。'])]
        agent = Agent([('evaluate', PASS), ('revise', {'translations': [{'id': 'a_001', 'text': '它可能有帮助。'}]}), ('evaluate', PASS)])
        backend = Backend([[.6, .95], [.85, .95]])
        saved = []
        result = evaluate_document(agent, blocks, [], backend, checkpoint=lambda t, r: saved.append(copy.deepcopy(r)))
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['minimum_score'], .85)
        self.assertEqual(len(agent.calls[0][1]['document']['blocks']), 2)
        self.assertEqual([s['id'] for s in agent.calls[1][1]['sentences']], ['a_001'])
        repair = agent.calls[1][1]
        self.assertEqual(repair['document']['blocks'][1]['source'], 'It works.')
        self.assertEqual(repair['document']['blocks'][1]['translation'], '它有效。')
        self.assertNotIn('It works.', repair['context'])
        self.assertEqual([u['id'] for u in backend.calls[1][1]], ['a_001'])
        self.assertEqual(saved[1]['reason'], '修订已保存，等待重新评分')
        self.assertIsNone(saved[1]['scores'])
        self.assertEqual(saved[1]['translations']['a_001'], '它可能有帮助。')

    def test_one_low_unit_cannot_hide_behind_high_average(self):
        blocks = [block('a', 'It may help.', ['它有帮助。']), block('b', 'It works.', ['它有效。'])]
        agent = Agent([('evaluate', PASS), ('revise', {'translations': [{'id': 'a_001', 'text': '它有帮助。'}]})])
        result = evaluate_document(agent, blocks, [], Backend([[.69, 1.]]))
        self.assertEqual(result['status'], 'needs_review')
        self.assertIn('循环', result['reason'])
        self.assertEqual(result['minimum_score'], .69)
        self.assertAlmostEqual(result['document_score'], .845)

    def test_threshold_is_strictly_greater_than_point_seven(self):
        b = block('a', 'It works.', ['它有效。'])
        agent = Agent([('evaluate', PASS), ('revise', {'translations': [{'id': 'a_001', 'text': '它有效。'}]})])
        equal = evaluate_document(agent, [b], [], Backend([[.7]]))
        self.assertEqual(equal['status'], 'needs_review')
        self.assertEqual(equal['threshold_operator'], '>')
        above = evaluate_document(Agent([('evaluate', PASS)]), [b], [], Backend([[.700001]]))
        self.assertEqual(above['status'], 'accepted')
        self.assertEqual(above['threshold'], .7)

    def test_high_score_cannot_override_numbers_and_units(self):
        b = block('a', 'Latency is 15 ms.', ['延迟为 5 秒。'])
        issues = paper_checks([b], b['translations'])
        self.assertTrue(any('数字' in i['reason'] for i in issues))
        self.assertTrue(any('单位' in i['reason'] for i in issues))
        agent = Agent([('evaluate', PASS), ('revise', {'translations': [{'id': 'a_001', 'text': '延迟为 15 毫秒。'}]}), ('evaluate', PASS)])
        result = evaluate_document(agent, [b], [], Backend([[.9], [.91]]))
        self.assertEqual(result['status'], 'accepted')

    def test_decimal_fragments_rejoin_and_revive_all_related_ids(self):
        b = block('p', 'Accuracy is 0.5.', ['准确率为 0.', '5。'])
        units = units_for([b], b['translations'])
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]['src'], 'Accuracy is 0.5.')
        self.assertEqual(units[0]['mt'], '准确率为 0.5。')
        self.assertEqual(paper_checks([b], b['translations']), [])
        patch = {'translations': [{'id': 'p_001', 'text': '准确率为 0.'}, {'id': 'p_002', 'text': '5。'}]}
        agent = Agent([('evaluate', PASS), ('revise', patch)])
        result = evaluate_document(agent, [b], [], Backend([[.7]]))
        self.assertEqual(len(agent.calls[1][1]['sentences']), 2)
        self.assertEqual(result['status'], 'needs_review')

    def test_comparison_abbreviation_keeps_a_complete_unit_and_frozen_ids(self):
        b=block('a','ReAct vs. CoT yields better results.',['ReAct 与','CoT 相比获得了更好的结果。'])
        units=units_for([b],b['translations'])
        self.assertEqual(len(units),1)
        self.assertEqual(units[0]['src'],b['source'])
        self.assertEqual(units[0]['ids'],['a_001','a_002'])
        self.assertEqual([s['id'] for s in b['sentences']],['a_001','a_002'])

    def test_page_continuation_rejoins_around_caption_without_changing_ids(self):
        before=block('a','We note that editing the policy',['我们注意到修改策略'])
        caption=block('c','Figure 5: An example.',['图5：示例。'])
        after=block('b','is difficult for prior methods.',['对先前方法很困难。'])
        for b,page in [(before,1),(caption,2),(after,2)]:
            b.update(page=page,kind='prose',font_size=10,bbox=[100,50,500,100])
        values={k:v for b in [before,caption,after] for k,v in b['translations'].items()}
        units=units_for([before,caption,after],values)
        self.assertEqual(len(units),2)
        self.assertEqual(units[0]['ids'],['a_001','b_001'])
        self.assertEqual(units[0]['src'],'We note that editing the policy is difficult for prior methods.')
        self.assertEqual(units[1]['ids'],['c_001'])
        self.assertEqual(after['sentences'][0]['id'],'b_001')

    def test_next_page_new_paragraph_is_not_a_continuation(self):
        before=block('a','An unfinished fragment',['一个片段'])
        after=block('b','We start a new section.',['我们开始新的章节。'])
        for b,page in [(before,1),(after,2)]:
            b.update(page=page,kind='prose',font_size=10,bbox=[100,50,500,100])
        self.assertEqual(len(units_for([before,after],{**before['translations'],**after['translations']})),2)

    def test_rules_protect_formula_and_citation(self):
        b = block('a', 'For x=2 use [12].', ['对于 x=3，使用参考文献。'])
        issues = paper_checks([b], b['translations'])
        self.assertTrue(any('公式' in i['reason'] for i in issues))
        self.assertTrue(any('引用' in i['reason'] for i in issues))

    def test_formula_sentence_punctuation_is_not_a_math_change(self):
        b = block('a', 'We use x=2.', ['我们使用 x=2。'])
        self.assertEqual(paper_checks([b], b['translations']), [])

    def test_hard_failure_and_cancellation_do_not_accept(self):
        b = block('a', 'It works.', ['它有效。'])
        with self.assertRaises(InterruptedError):
            evaluate_document(Agent([]), [b], [], Backend([]), cancelled=lambda: True)
        with tempfile.TemporaryDirectory() as temp:
            backend = CometBackend(temp, Path(temp)/'cache')
            with self.assertRaisesRegex(RuntimeError, '未安装'):
                evaluate_document(Agent([]), [b], [], backend)

    def test_bad_predictions_rejected(self):
        unit = {'mt': '译文'}
        for score in (True, math.nan, math.inf, '0.9'):
            with self.assertRaises(ValueError):
                CometBackend._validate({'score': score}, 'kiwi', unit)
        with self.assertRaises(ValueError):
            CometBackend._validate({'score': .9, 'error_spans': [{'start': 0, 'end': 10, 'severity': 'major'}]}, 'xcomet', unit)

    def test_decoder_whitespace_is_not_used_as_original_evidence(self):
        spans = normalize_spans([{'start':0,'end':3,'text':'中 文','severity':'major'}], '中文。')
        self.assertEqual(spans[0]['text'], '中文。')
        self.assertEqual(spans[0]['model_text'], '中 文')
        CometBackend._validate({'score':.7,'error_spans':spans}, 'xcomet', {'mt':'中文。'})

    def test_pending_revision_survives_next_inference_failure(self):
        b=block('a','It may help.',['它有帮助。'])
        agent=Agent([('evaluate',PASS),('revise',{'translations':[{'id':'a_001','text':'它可能有帮助。'}]})])
        saved=[]
        class Failing(Backend):
            def predict(self,kind,units):
                if kind=='kiwi' and len([c for c in self.calls if c[0]=='kiwi'])==1:
                    raise RuntimeError('test inference failure')
                return super().predict(kind,units)
        with self.assertRaisesRegex(RuntimeError,'inference failure'):
            evaluate_document(agent,[b],[],Failing([[.5]]),checkpoint=lambda t,r:saved.append(copy.deepcopy(r)))
        self.assertEqual(saved[-1]['translations']['a_001'],'它可能有帮助。')
        self.assertEqual(saved[-1]['status'],'needs_review')
        self.assertIsNone(saved[-1]['scores'])

    def test_resume_preserves_history_and_allows_reassessment(self):
        b = block('a', 'It works.', ['它有效。'])
        first = evaluate_document(Agent([('evaluate', PASS)]), [b], [], Backend([[.8]]))
        second = evaluate_document(Agent([('evaluate', PASS)]), [b], [], Backend([[.85]]), previous=first)
        self.assertEqual(second['status'], 'accepted')
        self.assertEqual(len(second['history']), 2)
        self.assertEqual(second['version'], VERSION)

    def test_first_translation_does_not_claim_quality_acceptance(self):
        b = block('a', 'It works.', ['它有效。'])
        agent = Agent([('translate', {'translations': [{'id': 'a_001', 'text': '它有效。'}]})])
        result = translate_batch(agent, b['sentences'], 'context', [])
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual([role for role, _ in agent.calls], ['translate'])


if __name__ == '__main__':
    unittest.main()
