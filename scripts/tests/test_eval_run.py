"""Exercise evaluation provenance and comparisons without loading model weights."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from evaluation.cases import load_cases, select_declaration
from evaluation.run import ALLOWED_VARIANTS, run_evaluation


PROMPT = "Keep cached zero and False results so the database is not queried again."
CASE = {"id": "cached_values", "language": "python", "prompt": PROMPT,
        "source": "def cached_value(cache, key, load):\n    if key not in cache:\n        cache[key] = load(key)\n    return cache[key]\n",
        "expected": "REVIEW_ONLY_EXPECTATION", "reviewNotes": "REVIEW_ONLY_NOTES",
        "family": "REVIEW_ONLY_FAMILY", "provenance": {"note": "REVIEW_ONLY_PROVENANCE"}}
COMMENT = "Preserve cached falsey results to avoid repeating the database lookup."


class FixtureBackend:
    def __init__(self):
        self.raw_calls = []
        self.payloads = []
        self.unloaded = False

    def configure(self, configuration):
        self.configuration = configuration

    def generate(self, system, payload):
        self.payloads.append(copy.deepcopy(payload))
        if payload["task"] == "comment_verification":
            decision = {"supported": True, "reason": "Membership testing preserves cached values."}
        elif payload["task"] == "comment_synthesis":
            decision = {"action": "synthesize_comment", "comment": COMMENT,
                        "promptEvidence": {"passageId": "p0"}, "reason": "Avoid repeated reads."}
        else:
            decision = {"action": "propose_comment", "comment": COMMENT, "reason": "Avoid repeated reads."}
        self.raw_calls.append({"text": json.dumps(decision), "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "elapsedMs": 1})
        return decision, {"inputTokens": 10, "outputTokens": 5}

    def unload(self):
        self.unloaded = True


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.cases = self.directory / "cases.json"
        self.write_cases([CASE])
        self.model = self.directory / "fixture-not-real-weights.gguf"
        self.model.write_bytes(b"TEST FIXTURE: NOT MODEL WEIGHTS")
        self.output = self.directory / "run.json"

    def write_cases(self, cases):
        self.cases.write_text(json.dumps({"description": "Synthetic fixture cases", "cases": cases}), encoding="utf-8")

    def run_fixture(self, backend=None, **kwargs):
        backend = backend or FixtureBackend()
        report = run_evaluation(self.model, self.cases, self.output,
                                backend_factory=lambda: backend, **kwargs)
        return report, backend

    def test_real_pipeline_uses_two_calls_and_baselines_one_without_label_leakage(self):
        report, backend = self.run_fixture()
        self.assertEqual(report["status"], "completed")
        rows = {row["variant"]: row for row in report["results"]}
        self.assertEqual(set(rows), set(ALLOWED_VARIANTS))
        self.assertEqual(rows["between_the_lines"]["metrics"]["modelCalls"], 2)
        self.assertEqual(rows["prompted_comment"]["metrics"]["modelCalls"], 1)
        self.assertEqual(rows["code_only"]["metrics"]["modelCalls"], 1)
        self.assertEqual(rows["between_the_lines"]["metrics"]["inputTokens"], 20)
        self.assertTrue(all(row["status"] == "comment" and row["syntaxPreserved"] for row in rows.values()))
        self.assertTrue(backend.unloaded)
        payloads = json.dumps(backend.payloads)
        self.assertNotIn("REVIEW_ONLY_", payloads)
        self.assertEqual(report["dataset"]["cases"][0]["reviewNotes"], "REVIEW_ONLY_NOTES")
        baseline_payloads = [item for item in backend.payloads if item["task"] == "comment_baseline"]
        self.assertEqual(sum("intent" in item for item in baseline_payloads), 1)
        code_only = next(item for item in baseline_payloads if "intent" not in item)
        self.assertNotIn(PROMPT, json.dumps(code_only))
        self.assertNotIn("originalContexts", code_only)
        self.assertEqual(self.cases.read_text(encoding="utf-8"), json.dumps({"description": "Synthetic fixture cases", "cases": [CASE]}))

    def test_frozen_cases_model_and_source_hashes_are_recorded(self):
        report, _ = self.run_fixture()
        self.assertEqual(report["dataset"]["sha256"], hashlib.sha256(self.cases.read_bytes()).hexdigest())
        self.assertEqual(report["model"]["sha256"], hashlib.sha256(self.model.read_bytes()).hexdigest())
        self.assertEqual(report["dataset"]["kind"], "synthetic_development")
        self.assertIn("worker/btl_agent/synthesis.py", report["sourceHashes"])
        self.assertTrue(report["sourceHashesUnchanged"])
        self.assertEqual(report["dataset"]["cases"], [CASE])
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8"))["runId"], report["runId"])

    def test_prior_report_is_not_overwritten_and_backend_is_not_started(self):
        self.output.write_text("previous report", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            run_evaluation(self.model, self.cases, self.output,
                           backend_factory=lambda: self.fail("Existing output must fail before backend creation."))
        self.assertEqual(self.output.read_text(encoding="utf-8"), "previous report")

    def test_known_decline_is_distinct_from_generation_failure(self):
        class DecliningBackend(FixtureBackend):
            def generate(self, system, payload):
                self.payloads.append(copy.deepcopy(payload))
                return {"action": "skip", "reason": "No useful explanation is supported."}, {"inputTokens": 2, "outputTokens": 3}
        report, _ = self.run_fixture(DecliningBackend())
        self.assertTrue(all(row["status"] == "abstained" for row in report["results"]))
        self.assertTrue(all(row["comment"] is None for row in report["results"]))
        self.assertTrue(all(row["syntaxPreserved"] is None for row in report["results"]))

    def test_inference_error_keeps_raw_candidate_out_of_published_comment(self):
        class RejectedBackend(FixtureBackend):
            def generate(self, system, payload):
                if payload["task"] == "comment_verification":
                    self.raw_calls.append({"text": '{"supported":false,"reason":"Wrong topic"}',
                                           "usage": {"prompt_tokens": 4, "completion_tokens": 3}})
                    return {"supported": False, "reason": "Wrong topic"}, {"inputTokens": 4, "outputTokens": 3}
                return super().generate(system, payload)
        report, _ = self.run_fixture(RejectedBackend(), variants=["between_the_lines"])
        row = report["results"][0]
        self.assertEqual(row["status"], "abstained")
        self.assertIsNone(row["comment"])
        self.assertIn(COMMENT, row["rawCalls"][0]["text"])
        self.assertEqual(row["metrics"]["modelCalls"], 2)
        self.assertEqual(row["metrics"]["inputTokens"], 14)

    def test_interruption_leaves_valid_report_and_unloads_backend(self):
        class InterruptedBackend(FixtureBackend):
            def generate(self, system, payload):
                raise KeyboardInterrupt("test interruption")
        backend = InterruptedBackend()
        with self.assertRaises(KeyboardInterrupt):
            self.run_fixture(backend)
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "interrupted")
        self.assertEqual(report["interruption"]["type"], "KeyboardInterrupt")
        self.assertTrue(backend.unloaded)

    def test_failed_configuration_is_saved_as_interrupted(self):
        class BrokenBackend(FixtureBackend):
            def configure(self, configuration):
                raise RuntimeError("fixture configuration failure")
        backend = BrokenBackend()
        with self.assertRaises(RuntimeError):
            self.run_fixture(backend)
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "interrupted")
        self.assertTrue(backend.unloaded)

    def test_seeded_order_is_reproducible_and_all_pairs_appear(self):
        first, _ = self.run_fixture(seed=73)
        self.output = self.directory / "second.json"
        second, _ = self.run_fixture(seed=73)
        self.assertEqual(first["jobOrder"], second["jobOrder"])
        self.assertEqual(len(first["jobOrder"]), 3)

    def test_source_is_never_executed(self):
        marker = self.directory / "must-not-exist.txt"
        case = copy.deepcopy(CASE)
        case["source"] = f"open({str(marker)!r}, 'w').write('executed')\n" + case["source"]
        self.write_cases([case])
        self.run_fixture()
        self.assertFalse(marker.exists())

    def test_case_loader_rejects_duplicates_unknown_ids_and_ambiguous_targets(self):
        self.write_cases([CASE, CASE])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_cases(self.cases)
        self.write_cases([CASE])
        with self.assertRaisesRegex(ValueError, "Unknown"):
            load_cases(self.cases, case_ids=["missing"])
        case = copy.deepcopy(CASE)
        case["source"] += "\ndef other():\n    return 1\n"
        self.write_cases([case])
        with self.assertRaisesRegex(ValueError, "unique symbolId"):
            load_cases(self.cases)
        case["symbolId"] = "python:cached_value"
        self.write_cases([case])
        self.assertEqual(select_declaration(load_cases(self.cases)["cases"][0]).name, "cached_value")

    def test_case_loader_selects_single_method_but_rejects_ambiguous_overloads(self):
        java = {"id": "java_single", "language": "java", "prompt": "Return the original value.",
                "source": "class Example {\n    int same(int value) { return value; }\n}\n"}
        self.assertEqual(select_declaration(java).name, "same")
        java["source"] = "class Example {\n    int same(int value) { return value; }\n    long same(long value) { return value; }\n}\n"
        java["symbol"] = "same"
        with self.assertRaisesRegex(ValueError, "unique symbolId"):
            select_declaration(java)


if __name__ == "__main__":
    unittest.main()
