from string import Template
import unittest
from unittest.mock import patch

from readmaster.agents import InvalidApiOutput, validate_schema
from readmaster.babeldoc_agent import BabelDOCTranslationAgent
from readmaster.pipeline import translation_schema, review_schema


class BabelAgentTests(unittest.TestCase):
    def candidate(self, inner):
        with patch('readmaster.babeldoc_agent.load_template', return_value=Template('$json_input_str\n$contextual_hints_block\n$glossary_tables_block')):
            return BabelDOCTranslationAgent(inner, '.')

    def test_review_roles_are_delegated_and_revisions_keep_feedback_and_ids(self):
        class Model:
            requests = []
            def call(self, role, payload, schema):
                self.requests.append((role, payload))
                if role == 'babeldoc_translate':
                    value = {'items': [{'id': 0, 'output': '它可能有帮助。'}]}
                    validate_schema(value, schema)
                    return value
                return {'passed': True, 'issues': []}
        inner = Model()
        agent = self.candidate(inner)
        source = [{'id': 'p003_b012_001', 'source': 'It may help.'}]
        issues = [{'id': source[0]['id'], 'evidence': 'may', 'reason': '不得删除不确定性',
                   'suggestion': '使用可能', 'severity': 'blocking'}]
        payload = {'sentences': source, 'issues': issues, 'context': '科研论文',
                   'terms': [{'source': 'help', 'target': '有帮助'}]}
        result = agent.call('revise', payload, translation_schema())
        self.assertEqual(result['translations'][0]['id'], source[0]['id'])
        request = inner.requests[0][1]
        self.assertEqual(request['reader_id_map'], {'0': 'p003_b012_001'})
        self.assertEqual(request['reader_data']['issues'], issues)
        self.assertIn('help → 有帮助', request['native_prompt'])
        self.assertEqual(agent.call('accept', payload, review_schema()), {'passed': True, 'issues': []})
        self.assertEqual(inner.requests[-1], ('accept', payload))

    def test_invalid_native_mapping_enters_existing_bounded_repair(self):
        class Model:
            def call(self, role, payload, schema):
                return {'items': [{'id': 999, 'output': '错误对应。'}]}
        agent = self.candidate(Model())
        with self.assertRaises(InvalidApiOutput):
            agent.call('translate', {'sentences': [{'id': 's1', 'source': 'One.'}]}, translation_schema())

    def test_native_integer_schema_rejects_boolean_id(self):
        with self.assertRaises(ValueError):
            validate_schema(True, {'type': 'integer'})


if __name__ == '__main__':
    unittest.main()
