import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from readmaster.quality import CometBackend
from scripts.xcomet_quantized import FORMAT, KIWI_FORMAT, read_manifest
from scripts import comet_worker


class QuantizedBundleTests(unittest.TestCase):
    def test_kiwi_verification_override_cuda_and_corrupt_marker(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            manifest = root/'data/comet-models/cometkiwi-int8/manifest.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{}')
            marker = manifest.with_name('verified.json')
            def selected():
                return CometBackend(root,root/'cache').models['kiwi']
            self.assertEqual(selected(),'Unbabel/wmt22-cometkiwi-da')
            marker.write_text('{')
            self.assertEqual(selected(),'Unbabel/wmt22-cometkiwi-da')
            marker.write_text('[]')
            self.assertEqual(selected(),'Unbabel/wmt22-cometkiwi-da')
            evidence = dict(passed=True,manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
            marker.write_text(json.dumps(evidence))
            self.assertEqual(selected(),str(manifest))
            with patch.dict(os.environ,{'READMASTER_KIWI_MODEL':'Unbabel/wmt22-cometkiwi-da'}):
                self.assertEqual(selected(),'Unbabel/wmt22-cometkiwi-da')
            with patch.dict(os.environ,{'READMASTER_COMET_DEVICE':'cuda'}):
                self.assertEqual(selected(),'Unbabel/wmt22-cometkiwi-da')
            manifest.write_text('{"changed":1}')
            self.assertEqual(selected(),'Unbabel/wmt22-cometkiwi-da')

    def test_kiwi_manifest_has_own_format(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'weights.pt').write_bytes(b'fake')
            manifest = root/'manifest.json'
            manifest.write_text(json.dumps({'format':KIWI_FORMAT,'files':{'weights.pt':'hash'}}))
            self.assertEqual(read_manifest(manifest)['format'],KIWI_FORMAT)

    def test_kiwi_fp32_and_int8_prediction_caches_are_separate(self):
        from readmaster.comet_service import WORKER
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            python = root/'.venv-comet/Scripts/python.exe'
            python.parent.mkdir(parents=True)
            python.touch()
            manifest = root/'manifest.json'
            manifest.write_text('{}')
            units = [{'src':'The value is 10.','mt':'数值为 10。'}]
            original = CometBackend(root,root/'cache')
            with patch.object(WORKER,'invoke',return_value={'results':[{'score':.71}]}) as invoke:
                self.assertEqual(original.predict('kiwi',units)[0]['score'],.71)
                self.assertEqual(original.predict('kiwi',units)[0]['score'],.71)
                self.assertEqual(invoke.call_count,1)
            with patch.dict(os.environ,{'READMASTER_KIWI_MODEL':str(manifest)}):
                quantized = CometBackend(root,root/'cache')
            with patch.object(WORKER,'invoke',return_value={'results':[{'score':.69}]}) as invoke:
                self.assertEqual(quantized.predict('kiwi',units)[0]['score'],.69)
                self.assertEqual(quantized.predict('kiwi',units)[0]['score'],.69)
                self.assertEqual(invoke.call_count,1)

    def test_quantized_threads_restore_even_after_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest=Path(folder)/'manifest.json'
            manifest.write_text('{}')
            settings=[]
            fake=SimpleNamespace(get_num_threads=lambda:24,set_num_threads=settings.append)
            with patch.dict('sys.modules',{'torch':fake}), patch.object(comet_worker,'_run',side_effect=ValueError('bad bundle')):
                with self.assertRaisesRegex(ValueError,'bad bundle'):
                    comet_worker.run({'model':str(manifest)})
            self.assertEqual(settings,[6,24])

    def test_only_verified_bundle_activates_and_respects_override_and_cuda(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            manifest = root/'data/comet-models/xcomet-xl-int8/manifest.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{}')
            def selected():
                return CometBackend(root,root/'cache').models['xcomet']
            self.assertEqual(selected(),'Unbabel/XCOMET-XL')
            marker = manifest.with_name('verified.json')
            marker.write_text(json.dumps({'passed':True,'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()}))
            self.assertEqual(selected(),str(manifest))
            with patch.dict(os.environ,{'READMASTER_COMET_DEVICE':'cuda'}):
                self.assertEqual(selected(),'Unbabel/XCOMET-XL')
            with patch.dict(os.environ,{'READMASTER_XCOMET_MODEL':'Unbabel/XCOMET-XL'}):
                self.assertEqual(selected(),'Unbabel/XCOMET-XL')
            manifest.write_text('{"changed":true}')
            self.assertEqual(selected(),'Unbabel/XCOMET-XL')

    def test_manifest_revision_changes_with_content(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root=Path(folder)
            manifest=root/'model.json'
            manifest.write_text('{"v":1}')
            with patch.dict(os.environ,{'READMASTER_XCOMET_MODEL':str(manifest)}):
                before=CometBackend(root,root/'cache').revisions['xcomet']
                manifest.write_text('{"v":2}')
                after=CometBackend(root,root/'cache').revisions['xcomet']
            self.assertNotEqual(before,after)

    def test_bundle_paths_cannot_escape_and_weight_checksum_required(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            manifest=root/'manifest.json'
            manifest.write_text(json.dumps({'format':FORMAT,'files':{}}))
            with self.assertRaisesRegex(ValueError,'checksum'):
                read_manifest(manifest)
            (root/'weights.pt').write_bytes(b'fake')
            manifest.write_text(json.dumps({'format':FORMAT,'files':{'weights.pt':'hash','../escape':'hash'}}))
            with self.assertRaisesRegex(ValueError,'Invalid or missing'):
                read_manifest(manifest)


if __name__=='__main__':
    unittest.main()
