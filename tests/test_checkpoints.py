import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from readmaster.checkpoints import paragraph_batches
from readmaster import progress, server


def block(ident, size, page=1):
    source='a'*(size-1)+'.'
    return {'id':ident,'source':source,'page':page,'sentences':[{'id':ident+'_001','source':source}],
            'status':'pending','translations':{}}


class CheckpointTests(unittest.TestCase):
    def test_threshold_waits_for_paragraph_and_resets(self):
        blocks=[block('a',1800),block('b',1700),block('c',2000),block('d',1200),block('e',50)]
        groups=list(paragraph_batches(blocks))
        self.assertEqual([[b['id'] for b in g] for g in groups],[['a','b'],['c','d'],['e']])
        self.assertEqual([sum(len(b['source']) for b in g) for g in groups],[3500,3200,50])

    def test_page_boundary_does_not_reset_and_long_paragraph_is_intact(self):
        blocks=[block('a',1500,1),block('b',1500,2),block('c',6000,2)]
        self.assertEqual(list(paragraph_batches(blocks)),[blocks[:2],blocks[2:]])
        self.assertEqual(list(paragraph_batches([])),[])

    def test_zero_threshold_rejected(self):
        with self.assertRaises(ValueError):list(paragraph_batches([],0))

    def run_worker(self, fail=False):
        blocks=[block('a',1800),block('b',1700,2),block('c',400,2)]
        doc={'id':'test','blocks':blocks,'pages':[{'number':1},{'number':2}],
             'terms':[{'source':'a','target':'甲','definition':'test'}],'events':[],
             'state':'processing','progress':{'page':1},'error':'','numeric_layout_version':1}
        doc['task']=progress.begin(blocks,1,2,'test')
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)/'documents/test';directory.mkdir(parents=True)
            calls=[]
            def batch(agent,sentences,*args,**kwargs):
                calls.append(sentences)
                if sentences[0]['id']=='c_001':
                    if fail:raise RuntimeError('simulated offline')
                return {'translations':{s['id']:'译文' for s in sentences},'status':'needs_review',
                        'history':[],'issues':[],'revisions':0,'reason':'test'}
            def quality(agent, all_blocks, terms, backend, **kwargs):
                self.assertEqual(len(all_blocks), 3)
                self.assertTrue(all(b['translations'] for b in all_blocks))
                result = {'translations': {s['id']: b['translations'][s['id']] for b in all_blocks for s in b['sentences']},
                          'status': 'accepted', 'issues': [], 'revisions': 0, 'reason': 'test quality', 'history': [],
                          'scores': {'a_001': .9}, 'version': 'comet-document-v1', 'threshold': .8}
                kwargs['checkpoint'](result['translations'], result)
                return result
            with patch.object(server,'DATA' ,Path(temp)),patch.object(server,'DOCS',{'test':doc}),\
                 patch.object(server,'STOPS',{'test':threading.Event()}),\
                 patch.object(server,'CodexAgent',return_value=object()),\
                 patch.object(server,'codex_binary',return_value='unused'),\
                 patch.object(server,'translate_batch',side_effect=batch),\
                 patch.object(server.CometBackend,'predict',return_value=[]),\
                 patch.object(server,'evaluate_document',side_effect=quality):
                server.process_document('test',1,2)
            # Reload from disk, without any model or process-local translation state.
            saved=json.loads((directory/'document.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['blocks'][0]['translations'],{'a_001':'译文'})
            self.assertEqual(saved['cache_policy']['source_chars'],3000)
            return copy.deepcopy(saved)

    def test_incremental_save_and_final_tail_survive_reload(self):
        saved=self.run_worker()
        self.assertEqual(saved['state'],'complete')
        self.assertEqual(sorted(c['source_chars'] for c in saved['translation_checkpoints']),[400,3500])
        self.assertTrue(all(b['translations'] for b in saved['blocks']))

    def test_later_failure_preserves_previous_checkpoint(self):
        with self.assertLogs(level='ERROR'):saved=self.run_worker(fail=True)
        self.assertEqual(saved['state'],'failed')
        self.assertEqual(len(saved['translation_checkpoints']),1)
        self.assertEqual(saved['blocks'][2]['status'],'pending')
        self.assertEqual(saved['blocks'][2]['translations'],{})
