import copy
from pathlib import Path
import tempfile
import unittest

from readmaster.server import CachedAgent
from readmaster.pipeline import review_schema


class AgentValidationTests(unittest.TestCase):
    def test_adjacent_sentence_evidence_is_repaired_before_caching(self):
        invalid = {"passed": False, "issues": [{"id": "s1", "severity": "blocking",
                   "evidence": "must", "reason": "语气错误", "suggestion": "保留可能"}]}
        valid = {"passed": False, "issues": [{**invalid["issues"][0], "evidence": "may"}]}
        calls = []

        class Model:
            def call(self, role, payload, schema):
                calls.append(copy.deepcopy(payload))
                return invalid if len(calls) == 1 else valid

        payload = {"sentences": [{"id": "s1", "source": "It may help."},
                                 {"id": "s2", "source": "It must work."}]}
        with tempfile.TemporaryDirectory() as folder:
            agent = CachedAgent(Model(), folder, {"provider": "test", "model": "test"})
            self.assertEqual(agent.call("evaluate", payload, review_schema()), valid)
            self.assertIn("s1", calls[1]["validation_feedback"])
            self.assertNotIn("validation_feedback", payload)
            self.assertEqual(len(list(Path(folder).glob("*.json"))), 1)
            agent.call("evaluate", payload, review_schema())
            self.assertEqual(len(calls), 2)

    def test_invalid_evaluation_has_a_finite_budget_and_is_never_cached(self):
        calls = []

        class Model:
            def call(self, role, payload, schema):
                calls.append(role)
                return {"passed": False, "issues": []}

        with tempfile.TemporaryDirectory() as folder:
            agent = CachedAgent(Model(), folder, {"provider": "test", "model": "test"})
            with self.assertRaises(ValueError):
                agent.call("evaluate", {"sentences": []}, review_schema())
            self.assertEqual(len(calls), 3)
            self.assertEqual(list(Path(folder).glob("*.json")), [])
