"""Check blinding, human-label validation and incomplete-review denominators."""

import csv
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.review import COLUMNS, create_review_packet, summarize_reviews


class ReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "run.json"
        cases = [{"id": name, "language": "python", "prompt": "Preserve order. </script><img src=x>",
                  "source": "def keep(items):\n    return list(items)\n", "expected": "SECRET_EXPECTED",
                  "reviewNotes": "SECRET_NOTES"} for name in ("a", "b", "c")]
        self.report = {"schemaVersion": 1, "runId": "run-one", "status": "completed",
                       "dataset": {"sha256": "dataset-hash", "cases": cases}, "results": []}
        for case in cases:
            for variant in ("between_the_lines", "prompted_comment"):
                status = "error" if case["id"] == "c" else "abstained" if case["id"] == "b" and variant == "between_the_lines" else "comment"
                self.report["results"].append({"caseId": case["id"], "variant": variant, "status": status,
                    "comment": "Preserve arrival order." if status == "comment" else None,
                    "decisionReason": "SECRET_VERDICT", "rawCalls": ["SECRET_RAW"], "syntaxPreserved": True,
                    "metrics": {"modelCalls": 2, "elapsedMs": 1000, "inputTokens": 99, "outputTokens": 10, "peakRssBytes": 12345}})
        self.path.write_text(json.dumps(self.report), encoding="utf-8")
        self.packet = self.root / "packet"
        create_review_packet(self.path, self.packet, "reviewer-one")
        self.key = json.loads((self.packet / "private/key.json").read_text(encoding="utf-8"))

    def rows(self):
        with (self.packet / "ratings.csv").open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))

    def write(self, rows, name="filled.csv"):
        path = self.root / name
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def score(self, paths, name="summary.json"):
        return summarize_reviews(self.path, self.packet, paths, self.root / name)

    def test_export_hides_variant_labels_verdicts_and_reference_answers(self):
        page = (self.packet / "review.html").read_text(encoding="utf-8")
        for secret in ("between_the_lines", "prompted_comment", "SECRET_EXPECTED", "SECRET_NOTES", "SECRET_VERDICT", "SECRET_RAW"):
            self.assertNotIn(secret, page)
        self.assertNotIn("<img src=x>", page)
        encoded = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.DOTALL).group(1)
        payload = json.loads(encoded)
        self.assertIn("</script><img", payload["candidates"][0]["prompt"])
        self.assertEqual(len(payload["candidates"]), 6)
        self.assertIn("default-src 'none'", page)
        self.assertIn("textContent", page)

    def test_blank_template_stays_pending_not_zero_or_success(self):
        result = self.score([self.packet / "ratings.csv"])
        score = result["reviewers"]["reviewer-one"]["variants"]["between_the_lines"]
        self.assertIsNone(score["usableComments"]["rate"])
        self.assertEqual(score["pendingComments"], 1)
        self.assertEqual(score["pendingAbstentions"], 1)
        self.assertEqual(score["unavailable"], 1)
        self.assertEqual(score["reviewCoverage"]["denominator"], 2)

    def test_partial_comments_stay_pending_and_abstentions_have_their_own_rate(self):
        rows = self.rows()
        for row in rows:
            status = self.key["candidates"][row["candidateId"]]["status"]
            if status == "comment":
                row["factuality"] = "2"
            if status == "abstained":
                row["abstentionAppropriate"] = "yes"
        result = self.score([self.write(rows)])
        score = result["reviewers"]["reviewer-one"]["variants"]["between_the_lines"]
        self.assertEqual(score["reviewedComments"], 0)
        self.assertEqual(score["pendingComments"], 1)
        self.assertEqual(score["appropriateAbstentions"]["rate"], 1)
        self.assertIsNone(score["usableComments"]["rate"])

    def test_reviewers_are_not_pooled_and_disagreements_and_paired_coverage_are_reported(self):
        first, second = self.rows(), self.rows()
        for rows, reviewer in ((first, "alice"), (second, "bob")):
            for row in rows:
                row["reviewer"] = reviewer
                entry = self.key["candidates"][row["candidateId"]]
                if entry["status"] == "comment":
                    for axis in ("factuality", "intentPreservation", "usefulness", "readability"):
                        row[axis] = "2"
                    if reviewer == "bob" and entry["variant"] == "prompted_comment":
                        row["factuality"] = "0"
                if entry["status"] == "abstained":
                    row["abstentionAppropriate"] = "yes"
        result = self.score([self.write(first, "alice.csv"), self.write(second, "bob.csv")])
        self.assertEqual(set(result["reviewers"]), {"alice", "bob"})
        self.assertNotIn("usableComments", result["variants"]["prompted_comment"])
        self.assertEqual(result["reviewers"]["alice"]["variants"]["prompted_comment"]["usableComments"]["rate"], 1)
        self.assertEqual(result["reviewers"]["alice"]["variants"]["prompted_comment"]["strongAnnotations"]["rate"], 1)
        self.assertEqual(result["reviewers"]["alice"]["variants"]["prompted_comment"]["axisCounts"]["usefulness"]["2"], 2)
        self.assertEqual(result["reviewers"]["bob"]["variants"]["prompted_comment"]["falseClaims"]["rate"], 1)
        self.assertEqual(len(result["disagreements"]), 2)
        pair = result["reviewers"]["bob"]["pairedComparisons"][0]
        self.assertEqual(pair["fullyReviewedCommentPairs"], 1)
        self.assertEqual(pair["firstUsableOnly"], 1)

    def test_runtime_error_cannot_be_a_successful_abstention(self):
        rows = self.rows()
        next(row for row in rows if self.key["candidates"][row["candidateId"]]["status"] == "error")["abstentionAppropriate"] = "yes"
        with self.assertRaisesRegex(ValueError, "runtime error"):
            self.score([self.write(rows)])

    def test_duplicate_and_unknown_candidates_are_rejected(self):
        rows = self.rows()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.score([self.write(rows + [rows[0]])])
        rows[0]["candidateId"] = "unknown"
        with self.assertRaisesRegex(ValueError, "unknown"):
            self.score([self.write(rows, "unknown.csv")])

    def test_decimal_and_out_of_range_grades_are_rejected(self):
        for invalid in ("2.0", "3", "-1", "NaN"):
            rows = self.rows()
            next(row for row in rows if self.key["candidates"][row["candidateId"]]["status"] == "comment")["factuality"] = invalid
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, "integer"):
                self.score([self.write(rows, invalid + ".csv")])

    def test_modified_report_is_rejected(self):
        self.report["results"][0]["comment"] = "Different wording."
        self.path.write_text(json.dumps(self.report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exact frozen report"):
            self.score([self.packet / "ratings.csv"])

    def test_existing_packet_and_summary_are_preserved(self):
        with self.assertRaisesRegex(ValueError, "new review directory"):
            create_review_packet(self.path, self.packet, "alice")
        self.score([self.packet / "ratings.csv"])
        with self.assertRaisesRegex(ValueError, "new summary path"):
            self.score([self.packet / "ratings.csv"])

    def test_private_key_cannot_disguise_an_error_status(self):
        self.key["candidates"][next(iter(self.key["candidates"]))]["status"] = "unknown"
        (self.packet / "private/key.json").write_text(json.dumps(self.key), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "status differs"):
            self.score([self.packet / "ratings.csv"])

    def test_incomplete_run_is_not_exported_as_a_finished_experiment(self):
        self.report["status"] = "interrupted"
        self.path.write_text(json.dumps(self.report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Complete the evaluation"):
            create_review_packet(self.path, self.root / "pending", "alice")

    def test_completed_run_cannot_silently_omit_a_failed_or_missing_pair(self):
        self.report["results"].pop()
        self.path.write_text(json.dumps(self.report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "every frozen case/variant"):
            create_review_packet(self.path, self.root / "missing", "alice")


if __name__ == "__main__":
    unittest.main()
