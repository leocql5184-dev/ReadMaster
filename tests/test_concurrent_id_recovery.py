import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock
from readmaster.server import CachedAgent
from readmaster.pipeline import translation_schema, run_batch
from readmaster.agents import ResultValidationError


class IdRecoveryTests(unittest.TestCase):
    def test_duplicate_and_foreign_ids_only_retry_ambiguous_missing_entries(self):
        inner = MagicMock()
        inner.call.side_effect = [
            {'translations': [{'id': 'a', 'text': '错误甲'}, {'id': 'a', 'text': '错误乙'},
                              {'id': 'foreign', 'text': '错误'}, {'id': 'b', 'text': '正确乙'}]},
            {'translations': [{'id': 'a', 'text': '正确甲'}, {'id': 'b', 'text': '不应覆盖'}]}]
        payload = {'sentences': [{'id': 'a', 'source': 'A.'}, {'id': 'b', 'source': 'B.'}]}
        with tempfile.TemporaryDirectory() as directory:
            agent = CachedAgent(inner, directory, {'provider': 'api', 'model': 'm'})
            result = agent.call('translate', payload, translation_schema())
            self.assertEqual(result['translations'], [{'id': 'a', 'text': '正确甲'}, {'id': 'b', 'text': '正确乙'}])
            retry = inner.call.call_args_list[1].args[1]
            self.assertEqual(retry['expected_ids'], ['a'])
            self.assertEqual([s['id'] for s in retry['sentences']], ['a'])
            self.assertEqual(len(list(Path(directory).glob('*.json'))), 1)

    def test_two_parallel_documents_same_sentence_ids_remain_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            def task(name):
                inner = MagicMock()
                inner.call.side_effect = [{'translations': []}, {'translations': [{'id': 'same', 'text': name}]}]
                agent = CachedAgent(inner, Path(directory)/name, {'provider': 'api', 'model': 'm'})
                return agent.call('translate', {'sentences': [{'id': 'same', 'source': name}]}, translation_schema())
            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(task, ['论文甲', '论文乙']))
            self.assertEqual([r['translations'][0]['text'] for r in results], ['论文甲', '论文乙'])

    def test_persistent_validation_failure_splits_batch_and_checks_each_piece(self):
        class Agent:
            def call(self, role, payload, schema):
                if len(payload['sentences']) > 1:
                    raise ResultValidationError('编号不匹配')
                if role in ('translate', 'revise'):
                    return {'translations': [{'id': payload['sentences'][0]['id'], 'text': '译文。'}]}
                return {'passed': True, 'issues': []}
        result = run_batch(Agent(), [{'id': 'a', 'source': 'A.'}, {'id': 'b', 'source': 'B.'}], 'context', [])
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(set(result['translations']), {'a', 'b'})


if __name__ == '__main__':
    unittest.main()
