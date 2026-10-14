import threading
import unittest

from readmaster.translation_schedule import run_waves


class ScheduleTests(unittest.TestCase):
    def test_two_calls_overlap_and_next_wave_overlaps_score(self):
        barrier = threading.Barrier(2)
        scoring, next_wave = threading.Event(), threading.Event()
        committed, checked, peak = [], [], [0]
        active, guard = [0], threading.Lock()
        groups = [[{'id': i}] for i in range(4)]
        def translate(group):
            with guard:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            barrier.wait(timeout=3)
            if group[0]['id'] >= 2:
                self.assertTrue(scoring.wait(3))
                next_wave.set()
            with guard:
                active[0] -= 1
            return group[0]['id']
        def score(blocks):
            checked.extend(b['id'] for b in blocks)
            if blocks[0]['id'] == 0:
                self.assertEqual(set(committed), {0, 1})
                scoring.set()
                self.assertTrue(next_wave.wait(3))
        run_waves(groups, translate, lambda g, r: committed.append(r), score,
                  lambda w: None, lambda w: None, lambda: False)
        self.assertEqual(peak[0], 2)
        self.assertEqual(set(committed), set(range(4)))
        self.assertEqual(checked, list(range(4)))

    def test_failure_keeps_successful_sibling_and_stops_new_waves(self):
        committed, started = [], []
        def translate(group):
            ident = group[0]['id']
            started.append(ident)
            if ident == 0:
                raise RuntimeError('offline')
            return ident
        with self.assertRaisesRegex(RuntimeError, 'offline'):
            run_waves([[{'id': i}] for i in range(4)], translate,
                      lambda g, r: committed.append(r), lambda b: None,
                      lambda w: None, lambda w: None, lambda: False)
        self.assertEqual(committed, [1])
        self.assertEqual(set(started), {0, 1})

    def test_pause_keeps_completed_wave_without_starting_next(self):
        paused, saved = [False], []
        def commit(g, result):
            saved.append(result)
            paused[0] = True
        with self.assertRaises(InterruptedError):
            run_waves([[{'id': i}] for i in range(4)], lambda g: g[0]['id'],
                      commit, lambda b: None, lambda w: None, lambda w: None, lambda: paused[0])
        self.assertEqual(set(saved), {0, 1})
