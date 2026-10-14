import unittest

from readmaster.pipeline import split_periods

from readmaster import quality


def make_block(source, texts):
    sentences = split_periods(source, 'b')
    return {'id': 'b', 'page': 1, 'source': source, 'sentences': sentences,
            'translations': {s['id']: text for s, text in zip(sentences, texts)}}


class Backend:
    def predict(self, kind, units):
        return [{'score': .95, **({'error_spans': []} if kind == 'xcomet' else {})} for _ in units]


class Agent:
    def __init__(self, patch=None, reject_acceptance=False):
        self.calls, self.patch, self.reject = [], patch, reject_acceptance

    def call(self, role, payload, schema):
        self.calls.append((role, payload))
        if role == 'revise':
            return {'translations': self.patch or payload['translations']}
        if role == 'accept' and self.reject:
            s = payload['sentences'][0]
            return {'passed': False, 'issues': [{'id': s['id'], 'severity': 'blocking',
                'evidence': s['source'].strip(), 'reason': '未解决的问题', 'suggestion': '重新核对原文'}]}
        return {'passed': True, 'issues': []}


class FixTests(unittest.TestCase):
    def test_acceptance_error_returns_to_translation_and_evaluation(self):
        b = make_block('The method may work. We use a dataset.', ['该方法一定有效。', '我们使用一个数据集。'])
        class Expert(Agent):
            def call(self, role, payload, schema):
                self.calls.append((role, payload))
                if role == 'accept' and any('一定' in t['text'] for t in payload['translations']):
                    return {'passed': False, 'issues': [{'id': 'b_001', 'severity': 'blocking',
                        'evidence': 'may', 'reason': '“一定”将可能性改为确定性',
                        'suggestion': '改为“该方法可能有效。”'}]}
                if role == 'revise':
                    return {'translations': [{'id': 'b_001', 'text': '该方法可能有效。'}]}
                return {'passed': True, 'issues': []}
        agent = Expert()
        result = quality.evaluate_document(agent, [b], [], Backend())
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual([r for r, _ in agent.calls], ['evaluate', 'accept', 'revise', 'evaluate', 'accept'])
        repair = agent.calls[2][1]
        self.assertEqual([s['id'] for s in repair['sentences']], ['b_001'])
        self.assertIn('may', str(repair['issues']))
        self.assertEqual(result['translations']['b_002'], '我们使用一个数据集。')

    def test_acceptance_warning_does_not_trigger_revision(self):
        b = make_block('The method works.', ['该方法有效。'])
        class Expert(Agent):
            def call(self, role, payload, schema):
                self.calls.append((role, payload))
                return {'passed': True, 'issues': ([{'id': 'b_001', 'severity': 'warning',
                    'evidence': 'works', 'reason': '上下文不足以确认术语问题',
                    'suggestion': '保留现译'}] if role == 'accept' else [])}
        agent = Expert()
        result = quality.evaluate_document(agent, [b], [], Backend())
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual([r for r, _ in agent.calls], ['evaluate', 'accept'])

    def test_wrong_number_targets_second_sentence_only(self):
        b = make_block('The baseline uses 15 ms. The method uses 25 ms.',
                       ['基线使用 15 毫秒。', '该方法使用 50 毫秒。'])
        agent = Agent(patch=[{'id': 'b_002', 'text': '该方法使用 25 毫秒。'}])
        result = quality.evaluate_document(agent, [b], [], Backend())
        self.assertEqual(result['status'], 'accepted')
        patch_call = next(payload for role, payload in agent.calls if role == 'revise')
        self.assertEqual([s['id'] for s in patch_call['sentences']], ['b_002'])
        self.assertEqual(result['translations']['b_001'], '基线使用 15 毫秒。')

    def test_decimal_error_targets_all_related_fragments(self):
        b = make_block('The result is 0.5.', ['结果为 0.', '6。'])
        findings = quality.paper_checks([b], b['translations'])
        self.assertEqual({f['id'] for f in findings}, {'b_001', 'b_002'})

    def test_acceptance_does_not_receive_reviewer_scores_or_findings(self):
        b = make_block('The method works.', ['该方法有效。'])
        agent = Agent()
        result = quality.evaluate_document(agent, [b], [], Backend())
        self.assertEqual([role for role, _ in agent.calls], ['evaluate', 'accept'])
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(len(agent.calls[-1][1]['document']['blocks']), 1)
        for key in ('xcomet_findings', 'paper_findings', 'issues', 'passed', 'scores'):
            self.assertNotIn(key, agent.calls[-1][1])

    def test_high_scores_do_not_override_rejected_acceptance(self):
        b = make_block('The method works.', ['该方法有效。'])
        result = quality.evaluate_document(Agent(reject_acceptance=True), [b], [], Backend())
        self.assertEqual(result['status'], 'needs_review')
        self.assertIn('循环', result['reason'])


if __name__ == '__main__':
    unittest.main()
