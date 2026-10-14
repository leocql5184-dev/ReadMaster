import json
from pathlib import Path
import tempfile
import unittest
import threading
import urllib.request
from unittest.mock import patch

from readmaster.models import ModelStore, connection


class ModelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = ModelStore(self.folder)

    def profile(self, key='fake-secret-models-12345', **changes):
        return dict(name='论文模型', provider='api', model='test-model',
                    endpoint='https://api.example.com/v1', api_key=key, **changes)

    def test_encrypted_restart_and_public_mask(self):
        body = self.profile()
        ident = self.store.save(body)
        self.store.activate(ident)
        self.assertNotIn(body['api_key'], self.store.path.read_text(encoding='utf-8'))
        self.assertNotIn(body['api_key'], json.dumps(self.store.public()))
        restarted = ModelStore(self.folder)
        config, key = restarted.snapshot()
        self.assertEqual(key, body['api_key'])
        self.assertEqual(config['model'], 'test-model')
        self.assertTrue(restarted.public()['onboarded'])

    def test_blank_preserves_and_new_key_replaces(self):
        ident = self.store.save(self.profile())
        self.store.save({**self.profile(''), 'id': ident, 'model': 'next-model'})
        self.assertEqual(self.store.snapshot(ident)[1], 'fake-secret-models-12345')
        self.store.save({**self.profile('replacement-secret-23456'), 'id': ident})
        self.assertEqual(self.store.snapshot(ident)[1], 'replacement-secret-23456')

    def test_switch_delete_and_snapshot_isolation(self):
        first = self.store.save(self.profile())
        frozen_config, frozen_key = self.store.activate(first)
        second = self.store.save({**self.profile('second-secret-34567'), 'name': '第二模型', 'model': 'second'})
        self.store.activate(second)
        self.assertEqual(frozen_key, 'fake-secret-models-12345')
        self.assertEqual(frozen_config['model'], 'test-model')
        self.store.delete(second)
        self.assertIsNone(self.store.public()['active_id'])
        self.assertEqual(len(self.store.public()['profiles']), 2)
        with self.assertRaises(ValueError):
            self.store.delete('codex')

    def test_fresh_onboarding_skip_and_legacy(self):
        self.assertFalse(self.store.public()['onboarded'])
        self.store.skip_onboarding()
        self.assertTrue(ModelStore(self.folder).public()['onboarded'])
        with tempfile.TemporaryDirectory() as folder:
            migrated = ModelStore(folder, existing_library=True)
            self.assertEqual(migrated.public()['active_id'], 'codex')
            self.assertTrue(migrated.public()['onboarded'])

    def test_validation_and_url_normalization(self):
        for endpoint in ('http://example.com/v1', 'https://key@example.com/v1', 'https://example.com/v1?key=secret'):
            with self.assertRaises(ValueError):
                connection({**self.profile(), 'endpoint': endpoint})
        with self.assertRaises(ValueError):
            self.store.save(self.profile(''))
        with self.assertRaises(ValueError):
            self.store.save(self.profile('bad\nkey'))
        self.assertEqual(connection({**self.profile(), 'endpoint': 'https://api.example.com/v1/chat/completions'})['endpoint'], 'https://api.example.com/v1')


class ModelHttpTests(unittest.TestCase):
    def test_saved_profiles_and_probe_through_http(self):
        from readmaster import server
        with patch.object(server.logging, 'basicConfig'), patch.object(server, 'MODELS'), tempfile.TemporaryDirectory() as folder, patch.object(server, 'DATA', Path(folder)), patch.object(server, 'DOCS', {}), patch.object(server, 'CONFIG', dict(server.CONFIG)), patch.object(server, 'KEY', ''):
            http = server.create_server(0)
            worker = threading.Thread(target=http.serve_forever)
            worker.start()
            base = f'http://127.0.0.1:{http.server_port}'
            def request(path, body=None):
                raw = json.dumps(body).encode() if body is not None else None
                req = urllib.request.Request(base+path, raw, {'X-ReadMaster': '1', 'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.load(response)
            try:
                self.assertFalse(request('/api/models')['onboarded'])
                body = {'name': 'HTTP 测试', 'provider': 'api', 'model': 'test', 'endpoint': 'https://api.example.com/v1', 'api_key': 'http-fake-secret-key'}
                saved = request('/api/models/save', body)
                request('/api/models/activate', {'id': saved['id']})
                self.assertTrue(request('/api/settings')['has_key'])
                with patch.object(server.ApiAgent, 'call', return_value={'ok': True}) as call:
                    result = request('/api/models/test', {**body, 'id': saved['id'], 'api_key': ''})
                    self.assertIn('连接成功', result['message'])
                    self.assertEqual(call.call_args.args[0], 'probe')
                self.assertNotIn(body['api_key'], json.dumps(request('/api/models')))
                request('/api/models/delete', {'id': saved['id']})
                self.assertIsNone(request('/api/models')['active_id'])
            finally:
                http.shutdown()
                worker.join()
                http.server_close()


if __name__ == '__main__':
    unittest.main()
