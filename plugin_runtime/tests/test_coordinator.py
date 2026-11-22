"""Observe real local file edits without invoking a model or external service."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "plugin_runtime"), str(ROOT / "worker")]

from between_the_lines_plugin.coordinator import Coordinator
from between_the_lines_plugin.annotations import apply_annotation, preview_annotation, undo_annotation
from btl_agent.history import digest


BEFORE = 'def rank(items):\n    return items\n'
AFTER = 'def rank(items):\n    return sorted(items, key=lambda item: item["score"], reverse=True)\n'
PROMPT = "Rank higher scores first. Preserve arrival order when scores tie so the leaderboard stays stable."


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.project = self.directory / "project"
        self.project.mkdir()
        self.path = self.project / "rank.py"
        self.path.write_text(BEFORE, encoding="utf-8", newline="")
        self.coordinator = Coordinator(self.directory / "state")

    def event(self, name, **fields):
        return {"hook_event_name": name, "cwd": str(self.project), "session_id": "session-one",
                "turn_id": "turn-one", "model": "test-host-model", **fields}

    def prompt(self, **fields):
        return self.coordinator.handle_hook("UserPromptSubmit", self.event("UserPromptSubmit", **{"prompt": PROMPT, **fields}))

    def write(self, source=AFTER, path=None, tool_id="edit-one", **identity):
        target = path or self.path
        fields = {"tool_use_id": tool_id, "tool_name": "write_file", "tool_input": {"path": str(target), "content": source}, **identity}
        self.coordinator.handle_hook("PreToolUse", self.event("PreToolUse", **fields))
        target.write_text(source, encoding="utf-8", newline="")
        self.coordinator.handle_hook("PostToolUse", self.event("PostToolUse", **fields))

    def stop(self, **fields):
        return self.coordinator.handle_hook("Stop", self.event("Stop", **fields))

    def jobs(self):
        return self.coordinator.store.pending(str(self.project))

    def prepare(self):
        self.prompt()
        self.write()
        response = self.stop()
        return response, self.jobs()[0]

    def test_only_completed_verified_edits_queue_a_model_pass(self):
        self.prompt()
        self.write()
        self.assertEqual([], self.jobs())
        response = self.stop()
        self.assertEqual("block", response["decision"])
        self.assertIn("get_annotation_context", response["reason"])
        self.assertIn("preview_annotation", response["reason"])
        self.assertIn("apply_annotation", response["reason"])
        self.assertEqual(1, len(self.jobs()))
        job = self.jobs()[0]
        self.assertEqual(PROMPT, job["prompt"])
        self.assertEqual(digest(PROMPT), job["promptHash"])
        self.assertEqual("test-host-model", job["modelAtCapture"])
        self.assertNotEqual(job["eventId"], job["promptEventId"])
        self.assertEqual(AFTER, self.path.read_text())

    def test_context_contains_actual_task_and_changed_block_with_exact_evidence(self):
        _, job = self.prepare()
        context = self.coordinator.context(str(self.project), job["id"])
        self.assertEqual({"prompt": PROMPT}, context["intent"])
        self.assertEqual(AFTER.rstrip(), context["currentSource"].rstrip())
        self.assertEqual(BEFORE.rstrip(), context["previousSource"].rstrip())
        self.assertEqual(PROMPT, context["evidenceChoices"][0]["quote"])
        self.assertEqual([], context["allowedContextFiles"])

    def test_repeated_stop_is_idempotent_and_only_one_pass_is_requested(self):
        self.prepare()
        self.assertEqual({}, self.stop())
        self.assertEqual({}, self.stop(stop_hook_active=True))
        self.assertEqual(1, len(self.jobs()))

    def test_context_omits_unrelated_sibling_original_records(self):
        sibling = "\ndef other(value):\n    return value\n"
        self.path.write_text(BEFORE + sibling)
        self.prompt()
        self.write(AFTER + sibling)
        self.stop()
        job = self.jobs()[0]
        record = {"id": "sibling-original", "symbolId": "python:other", "prompt": "PRIVATE_SIBLING_REQUIREMENT",
                  "quote": "PRIVATE_SIBLING_REQUIREMENT", "status": "active", "scopeStatus": "exact"}
        with patch.object(self.coordinator.history, "requirements_for_paths", return_value=[record]):
            context = self.coordinator.context(str(self.project), job["id"])
        self.assertEqual([], context["originalContexts"])
        self.assertNotIn("knownRequirements", context)
        self.assertNotIn("PRIVATE_SIBLING_REQUIREMENT", json.dumps(context))

    def test_already_continued_stop_never_requests_another_pass(self):
        self.prompt()
        self.write()
        self.assertEqual({}, self.stop(stop_hook_active=True))
        self.assertEqual(1, len(self.jobs()))

    def test_exact_plugin_continuation_never_becomes_user_intent(self):
        response, original = self.prepare()
        event = self.event("UserPromptSubmit", prompt=response["reason"], turn_id="synthetic-turn")
        self.assertEqual({}, self.coordinator.handle_hook("UserPromptSubmit", event))
        self.write(source=AFTER + "\n", turn_id="synthetic-turn", tool_id="synthetic-edit")
        self.assertEqual({}, self.stop(turn_id="synthetic-turn", stop_hook_active=True))
        with self.coordinator.history._connection() as db:
            prompts = db.execute("SELECT prompt FROM prompts").fetchall()
        self.assertEqual([PROMPT], [row["prompt"] for row in prompts])
        self.assertEqual(original["id"], self.jobs()[0]["id"])

    def test_real_later_prompt_retains_its_own_original(self):
        response, _ = self.prepare()
        self.coordinator.handle_hook("UserPromptSubmit", self.event("UserPromptSubmit", prompt=response["reason"], turn_id="synthetic-turn"))
        new_prompt = "Clamp scores below zero before ranking."
        self.prompt(prompt=new_prompt, turn_id="turn-two")
        source = AFTER.replace('item["score"]', 'max(0, item["score"])')
        self.write(source=source, turn_id="turn-two", tool_id="edit-two")
        self.assertEqual("block", self.stop(turn_id="turn-two")["decision"])
        jobs = self.jobs()
        self.assertEqual({PROMPT, new_prompt}, {job["prompt"] for job in jobs})

    def test_a_different_session_cannot_borrow_the_last_prompt(self):
        self.prompt()
        self.write(session_id="another-session")
        self.assertEqual({}, self.stop(session_id="another-session"))
        self.assertEqual([], self.jobs())

    def test_unregistered_workspace_and_cross_project_session_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "not been registered"):
            self.coordinator.pending(str(self.project))
        self.prompt()
        other = self.directory / "other-project"
        other.mkdir()
        with self.assertRaises(ValueError):
            self.prompt(cwd=str(other), turn_id="turn-two")
        with self.assertRaisesRegex(ValueError, "not been registered"):
            self.coordinator.require_workspace(str(other))

    def test_shell_writes_are_not_guessed_from_disk_changes(self):
        self.prompt()
        fields = {"tool_use_id": "shell-one", "tool_name": "exec_command", "tool_input": {"cmd": "write rank.py"}}
        self.coordinator.handle_hook("PreToolUse", self.event("PreToolUse", **fields))
        self.path.write_text(AFTER)
        self.coordinator.handle_hook("PostToolUse", self.event("PostToolUse", **fields))
        self.assertEqual({}, self.stop())
        self.assertEqual([], self.jobs())

    def test_stale_disk_before_completion_prevents_job_creation(self):
        self.prompt()
        self.write()
        self.path.write_text(AFTER + "# changed elsewhere\n")
        self.assertEqual({}, self.stop())
        self.assertEqual([], self.jobs())

    def test_stale_disk_after_capture_prevents_context_use(self):
        _, job = self.prepare()
        self.path.write_text(AFTER + "# changed elsewhere\n")
        with self.assertRaisesRegex(ValueError, "Source changed"):
            self.coordinator.context(str(self.project), job["id"])

    def test_human_documentation_is_preserved(self):
        self.path.write_text("# Existing human explanation.\n" + BEFORE)
        self.prompt()
        self.write("# Existing human explanation.\n" + AFTER)
        self.assertEqual({}, self.stop())
        self.assertEqual([], self.jobs())

    def test_malformed_source_does_not_block_a_later_valid_file(self):
        malformed = self.project / "broken.py"
        malformed.write_text("def broken():\n    return None\n")
        self.prompt()
        self.write("def broken(:\n", path=malformed, tool_id="broken-edit")
        self.write(AFTER, tool_id="valid-edit")
        self.assertEqual("block", self.stop()["decision"])
        self.assertEqual([str(self.path)], [job["path"] for job in self.jobs()])

    def test_journal_survives_queue_failure_and_recovers_after_restart(self):
        self.prompt()
        self.write()
        with patch.object(self.coordinator.store, "save_job", side_effect=OSError("fixture write failure")):
            with self.assertRaises(OSError):
                self.stop()
        self.assertEqual(1, len(self.coordinator.history.pending_completed(str(self.project))))
        self.coordinator = Coordinator(self.directory / "state")
        self.assertEqual("block", self.stop()["decision"])
        self.assertEqual([], self.coordinator.history.pending_completed(str(self.project)))
        self.assertEqual(1, len(self.jobs()))

    def test_one_changed_declaration_per_file_bounds_the_pass(self):
        before = BEFORE + "\ndef count(values):\n    return len(values)\n"
        after = AFTER + "\ndef count(values):\n    return sum(value > 0 for value in values)\n"
        self.path.write_text(before)
        self.prompt()
        self.write(after)
        self.stop()
        self.assertEqual(1, len(self.jobs()))

    def test_java_changed_method_is_selected_instead_of_overlapping_class(self):
        source = self.project / "Rank.java"
        source.write_text("class Rank {\n    int score(int value) { return value; }\n}\n")
        self.prompt()
        self.write("class Rank {\n    int score(int value) { return Math.max(0, value); }\n}\n", path=source)
        self.stop()
        job = self.jobs()[0]
        self.assertEqual("java", job["language"])
        self.assertEqual("score", job["symbolName"])

    def test_original_job_evidence_and_terminal_states_cannot_be_rewritten(self):
        _, job = self.prepare()
        with self.assertRaisesRegex(ValueError, "evidence cannot be changed"):
            self.coordinator.store.update_job(str(self.project), job["id"], prompt="invented")
        with self.assertRaisesRegex(ValueError, "state transition"):
            self.coordinator.store.update_job(str(self.project), job["id"], state="applied")
        self.coordinator.skip(str(self.project), job["id"], "No useful supported explanation.")
        with self.assertRaisesRegex(ValueError, "state transition"):
            self.coordinator.store.update_job(str(self.project), job["id"], state="pending")
        self.assertEqual([], self.jobs())

    def test_pending_write_journal_cannot_be_discarded_by_skip(self):
        _, job = self.prepare()
        self.coordinator.store.update_job(str(self.project), job["id"], state="previewed", pendingWrite={"expected": "fixture"})
        with self.assertRaisesRegex(ValueError, "Recover the pending"):
            self.coordinator.skip(str(self.project), job["id"], "Skip it.")

    def test_same_file_jobs_share_a_nonblocking_writer_lock(self):
        _, job = self.prepare()
        other = {**job, "id": digest("another completed event"), "eventId": "another-event"}
        self.coordinator.store.save_job(other)
        with self.coordinator.store.job_lock(str(self.project), job["id"]):
            with self.assertRaisesRegex(ValueError, "already being updated"):
                with self.coordinator.store.job_lock(str(self.project), other["id"]):
                    self.fail("A second file writer acquired the same lock.")

    def test_model_cannot_retire_original_without_captured_user_authority(self):
        self.prompt()
        requirement_id = digest("existing-original")
        with patch.object(self.coordinator.history, "retire_requirement") as retire:
            with self.assertRaisesRegex(ValueError, "standalone line"):
                self.coordinator.retire(str(self.project), requirement_id)
            retire.assert_not_called()

    def test_exact_current_user_marker_authorizes_retirement(self):
        requirement_id = digest("existing-original")
        self.prompt(prompt="Retire " + requirement_id)
        with patch.object(self.coordinator.history, "retire_requirement", return_value={"id": requirement_id, "status": "retired"}) as retire:
            result = self.coordinator.retire(str(self.project), requirement_id)
            self.assertEqual("retired", result["record"]["status"])
            retire.assert_called_once_with(requirement_id, str(self.project))

    def test_old_or_embedded_retirement_marker_does_not_authorize_a_later_task(self):
        requirement_id = digest("existing-original")
        self.prompt(prompt="Retire " + requirement_id)
        self.prompt(prompt="Make the rank function faster.", turn_id="turn-two")
        with patch.object(self.coordinator.history, "retire_requirement") as retire:
            with self.assertRaisesRegex(ValueError, "standalone line"):
                self.coordinator.retire(str(self.project), requirement_id)
            retire.assert_not_called()
        self.prompt(prompt="Do not Retire " + requirement_id, turn_id="turn-three")
        with self.assertRaisesRegex(ValueError, "standalone line"):
            self.coordinator.retire(str(self.project), requirement_id)

    def recovery_preview(self):
        _, job = self.prepare()
        preview = preview_annotation(self.coordinator, str(self.project), job["id"],
                                     "Keep tied scores in arrival order so the leaderboard stays stable.",
                                     "p0", "Stable sorting retains the stated ordering constraint.")
        return job, preview

    def test_fresh_process_discovers_apply_recovery_after_history_failure(self):
        job, preview = self.recovery_preview()
        with patch.object(self.coordinator.history, "record_requirement", side_effect=OSError("fixture history failure")):
            with self.assertRaises(OSError):
                apply_annotation(self.coordinator, str(self.project), job["id"], preview["previewId"], "The task and stable sort support the explanation.")
        annotated = self.path.read_bytes()
        self.coordinator = Coordinator(self.directory / "state")
        pending = self.coordinator.pending(str(self.project))["jobs"][0]
        self.assertEqual({"action": "apply_annotation", "previewId": preview["previewId"]}, pending["recovery"])
        context = self.coordinator.context(str(self.project), job["id"])
        self.assertEqual("annotation_recovery", context["task"])
        self.assertEqual("approved_after_annotation", context["diskState"])
        self.assertEqual(PROMPT, context["intent"]["prompt"])
        self.assertNotEqual(context["approvedComment"], context["intent"]["prompt"])
        self.assertEqual(PROMPT, context["originalEvidence"][0]["quote"])
        self.assertEqual(annotated, self.path.read_bytes())
        self.assertIn("supportReason", context["recovery"])

    def test_apply_recovery_context_also_accepts_exact_approved_before_bytes(self):
        job, preview = self.recovery_preview()
        with patch("between_the_lines_plugin.annotations._replace", side_effect=OSError("fixture before-write failure")):
            with self.assertRaises(OSError):
                apply_annotation(self.coordinator, str(self.project), job["id"], preview["previewId"], "The task and stable sort support the explanation.")
        self.coordinator = Coordinator(self.directory / "state")
        context = self.coordinator.context(str(self.project), job["id"])
        self.assertEqual("approved_before_annotation", context["diskState"])
        self.assertEqual("apply_annotation", context["recovery"]["action"])
        self.assertEqual(AFTER, self.path.read_text())

    def test_pending_undo_remains_discoverable_after_write_and_state_failure(self):
        job, preview = self.recovery_preview()
        apply_annotation(self.coordinator, str(self.project), job["id"], preview["previewId"], "The task and stable sort support the explanation.")
        update = self.coordinator.store.update_job

        def fail_final_state(workspace, job_id, **updates):
            if updates.get("state") == "undone":
                raise OSError("fixture undo state failure")
            return update(workspace, job_id, **updates)

        with patch.object(self.coordinator.store, "update_job", side_effect=fail_final_state):
            with self.assertRaises(OSError):
                undo_annotation(self.coordinator, str(self.project), job["id"])
        self.coordinator = Coordinator(self.directory / "state")
        pending = self.coordinator.pending(str(self.project))["jobs"][0]
        self.assertEqual("applied", pending["state"])
        self.assertEqual({"action": "undo_annotation", "previewId": preview["previewId"]}, pending["recovery"])
        context = self.coordinator.context(str(self.project), job["id"])
        self.assertEqual("undo_annotation", context["recovery"]["action"])
        self.assertEqual("approved_before_annotation", context["diskState"])
        self.assertEqual(PROMPT, context["intent"]["prompt"])
        self.assertEqual(AFTER, self.path.read_text())

    def test_external_changes_block_recovery_context_without_rewriting_them(self):
        job, preview = self.recovery_preview()
        with patch.object(self.coordinator.history, "record_requirement", side_effect=OSError("fixture history failure")):
            with self.assertRaises(OSError):
                apply_annotation(self.coordinator, str(self.project), job["id"], preview["previewId"], "The task and stable sort support the explanation.")
        edited = self.path.read_bytes() + b"\n# A later external edit.\n"
        self.path.write_bytes(edited)
        self.coordinator = Coordinator(self.directory / "state")
        with self.assertRaisesRegex(ValueError, "External source edits"):
            self.coordinator.context(str(self.project), job["id"])
        self.assertEqual(edited, self.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
