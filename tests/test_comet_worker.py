from types import SimpleNamespace
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
from scripts import comet_worker

from scripts.comet_worker import validate_lengths, load_with_cached_assets, decode_complete_spans


class Tokenizer:
    def encode(self, text, **kwargs):
        return list(text)


def model(kind):
    return SimpleNamespace(encoder=SimpleNamespace(max_positions=512,tokenizer=Tokenizer()),
                           hparams=SimpleNamespace(class_identifier=kind))


class CometInputTests(unittest.TestCase):
    def test_resident_weights_reused_and_low_memory_evicts(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'a.ckpt', Path(directory)/'b.ckpt'
            a.write_bytes(b'a'); b.write_bytes(b'b')
            fake = SimpleNamespace(hparams=SimpleNamespace(class_identifier='qe'),
                eval=lambda: None, predict=lambda *a, **k: SimpleNamespace(scores=[.8]))
            torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
            hub = SimpleNamespace(snapshot_download=None, try_to_load_from_cache=None)
            request = {'model': str(a), 'cache_root': directory, 'data': [{'src':'a','mt':'甲'}]}
            with patch.dict('sys.modules', {'torch': torch, 'huggingface_hub': hub}), \
                 patch.object(comet_worker, '_MODELS', {}), \
                 patch.object(comet_worker, 'available_memory', return_value=0), \
                 patch.object(comet_worker, 'validate_lengths'), \
                 patch.object(comet_worker, 'load_checkpoint_mapped', return_value=fake) as loader:
                comet_worker.run(request)
                comet_worker.run(request)
                self.assertEqual(loader.call_count, 1)
                comet_worker.run({**request, 'model': str(b)})
                self.assertEqual(loader.call_count, 2)
                self.assertEqual(list(comet_worker._MODELS), [(str(b), 'cpu')])

    def test_terminal_error_survives_special_token_offsets(self):
        m=SimpleNamespace(decoding_threshold=None,
            label_encoder=SimpleNamespace(ids_to_label={0:'O',1:'I-critical'}),
            encoder=SimpleNamespace(tokenizer=SimpleNamespace(decode=lambda tokens:'错误译文')))
        tensor=lambda data:SimpleNamespace(tolist=lambda:data)
        spans=decode_complete_spans(m,tensor([[[.01,.99]]*4]),tensor([[0,1,2,3]]),[[(0,0),(0,1),(1,4),(0,0)]])[0]
        self.assertEqual(len(spans),1)
        self.assertEqual((spans[0]['start'],spans[0]['end']),(0,4))
        self.assertEqual(spans[0]['severity'],'critical')
        self.assertAlmostEqual(spans[0]['confidence'],.99)

    def test_correct_tokens_do_not_create_errors(self):
        m=SimpleNamespace(decoding_threshold=None,
            label_encoder=SimpleNamespace(ids_to_label={0:'O',1:'I-major'}),
            encoder=SimpleNamespace(tokenizer=SimpleNamespace(decode=lambda tokens:'')))
        tensor=lambda data:SimpleNamespace(tolist=lambda:data)
        self.assertEqual(decode_complete_spans(m,tensor([[[.99,.01]]]),tensor([[1]]),[[(0,3)]]),[[]])

    def test_missing_local_vocab_retries_official_online_loader(self):
        calls=[]
        def loader(checkpoint, local_files_only):
            calls.append(local_files_only)
            if local_files_only:
                raise AttributeError("'NoneType' object has no attribute 'endswith'")
            return 'loaded'
        self.assertEqual(load_with_cached_assets(loader, 'official.ckpt'), 'loaded')
        self.assertEqual(calls, [True, False])

    def test_unrelated_loading_bug_is_not_retried_as_a_cache_miss(self):
        calls=[]
        def loader(checkpoint, local_files_only):
            calls.append(local_files_only)
            raise AttributeError('model has no attribute encoder')
        with self.assertRaises(AttributeError):
            load_with_cached_assets(loader, 'official.ckpt')
        self.assertEqual(calls, [True])

    def test_regression_does_not_reject_valid_separately_encoded_inputs(self):
        validate_lengths(model('referenceless_regression_metric'), [{'src':'a'*400,'mt':'b'*400}])

    def test_xcomet_cannot_silently_truncate_combined_inputs(self):
        with self.assertRaisesRegex(ValueError, '^length:0$'):
            validate_lengths(model('xcomet_metric'), [{'src':'a'*400,'mt':'b'*400}])

    def test_pinned_kiwi_unified_inputs_use_combined_limit(self):
        with self.assertRaisesRegex(ValueError, '^length:0$'):
            validate_lengths(model('unified_metric'), [{'src':'a'*400,'mt':'b'*400}])

    def test_source_and_translation_individual_limits_are_checked(self):
        for key in ('src','mt'):
            row={'src':'a','mt':'b'}
            row[key]='x'*509
            with self.assertRaises(ValueError):
                validate_lengths(model('referenceless_regression_metric'), [row])


if __name__ == '__main__':
    unittest.main()
