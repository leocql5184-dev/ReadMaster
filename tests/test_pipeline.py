import unittest
from readmaster.pipeline import split_periods, run_batch, validate_translations, validate_review


class ScriptedAgent:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def call(self, role, payload, schema):
        self.calls.append((role, payload))
        expected_role, result = next(self.replies)
        assert role == expected_role, (role, expected_role)
        return result


def translated(text):
    return {"translations": [{"id": "s_001", "text": text}]}


PASS = {"passed": True, "issues": []}
FAIL = {"passed": False, "issues": [{"id": "s_001", "severity": "blocking", "evidence": "may",
                                     "reason": "丢失不确定语气", "suggestion": "保留可能"}]}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.sentences = split_periods("It may help.")

    def test_literal_periods_keep_every_character(self):
        text = ' Fig. 3.14 is useful.\nNext part without period  '
        blocks = split_periods(text)
        self.assertEqual(''.join(b['source'] for b in blocks), text)
        self.assertEqual(len(blocks), 4)
        for b in blocks:
            self.assertEqual(text[b['start']:b['end']], b['source'])

    def test_unicode_offsets_and_no_period_tail(self):
        text = '😀 Test. 中文尾巴'
        pieces = split_periods(text)
        self.assertEqual(pieces[0]['end'], 7)
        self.assertEqual(''.join(p['source'] for p in pieces), text)

    def test_duplicate_missing_and_unknown_ids_rejected(self):
        for entries in [[], [{'id':'wrong','text':'译文'}], [{'id':'s_001','text':'甲'},{'id':'s_001','text':'乙'}]]:
            with self.assertRaises(ValueError):
                validate_translations({'translations':entries}, self.sentences)

    def test_invented_review_evidence_rejected(self):
        invalid = {"passed":False,"issues":[{**FAIL['issues'][0],"evidence":"must"}]}
        with self.assertRaises(ValueError):
            validate_review(invalid,self.sentences)

    def test_inconsistent_pass_rejected(self):
        with self.assertRaises(ValueError):
            validate_review({'passed':False,'issues':[]},self.sentences)

    def test_contextual_revision_then_independent_acceptance(self):
        agent=ScriptedAgent([('translate',translated('它有帮助。')),('evaluate',FAIL),('revise',translated('它可能有帮助。')),('evaluate',PASS),('accept',PASS)])
        result=run_batch(agent,self.sentences,'previous paragraph',[],2)
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(result['revisions'],1)
        self.assertIn('context',agent.calls[2][1])
        self.assertIn('translations',agent.calls[2][1])
        self.assertNotIn('passed',agent.calls[-1][1])
        self.assertNotIn('history',agent.calls[-1][1])

    def test_acceptance_rejection_shares_revision_budget(self):
        agent=ScriptedAgent([('translate',translated('它有帮助。')),('evaluate',PASS),('accept',FAIL)])
        result=run_batch(agent,self.sentences,'context',[],0)
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(len(agent.calls),3)

    def test_same_translation_stops_loop(self):
        agent=ScriptedAgent([('translate',translated('它有帮助。')),('evaluate',FAIL),('revise',translated('它有帮助。'))])
        result=run_batch(agent,self.sentences,'context',[],5)
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(result['revisions'],1)

    def test_cancel_before_call(self):
        agent=ScriptedAgent([])
        with self.assertRaises(InterruptedError):
            run_batch(agent,self.sentences,'context',[],2,cancelled=lambda:True)
        self.assertEqual(agent.calls,[])

    def test_only_affected_sentences_revised(self):
        sentences=split_periods('It may help. It works.')
        first={'translations':[{'id':'s_001','text':'有帮助。'},{'id':'s_002','text':'它有效。'}]}
        agent=ScriptedAgent([('translate',first),('evaluate',FAIL),('revise',translated('可能有帮助。')),('evaluate',PASS),('accept',PASS)])
        result=run_batch(agent,sentences,'context',[],2)
        self.assertEqual([s['id'] for s in agent.calls[2][1]['sentences']],['s_001'])
        self.assertEqual(result['translations']['s_002'],'它有效。')


if __name__=='__main__':
    unittest.main()
