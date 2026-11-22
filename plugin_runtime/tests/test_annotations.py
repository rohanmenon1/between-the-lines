"""Exercise checked host-model comment writes without model inference."""

import ast
import codecs
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "plugin_runtime"), str(ROOT / "worker")]

from between_the_lines_plugin.annotations import apply_annotation, preview_annotation, undo_annotation
from between_the_lines_plugin.coordinator import Coordinator
from btl_agent.parsing import fingerprint
from btl_agent.types import WorkerError


BEFORE = 'def rank(items):\n    return items\n'
AFTER = 'def rank(items):\n    return sorted(items, key=lambda item: item["score"], reverse=True)\n'
PROMPT = "Rank higher scores first. Preserve arrival order when scores tie so the leaderboard stays stable."
COMMENT = "Keep equal-score entries in arrival order so the leaderboard stays stable."
REASON = "The stable sort preserves ties while returning higher scores first."


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.project = self.directory / "project"
        self.project.mkdir()
        self.path = self.project / "rank.py"
        self.workspace = str(self.project)
        self.coordinator = Coordinator(self.directory / "state")

    def event(self, name, **fields):
        return {"hook_event_name": name, "cwd": self.workspace, "session_id": "session-one",
                "turn_id": "turn-one", "model": "test-host-model", **fields}

    def prepare(self, before=BEFORE, after=AFTER, prompt=PROMPT, bom=False, model="test-host-model"):
        prefix = codecs.BOM_UTF8 if bom else b""
        self.path.write_bytes(prefix + before.encode("utf-8"))
        self.coordinator.handle_hook("UserPromptSubmit", self.event("UserPromptSubmit", prompt=prompt))
        fields = {"tool_use_id": "edit-one", "tool_name": "write_file",
                  "tool_input": {"path": str(self.path), "content": after}}
        self.coordinator.handle_hook("PreToolUse", self.event("PreToolUse", **fields))
        self.path.write_bytes(prefix + after.encode("utf-8"))
        self.coordinator.handle_hook("PostToolUse", self.event("PostToolUse", **fields))
        self.coordinator.handle_hook("Stop", self.event("Stop", model=model))
        jobs = self.coordinator.store.pending(self.workspace)
        self.assertEqual(1, len(jobs))
        self.job = jobs[0]
        self.id = self.job["id"]
        return self.job

    def preview(self, comment=COMMENT, passage="p0", reason=REASON):
        return preview_annotation(self.coordinator, self.workspace, self.id, comment, passage, reason)

    def apply(self, preview):
        return apply_annotation(self.coordinator, self.workspace, self.id, preview["previewId"], REASON)

    def records(self):
        return self.coordinator.history.requirements_for_paths([str(self.path)], self.workspace)

    def prepare_revision(self):
        self.prepare()
        self.apply(self.preview())
        old_record = self.records()[0]
        source = self.path.read_text().replace("reverse=True", "reverse=False")
        prompt = "Supersede " + old_record["id"] + "\nRank lower scores first while retaining arrival order for tied scores."
        self.coordinator.handle_hook("UserPromptSubmit", self.event("UserPromptSubmit", prompt=prompt, turn_id="turn-two"))
        fields = {"turn_id": "turn-two", "tool_use_id": "edit-two", "tool_name": "write_file",
                  "tool_input": {"path": str(self.path), "content": source}}
        self.coordinator.handle_hook("PreToolUse", self.event("PreToolUse", **fields))
        self.path.write_text(source, encoding="utf-8", newline="")
        self.coordinator.handle_hook("PostToolUse", self.event("PostToolUse", **fields))
        self.coordinator.handle_hook("Stop", self.event("Stop", turn_id="turn-two"))
        self.job = self.coordinator.store.pending(self.workspace)[0]
        self.id = self.job["id"]
        return old_record, source

    def test_preview_is_comment_only_and_does_not_write_or_promote_generated_text(self):
        self.prepare()
        preview = self.preview()
        self.assertEqual(AFTER.encode(), self.path.read_bytes())
        self.assertEqual([], self.records())
        self.assertTrue(preview["codeUnchanged"])
        self.assertTrue(preview["requiresSemanticReview"])
        self.assertIn("# " + COMMENT, preview["diff"])
        self.assertEqual(PROMPT, preview["evidence"][0]["quote"])
        self.assertNotEqual(COMMENT, preview["evidence"][0]["quote"])

    def test_apply_preserves_ast_and_records_original_evidence_and_host_route(self):
        self.prepare()
        result = self.apply(self.preview())
        source = self.path.read_text()
        self.assertTrue(result["applied"])
        self.assertEqual(ast.dump(ast.parse(AFTER)), ast.dump(ast.parse(source)))
        self.assertTrue(source.startswith("# " + COMMENT))
        record = self.records()[0]
        self.assertEqual(PROMPT, record["prompt"])
        self.assertEqual(self.job["promptEventId"], record["promptEventId"])
        self.assertNotEqual(self.job["eventId"], record["promptEventId"])
        self.assertEqual("host_codex", record["generationRoute"])
        self.assertEqual("codex:test-host-model", record["generation"]["model"])
        self.assertEqual("host_model_judgment", record["supportReview"]["kind"])
        self.assertEqual("# " + COMMENT, record["commentText"])
        self.assertEqual("exact", record["scopeStatus"])

    def test_repeated_apply_is_idempotent_and_does_not_rewrite_source(self):
        self.prepare()
        preview = self.preview()
        self.apply(preview)
        previous = self.path.stat()
        again = self.apply(preview)
        self.assertTrue(again["alreadyApplied"])
        self.assertEqual(previous.st_mtime_ns, self.path.stat().st_mtime_ns)
        self.assertEqual(1, len(self.records()))

    def test_missing_or_false_evidence_and_copied_tasks_are_rejected(self):
        self.prepare()
        for comment, passage in ((COMMENT, "invented"), (PROMPT, "p0")):
            with self.subTest(comment=comment, passage=passage), self.assertRaises(WorkerError):
                self.preview(comment, passage)
        self.assertEqual(AFTER.encode(), self.path.read_bytes())

    def test_unsafe_comment_and_directives_never_reach_a_write(self):
        self.prepare()
        for comment in ("Keep ties.\nreturn []", "coding: latin-1.", "# Keep ties.", "Keep `ties."):
            with self.subTest(comment=comment), self.assertRaises(WorkerError):
                self.preview(comment)
        self.assertEqual(AFTER.encode(), self.path.read_bytes())

    def test_a_comment_cannot_push_the_file_past_the_readable_source_limit(self):
        padding = "# " + "x" * (128 * 1024 - len(AFTER.encode()) - 20 - 3) + "\n"
        self.prepare(before=BEFORE + padding, after=AFTER + padding)
        with self.assertRaisesRegex(WorkerError, "exceed the source limit"):
            self.preview()
        self.assertEqual((AFTER + padding).encode(), self.path.read_bytes())

    def test_human_edit_after_preview_is_preserved(self):
        self.prepare()
        preview = self.preview()
        changed = "# Human explanation.\n" + AFTER
        self.path.write_text(changed, encoding="utf-8", newline="")
        with self.assertRaisesRegex(WorkerError, "Source changed"):
            self.apply(preview)
        self.assertEqual(changed, self.path.read_text())
        self.assertEqual([], self.records())

    def test_touched_then_restored_source_is_stale_even_when_bytes_match(self):
        self.prepare()
        preview = self.preview()
        info = self.path.stat()
        os.utime(self.path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
        with self.assertRaisesRegex(WorkerError, "Source changed"):
            self.apply(preview)
        self.assertEqual(AFTER.encode(), self.path.read_bytes())

    def test_a_replaced_preview_cannot_be_applied_by_an_old_id(self):
        self.prepare()
        old = self.preview()
        newer = self.preview("Preserve tie order to prevent leaderboard entries from jumping between refreshes.")
        self.assertNotEqual(old["previewId"], newer["previewId"])
        with self.assertRaisesRegex(WorkerError, "current preview"):
            self.apply(old)
        self.apply(newer)

    def test_skip_prevents_a_prepared_comment_from_being_applied(self):
        self.prepare()
        preview = self.preview()
        self.coordinator.skip(self.workspace, self.id, "Not useful enough.")
        with self.assertRaisesRegex(WorkerError, "current preview"):
            self.apply(preview)
        self.assertEqual(AFTER.encode(), self.path.read_bytes())

    def test_source_path_replaced_with_a_symlink_is_rejected(self):
        self.prepare()
        preview = self.preview()
        target = self.project / "other.py"
        target.write_text(AFTER, encoding="utf-8")
        self.path.unlink()
        try:
            self.path.symlink_to(target)
        except OSError:
            self.skipTest("Windows symlink creation is unavailable.")
        with self.assertRaisesRegex(WorkerError, "moved"):
            self.apply(preview)
        self.assertEqual(AFTER, target.read_text())

    def test_modified_saved_preview_cannot_insert_executable_source(self):
        self.prepare()
        preview = self.preview()
        job = self.coordinator.store.get_job(self.workspace, self.id)
        corrupt = copy.deepcopy(job["preview"])
        corrupt["annotatedSource"] += "print('unexpected source')\n"
        corrupt["id"] = hashlib.sha256(json.dumps({key: value for key, value in corrupt.items() if key != "id"}, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        self.coordinator.store.update_job(self.workspace, self.id, preview=corrupt)
        with self.assertRaisesRegex(WorkerError, "validated comment-only"):
            self.apply({"previewId": corrupt["id"]})
        self.assertEqual(AFTER.encode(), self.path.read_bytes())

    def test_history_failure_keeps_journal_and_recovers_without_reinsertion(self):
        self.prepare()
        preview = self.preview()
        with patch.object(self.coordinator.history, "record_requirement", side_effect=OSError("fixture history unavailable")):
            with self.assertRaises(OSError):
                self.apply(preview)
        job = self.coordinator.store.get_job(self.workspace, self.id)
        self.assertEqual("previewed", job["state"])
        self.assertTrue(job["pendingWrite"])
        self.assertTrue(self.path.read_text().startswith("# " + COMMENT))
        info = self.path.stat()
        self.apply(preview)
        self.assertEqual(info.st_mtime_ns, self.path.stat().st_mtime_ns)
        self.assertEqual(1, len(self.records()))
        self.assertIsNone(self.coordinator.store.get_job(self.workspace, self.id)["pendingWrite"])

    def test_history_commit_then_state_failure_is_recovered_idempotently(self):
        self.prepare()
        preview = self.preview()
        update = self.coordinator.store.update_job
        def fail_applied(workspace, job_id, **updates):
            if updates.get("state") == "applied":
                raise OSError("fixture state commit unavailable")
            return update(workspace, job_id, **updates)
        with patch.object(self.coordinator.store, "update_job", side_effect=fail_applied):
            with self.assertRaises(OSError):
                self.apply(preview)
        self.assertEqual(1, len(self.records()))
        self.apply(preview)
        self.assertEqual(1, len(self.records()))

    def test_failed_disk_replace_can_retry_the_recorded_preview(self):
        self.prepare()
        preview = self.preview()
        with patch("between_the_lines_plugin.annotations.os.replace", side_effect=OSError("fixture write unavailable")):
            with self.assertRaises(OSError):
                self.apply(preview)
        self.assertEqual(AFTER.encode(), self.path.read_bytes())
        self.assertEqual([], list(self.project.glob(".between-the-lines-*.tmp")))
        self.apply(preview)

    def test_missing_original_prompt_prevents_source_write(self):
        self.prepare()
        preview = self.preview()
        with self.coordinator.history._connection() as db:
            db.execute("DELETE FROM prompts WHERE id=?", (self.job["promptEventId"],))
        with self.assertRaisesRegex(WorkerError, "immutable original"):
            self.apply(preview)
        self.assertEqual(AFTER.encode(), self.path.read_bytes())

    def test_invalid_host_model_metadata_is_rejected_before_source_write(self):
        self.prepare(model="invalid\nmodel")
        preview = self.preview()
        with self.assertRaisesRegex(WorkerError, "model identity"):
            self.apply(preview)
        self.assertEqual(AFTER.encode(), self.path.read_bytes())

    def test_apply_and_undo_preserve_bom_crlf_permissions_and_original_context(self):
        after = AFTER.replace("\n", "\r\n")
        self.prepare(before=BEFORE.replace("\n", "\r\n"), after=after, bom=True)
        expected = self.path.read_bytes()
        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.apply(self.preview())
        annotated = self.path.read_bytes()
        self.assertTrue(annotated.startswith(codecs.BOM_UTF8 + b"# "))
        self.assertEqual(mode, stat.S_IMODE(self.path.stat().st_mode))
        self.assertNotIn(b"\n", annotated.replace(b"\r\n", b""))
        result = undo_annotation(self.coordinator, self.workspace, self.id)
        self.assertTrue(result["originalContextRetained"])
        self.assertEqual(expected, self.path.read_bytes())
        self.assertEqual("active", self.records()[0]["status"])
        self.assertTrue(undo_annotation(self.coordinator, self.workspace, self.id)["alreadyUndone"])

    def test_undo_preserves_later_code_and_comment_edits(self):
        self.prepare()
        self.apply(self.preview())
        changed = self.path.read_text() + "\n# Another person's edit.\n"
        self.path.write_text(changed, encoding="utf-8", newline="")
        with self.assertRaisesRegex(WorkerError, "Later source edits"):
            undo_annotation(self.coordinator, self.workspace, self.id)
        self.assertEqual(changed, self.path.read_text())

    def test_undo_state_commit_failure_recovers_without_reapplying_comment(self):
        self.prepare()
        self.apply(self.preview())
        update = self.coordinator.store.update_job
        def fail_undone(workspace, job_id, **updates):
            if updates.get("state") == "undone":
                raise OSError("fixture state commit unavailable")
            return update(workspace, job_id, **updates)
        with patch.object(self.coordinator.store, "update_job", side_effect=fail_undone):
            with self.assertRaises(OSError):
                undo_annotation(self.coordinator, self.workspace, self.id)
        self.assertEqual(AFTER.encode(), self.path.read_bytes())
        info = self.path.stat()
        result = undo_annotation(self.coordinator, self.workspace, self.id)
        self.assertTrue(result["recovered"])
        self.assertEqual(info.st_mtime_ns, self.path.stat().st_mtime_ns)

    def test_superseding_an_owned_annotation_does_not_break_checked_undo(self):
        old_record, before_comment_update = self.prepare_revision()
        comment = "Keep tied scores in arrival order while ranking lower scores first."
        self.apply(self.preview(comment))
        self.assertEqual(1, len(self.records()))
        self.assertNotEqual(old_record["id"], self.records()[0]["id"])
        undo_annotation(self.coordinator, self.workspace, self.id)
        self.assertEqual(before_comment_update, self.path.read_text())

    def test_recovery_after_supersession_uses_recorded_original_ownership(self):
        self.prepare_revision()
        preview = self.preview("Keep tied scores in arrival order while ranking lower scores first.")
        update = self.coordinator.store.update_job
        def fail_applied(workspace, job_id, **updates):
            if updates.get("state") == "applied":
                raise OSError("fixture final state commit unavailable")
            return update(workspace, job_id, **updates)
        with patch.object(self.coordinator.store, "update_job", side_effect=fail_applied):
            with self.assertRaises(OSError):
                self.apply(preview)
        self.assertEqual(1, len(self.records()))
        previous = self.path.stat()
        self.apply(preview)
        self.assertEqual(previous.st_mtime_ns, self.path.stat().st_mtime_ns)
        self.assertEqual(1, len(self.records()))

    def test_retired_original_after_preview_requires_a_new_review(self):
        old_record, source = self.prepare_revision()
        preview = self.preview("Keep tied scores in arrival order while ranking lower scores first.")
        self.coordinator.history.retire_requirement(old_record["id"], self.workspace)
        with self.assertRaisesRegex(WorkerError, "Original requirements changed"):
            self.apply(preview)
        self.assertEqual(source, self.path.read_text())

    def test_parallel_write_uses_the_coordinator_file_lock(self):
        self.prepare()
        with self.coordinator.store.job_lock(self.workspace, self.id):
            with self.assertRaisesRegex(ValueError, "already being updated"):
                self.preview()
        self.assertEqual(AFTER.encode(), self.path.read_bytes())

    def test_java_comment_preserves_parsed_code_without_compiling(self):
        self.path = self.project / "Invoice.java"
        before = "class Invoice {\n    java.math.BigDecimal display(java.math.BigDecimal amount) {\n        return amount;\n    }\n}\n"
        after = "class Invoice {\n    java.math.BigDecimal display(java.math.BigDecimal amount) {\n        return amount.setScale(2, java.math.RoundingMode.HALF_UP);\n    }\n}\n"
        prompt = "For invoice display, round half cents away from zero and always show two decimal places."
        self.prepare(before=before, after=after, prompt=prompt)
        result = self.apply(self.preview("Round displayed amounts to two decimals with half-cent ties away from zero.", reason="HALF_UP and scale two implement the requested display rule."))
        self.assertTrue(result["codeUnchanged"])
        self.assertEqual(fingerprint(after, "java"), fingerprint(self.path.read_text(), "java"))
        self.assertIn("    // Round displayed amounts", self.path.read_text())


if __name__ == "__main__":
    unittest.main()
