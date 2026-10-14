import io
import json
import ssl
import tempfile
import unittest
import urllib.error
from unittest.mock import patch, MagicMock
from readmaster.agents import ApiAgent, OutputLimitError
from readmaster.server import CachedAgent
from readmaster.pipeline import run_batch, split_periods, validate_translations


def response(value):
    return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]}).encode())


class RecoveryTests(unittest.TestCase):
    def test_decimal_fragments_stay_joined_and_revise_together(self):
        sentences = split_periods('At severity 0.3.', 'p001_b001')
        ids = [s['id'] for s in sentences]
        good = {'translations': [{'id': ids[0], 'text': '在严重程度 0.'}, {'id': ids[1], 'text': '3 时。'}]}
        self.assertEqual(''.join(validate_translations(good, sentences).values()), '在严重程度 0.3 时。')
        bad = {'translations': [{'id': ids[0], 'text': '在严重程度 0。'}, {'id': ids[1], 'text': '3 时。'}]}
        with self.assertRaisesRegex(ValueError, '小数衔接'):
            validate_translations(bad, sentences)
        revised = []
        class Agent:
            def call(self, role, payload, schema):
                if role in ('translate', 'revise'):
                    if role == 'revise':
                        revised.extend(s['id'] for s in payload['sentences'])
                    return good
                if role == 'evaluate' and not revised:
                    return {'passed': False, 'issues': [{'id': ids[1], 'severity': 'blocking', 'evidence': '3.', 'reason': 'test issue', 'suggestion': 'test repair'}]}
                return {'passed': True, 'issues': []}
        run_batch(Agent(), sentences, 'context', [])
        self.assertEqual(revised, ids)

    def test_tls_read_failure_reconnects_direct_with_normal_tls(self):
        broken = MagicMock()
        broken.__enter__.return_value = broken
        broken.read.side_effect = ssl.SSLError('DECRYPTION_FAILED_OR_BAD_RECORD_MAC')
        opener = MagicMock()
        opener.open.return_value = response({'ok': True})
        notices = []
        agent = ApiAgent({'endpoint': 'https://api.deepseek.com/v1', 'model': 'deepseek-flash'}, 'dummy', notify=notices.append)
        with patch('readmaster.agents.urllib.request.urlopen', return_value=broken), patch('readmaster.agents.urllib.request.build_opener', return_value=opener) as direct, patch('readmaster.agents.time.sleep'):
            self.assertEqual(agent.call('probe', {}, {}), {'ok': True})
        self.assertTrue(agent.direct)
        self.assertTrue(notices)
        self.assertEqual(direct.call_count, 1)
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 900)

    def test_certificate_failure_is_not_retried(self):
        agent = ApiAgent({'endpoint': 'https://api.deepseek.com/v1'}, 'dummy')
        with patch('readmaster.agents.urllib.request.urlopen', side_effect=ssl.SSLCertVerificationError('bad cert')) as call:
            with self.assertRaisesRegex(RuntimeError, '证书'):
                agent.call('probe', {}, {})
        self.assertEqual(call.call_count, 1)

    def test_retries_are_bounded_and_can_be_paused(self):
        agent = ApiAgent({'endpoint': 'https://other.example/v1', 'retry_attempts': 3}, 'dummy')
        with patch('readmaster.agents.urllib.request.urlopen', side_effect=ConnectionResetError), patch('readmaster.agents.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, '自动重连未成功'):
                agent.call('probe', {}, {})
        cancelled = MagicMock(side_effect=[False, True])
        agent = ApiAgent({'endpoint': 'https://other.example/v1'}, 'dummy', cancelled=cancelled)
        with patch('readmaster.agents.urllib.request.urlopen', side_effect=ConnectionResetError):
            with self.assertRaises(InterruptedError):
                agent.call('probe', {}, {})

    def test_decorative_fields_removed_but_missing_data_still_rejected(self):
        from readmaster.agents import InvalidApiOutput
        from readmaster.pipeline import translation_schema
        inner = MagicMock()
        inner.call.side_effect = [InvalidApiOutput('JSON'), {'translations': [{'id': 's1', 'text': '译文', 'comment': 'extra'}], 'reasoning': 'extra'}]
        with tempfile.TemporaryDirectory() as folder:
            agent = CachedAgent(inner, folder, {'provider': 'api', 'model': 'm'})
            result = agent.call('translate', {'sentences': [{'id': 's1', 'source': 'Text.'}]}, translation_schema())
            self.assertEqual(result, {'translations': [{'id': 's1', 'text': '译文'}]})
            inner.call.return_value = {'translations': []}
            inner.call.side_effect = None
            with self.assertRaises(ValueError):
                agent.call('translate', {'sentences': [{'id': 's2', 'source': 'More text.'}]}, translation_schema())

    def test_output_limit_splits_requests_without_losing_sentence_ids(self):
        class Agent:
            def call(self, role, payload, schema):
                if role == 'translate':
                    if len(payload['sentences']) > 1:
                        raise OutputLimitError('length')
                    return {'translations': [{'id': s['id'], 'text': '译文。'} for s in payload['sentences']]}
                return {'passed': True, 'issues': []}
        result = run_batch(Agent(), [{'id': 'a', 'source': 'First.'}, {'id': 'b', 'source': 'Second.'}], 'context', [])
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(set(result['translations']), {'a', 'b'})


if __name__ == '__main__':
    unittest.main()
