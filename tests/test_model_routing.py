import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import launcher
from readmaster.model_routing import MODEL_BUNDLES, ORIGINAL_MODELS, select_models


class ModelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        env = patch.dict(os.environ, {'READMASTER_COMET_MODE': 'quantized'}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def bundle(self, kind):
        folder, format_name = MODEL_BUNDLES[kind]
        path = self.root / 'data/comet-models' / folder / 'manifest.json'
        path.parent.mkdir(parents=True)
        weights = path.with_name('weights.pt')
        weights.write_bytes(b'test fixture')
        path.write_text(json.dumps({'format': format_name, 'files': {
            'weights.pt': hashlib.sha256(weights.read_bytes()).hexdigest()}}))
        path.with_name('verified.json').write_text(json.dumps({
            'passed': True, 'manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
        return path

    def test_both_verified_quantized_models_are_selected(self):
        expected = {kind: str(self.bundle(kind).resolve()) for kind in MODEL_BUNDLES}
        self.assertEqual(select_models(self.root), expected)

    def test_missing_or_stale_bundle_does_not_fall_back(self):
        with self.assertRaisesRegex(ValueError, '不会自动下载'):
            select_models(self.root)
        kiwi = self.bundle('kiwi')
        self.bundle('xcomet')
        kiwi.with_name('weights.pt').unlink()
        with self.assertRaisesRegex(ValueError, 'kiwi'):
            select_models(self.root)

    def test_wrong_model_format_is_rejected_even_with_matching_marker(self):
        kiwi = self.bundle('kiwi')
        self.bundle('xcomet')
        os.environ['READMASTER_XCOMET_MODEL'] = str(kiwi)
        with self.assertRaisesRegex(ValueError, 'xcomet'):
            select_models(self.root)

    def test_fp32_is_explicit_and_skips_local_bundles(self):
        self.bundle('kiwi')
        self.bundle('xcomet')
        os.environ['READMASTER_COMET_MODE'] = 'fp32'
        self.assertEqual(select_models(self.root), ORIGINAL_MODELS)

    def test_quantized_mode_rejects_cuda_and_original_override(self):
        os.environ['READMASTER_COMET_DEVICE'] = 'cuda'
        with self.assertRaisesRegex(ValueError, 'CPU'):
            select_models(self.root)
        os.environ['READMASTER_COMET_DEVICE'] = 'cpu'
        os.environ['READMASTER_KIWI_MODEL'] = ORIGINAL_MODELS['kiwi']
        with self.assertRaisesRegex(ValueError, 'kiwi'):
            select_models(self.root)

    def test_launcher_defaults_quantized_and_preserves_desktop_arguments(self):
        os.environ.pop('READMASTER_COMET_MODE')
        with patch('sys.argv', ['launcher.py', '--port', '7999']), patch('readmaster.desktop.main') as run:
            launcher.main()
            run.assert_called_once()
            self.assertEqual(os.environ['READMASTER_COMET_MODE'], 'quantized')
            self.assertEqual(launcher.sys.argv, ['launcher.py', '--port', '7999'])

    def test_launcher_explicit_mode_overrides_environment_for_browser(self):
        with patch('sys.argv', ['launcher.py', '--browser', '--comet-mode', 'fp32', '--port', '7999']), patch('readmaster.server.main') as run:
            launcher.main()
            run.assert_called_once()
            self.assertEqual(os.environ['READMASTER_COMET_MODE'], 'fp32')
            self.assertEqual(launcher.sys.argv, ['launcher.py', '--port', '7999'])


if __name__ == '__main__':
    unittest.main()
