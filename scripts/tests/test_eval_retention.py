"""Check evaluation accounting and trusted fixture tests, without invoking a model."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from evaluation.retention import execute_checks, export_retention_tasks, grade_retention, load_retention_cases


FIXTURES = SCRIPTS.parent / "eval/retention-cases.json"
SOLUTIONS = {
    "leaderboard_filter": 'def rank(items, minimum_score=None):\n    selected = [item for item in items if minimum_score is None or item["score"] >= minimum_score]\n    return sorted(selected, key=lambda item: item["score"], reverse=True)\n',
    "optional_overrides": 'def options(defaults, overrides):\n    result = defaults.copy()\n    if overrides is not None:\n        result.update(overrides)\n    return result\n',
    "reverse_batch_order": 'def batches(items, size, reverse_batches=False):\n    result = [items[start:start + size] for start in range(0, len(items), size)]\n    return result[::-1] if reverse_batches else result\n',
}


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tasks, _ = load_retention_cases(FIXTURES)
        self.packet = self.root / "packet"

    def export(self, annotations=None):
        return export_retention_tasks(FIXTURES, self.packet, annotations)

    def key(self):
        return json.loads((self.packet / "private/key.json").read_text(encoding="utf-8"))

    def annotation_report(self, statuses=None):
        report = {"schemaVersion": 1, "status": "completed", "settings": {"variants": ["fixture_model"]},
                  "dataset": {"cases": [{"id": case["id"], "prompt": case["originalPrompt"],
                                          "source": case["source"], "symbol": case["symbol"]}
                                         for case in self.tasks["cases"]]}, "results": []}
        for index, case in enumerate(self.tasks["cases"]):
            status = (statuses or {}).get(index, "comment")
            report["results"].append({"caseId": case["id"], "variant": "fixture_model", "status": status,
                                      "comment": "Preserve the original constraints when changing this function." if status == "comment" else None})
        path = self.root / "fixture-annotations.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def test_original_fixtures_preserve_requirements_but_do_not_complete_follow_up(self):
        for case in self.tasks["cases"]:
            with self.subTest(case=case["id"]):
                original = execute_checks(case["source"], case["checks"])
                follow_up = execute_checks(case["source"], case["followUpChecks"])
                self.assertTrue(all(item["passed"] for item in original))
                self.assertFalse(all(item["passed"] for item in follow_up))

    def test_trusted_solutions_complete_new_task_and_keep_all_originals(self):
        for case in self.tasks["cases"]:
            with self.subTest(case=case["id"]):
                outcomes = execute_checks(SOLUTIONS[case["id"]], case["checks"] + case["followUpChecks"])
                self.assertTrue(all(item["passed"] for item in outcomes), outcomes)

    def test_input_mutation_is_failure_even_when_return_value_matches(self):
        check = {"function": "options", "args": [{"a": 1}, {"b": 2}], "expected": {"a": 1, "b": 2}, "inputsUnchanged": True}
        source = "def options(defaults, overrides):\n    defaults.update(overrides)\n    return defaults\n"
        self.assertFalse(execute_checks(source, [check])[0]["passed"])

    def test_candidate_timeout_is_bounded(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            execute_checks("def wait():\n    while True:\n        pass\n", [{"function": "wait", "expected": None}], timeout=0.2)

    def test_public_jobs_do_not_reveal_original_prompt_variants_or_checks(self):
        self.export(self.annotation_report())
        key = self.key()
        self.assertEqual(6, len(key["jobs"]))
        for job_id, job in key["jobs"].items():
            folder = self.packet / "jobs" / job_id
            task = (folder / "TASK.md").read_text(encoding="utf-8")
            case = next(case for case in self.tasks["cases"] if case["id"] == job["caseId"])
            self.assertIn(case["followUpTask"], task)
            self.assertNotIn(case["originalPrompt"], task)
            self.assertNotIn("fixture_model", task)
            self.assertNotIn("followUpChecks", task)
            source = (folder / "source.py").read_text(encoding="utf-8")
            self.assertEqual(job["variant"] == "fixture_model", source.startswith("# Preserve"))

    def test_unavailable_annotations_and_missing_edits_never_count_as_retained(self):
        self.export(self.annotation_report({0: "error", 1: "abstained"}))
        result = grade_retention(self.packet, self.root / "summary.json", allow_execution=True)
        self.assertEqual(6, result["plannedJobs"])
        for value in result["variants"].values():
            self.assertEqual(0, value["executed"])
            self.assertEqual(0, value["jointSuccess"])
            self.assertIsNone(value["retentionRateOfExecuted"])
        summary = json.loads((self.root / "summary.json").read_text())
        self.assertEqual(1, sum(item["status"] == "annotation_error" for item in summary["jobs"]))

    def test_unchanged_submissions_cannot_win_by_preserving_originals_alone(self):
        self.export()
        for job_id in self.key()["jobs"]:
            folder = self.packet / "jobs" / job_id
            (folder / "result.py").write_bytes((folder / "source.py").read_bytes())
        result = grade_retention(self.packet, self.root / "summary.json", allow_execution=True)
        counts = result["variants"]["no_comment"]
        self.assertEqual(3, counts["originalsPreserved"])
        self.assertEqual(0, counts["taskCompleted"])
        self.assertEqual(0, counts["jointSuccess"])

    def test_successful_submissions_are_scored_as_joint_success(self):
        self.export()
        for job_id, job in self.key()["jobs"].items():
            (self.packet / "jobs" / job_id / "result.py").write_text(SOLUTIONS[job["caseId"]], encoding="utf-8")
        result = grade_retention(self.packet, self.root / "summary.json", allow_execution=True)
        self.assertEqual(3, result["variants"]["no_comment"]["jointSuccess"])

    def test_execution_requires_explicit_flag_and_existing_results_are_preserved(self):
        self.export()
        with patch("evaluation.retention.execute_checks") as execute:
            with self.assertRaisesRegex(ValueError, "allow-execution"):
                grade_retention(self.packet, self.root / "summary.json")
            execute.assert_not_called()
        with self.assertRaisesRegex(ValueError, "new retention"):
            self.export()
        grade_retention(self.packet, self.root / "summary.json", allow_execution=True)
        with self.assertRaisesRegex(ValueError, "new grade"):
            grade_retention(self.packet, self.root / "summary.json", allow_execution=True)

    def test_mismatched_annotation_source_is_rejected_before_export(self):
        path = self.annotation_report()
        report = json.loads(path.read_text())
        report["dataset"]["cases"][0]["source"] += "# a different source\n"
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "different original"):
            self.export(path)
        self.assertFalse(self.packet.exists())

    def test_missing_comment_and_unknown_status_cannot_be_silent_controls(self):
        path = self.annotation_report()
        original = json.loads(path.read_text())
        for status, comment in (("comment", None), ("unknown", None), ("abstained", "not actually absent")):
            with self.subTest(status=status, comment=comment):
                report = json.loads(json.dumps(original))
                report["results"][0].update(status=status, comment=comment)
                path.write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, "distinguish"):
                    self.export(path)
                self.assertFalse(self.packet.exists())

    def test_modified_input_is_detected_before_candidate_execution(self):
        self.export()
        job_id = next(iter(self.key()["jobs"]))
        folder = self.packet / "jobs" / job_id
        (folder / "result.py").write_bytes((folder / "source.py").read_bytes())
        (folder / "TASK.md").write_text("Changed task")
        with patch("evaluation.retention.execute_checks") as execute:
            with self.assertRaisesRegex(ValueError, "modified"):
                grade_retention(self.packet, self.root / "summary.json", allow_execution=True)
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
