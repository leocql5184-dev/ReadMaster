import threading
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from readmaster.agents import CodexAgent, _CODEX_CALL_LOCK


class CodexRecoveryTests(unittest.TestCase):
    def test_capacity_retry_recovers_and_keeps_failure_log(self):
        notifications = []
        with tempfile.TemporaryDirectory() as folder:
            def run(args, **kwargs):
                if run.count == 0:
                    run.count += 1
                    return subprocess.CompletedProcess(args, 1, '', 'ERROR: Selected model is at capacity. Please try a different model.')
                Path(args[args.index('-o') + 1]).write_text('{"terms":[]}', encoding='utf-8')
                return subprocess.CompletedProcess(args, 0, '', '')
            run.count = 0
            agent = CodexAgent(folder, binary='codex.exe', notify=notifications.append)
            with patch('readmaster.agents.subprocess.run', side_effect=run) as call, patch('readmaster.agents.time.sleep'):
                self.assertEqual(agent.call('terms', {}, {}), {'terms': []})
            self.assertEqual(call.call_count, 2)
            self.assertEqual(len(list(Path(folder).glob('*/error.txt'))), 1)
            self.assertIn('容量不足', notifications[0])
            self.assertIn('10 秒', notifications[0])

    def test_queued_request_can_pause_without_launching_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            paused = [False]
            agent = CodexAgent(folder, binary='codex.exe', cancelled=lambda: paused[0], notify=lambda _: paused.__setitem__(0, True))
            with _CODEX_CALL_LOCK, _CODEX_CALL_LOCK, patch('readmaster.agents.subprocess.run') as call:
                with self.assertRaises(InterruptedError):
                    agent.call('terms', {}, {})
                call.assert_not_called()

    def test_codex_global_limit_is_two(self):
        with tempfile.TemporaryDirectory() as folder:
            entered, release, queued = threading.Event(), threading.Event(), threading.Event()
            results, errors = [], []
            def first_call(*args):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('test timed out')
                return {'terms': []}
            first = CodexAgent(Path(folder)/'first', binary='codex.exe')
            second = CodexAgent(Path(folder)/'second', binary='codex.exe', notify=lambda _: queued.set())
            def worker(agent):
                try:
                    results.append(agent.call('terms', {}, {}))
                except Exception as exc:
                    errors.append(exc)
            # Occupy one of two slots; the running first call takes the other.
            with _CODEX_CALL_LOCK, patch.object(first, '_call', side_effect=first_call), patch.object(second, '_call', return_value={'terms': []}) as call:
                a, b = threading.Thread(target=worker,args=(first,)), threading.Thread(target=worker,args=(second,))
                try:
                    a.start()
                    self.assertTrue(entered.wait(2))
                    b.start()
                    self.assertTrue(queued.wait(2))
                    call.assert_not_called()
                finally:
                    release.set()
                    a.join(3)
                    if b.ident is not None:b.join(3)
                self.assertFalse(errors)
                self.assertEqual(len(results),2)
                call.assert_called_once()

    def test_capacity_retries_bounded_and_user_can_pause(self):
        failure = subprocess.CompletedProcess([], 1, '', 'ERROR: Selected model is at capacity.')
        with tempfile.TemporaryDirectory() as folder:
            agent = CodexAgent(folder, {'retry_attempts': 2}, 'codex.exe')
            with patch('readmaster.agents.subprocess.run', return_value=failure) as call, patch('readmaster.agents.time.sleep'):
                with self.assertRaisesRegex(RuntimeError, '容量不足.*已保存的译文保留'):
                    agent.call('terms', {}, {})
                self.assertEqual(call.call_count, 2)
            paused = [False]
            agent = CodexAgent(folder, binary='codex.exe', cancelled=lambda: paused[0], notify=lambda _: paused.__setitem__(0, True))
            with patch('readmaster.agents.subprocess.run', return_value=failure) as call:
                with self.assertRaises(InterruptedError):
                    agent.call('terms', {}, {})
                self.assertEqual(call.call_count, 1)

    def test_permanent_error_is_not_retried_or_exposed(self):
        with tempfile.TemporaryDirectory() as folder:
            agent = CodexAgent(folder, binary='codex.exe')
            failure = subprocess.CompletedProcess([], 1, '', 'unsupported model; secret-marker')
            with patch('readmaster.agents.subprocess.run', return_value=failure) as call:
                with self.assertRaisesRegex(RuntimeError, '^Codex 调用失败。') as error:
                    agent.call('terms', {}, {})
                self.assertEqual(call.call_count, 1)
                self.assertNotIn('secret-marker', str(error.exception))


if __name__ == '__main__':
    unittest.main()
