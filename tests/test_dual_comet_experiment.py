import copy
import unittest
from unittest.mock import patch
from readmaster.agents import CodexAgent
from scripts.compare_dual_comet_paper import CompactCodexAgent


class CompactReviewTests(unittest.TestCase):
    def test_compaction_keeps_every_source_translation_and_numeric_evidence(self):
        payload={'sentences':[{'id':'s','source':'At 103.'}],
                 'translations':[{'id':'s','text':'在 10^3 时。'}],
                 'document':{'blocks':[
                     {'id':'b','preserved':False,'source':'At 103.','verified_numeric_source':'At 10^3.',
                      'translation':'在 10^3 时。','sentences':[{'id':'s','source':'At 103.','translation':'在 10^3 时。'}]},
                     {'id':'table','preserved':True,'source':'Table 42','translation':'Table 42','sentences':[]}]}}
        before=copy.deepcopy(payload)
        agent=object.__new__(CompactCodexAgent)
        with patch.object(CodexAgent,'call',return_value={}) as call:
            agent.call('evaluate',payload,{})
            sent=call.call_args.args[1]
        self.assertEqual(payload,before)
        self.assertEqual(sent['sentences'],before['sentences'])
        self.assertEqual(sent['document']['blocks'][0]['sentences'],before['document']['blocks'][0]['sentences'])
        self.assertEqual(sent['document']['blocks'][0]['verified_numeric_source'],'At 10^3.')
        self.assertEqual(sent['document']['blocks'][1]['source'],'Table 42')
        self.assertNotIn('translations',sent)

    def test_babeldoc_revision_preserves_native_prompt_and_id_mapping(self):
        payload={'native_prompt':'Translate requested ids','reader_id_map':{'0':'s'},
                 'reader_data':{'document':{'blocks':[]},'translations':[], 'sentences':[]}}
        agent=object.__new__(CompactCodexAgent)
        with patch.object(CodexAgent,'call',return_value={}) as call:
            agent.call('babeldoc_translate',payload,{})
            sent=call.call_args.args[1]
        self.assertEqual(sent['native_prompt'],payload['native_prompt'])
        self.assertEqual(sent['reader_id_map'],payload['reader_id_map'])
        self.assertIn('context_representation',sent['reader_data'])


if __name__=='__main__':
    unittest.main()
