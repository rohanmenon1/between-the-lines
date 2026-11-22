"""Provenance and edit attribution tests do not invoke a model or coding tool."""

from contextlib import closing
import io
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from btl_agent.capture import capture
from btl_agent.history import History, digest, target_paths
from btl_agent.parsing import discover
from btl_agent.types import WorkerError


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.inbox = self.root / "inbox"
        self.path = self.root / "rank.py"
        self.before = "def rank(items):\n    return items\n"
        self.after = "def rank(items):\n    return sorted(items)\n"
        self.path.write_text(self.before, encoding="utf-8", newline="")
        self.history = History(self.inbox)

    def tearDown(self):
        self.temporary.cleanup()

    def event(self, session="session-a", turn="turn-a", tool="tool-a", **extra):
        return {"session_id": session, "turn_id": turn, "tool_use_id": tool,
                "cwd": str(self.root), **extra}

    def prompt(self, text="rank must preserve the input order of ties.", **identity):
        return self.history.record_prompt(self.event(prompt=text, **identity), "codex", self.root)

    def write(self, **identity):
        return self.event(tool_name="Write", tool_input={"file_path": str(self.path), "content": self.after}, **identity)

    def requirement(self, prompt=None, quote="rank must preserve the input order of ties.", **extra):
        prompt = prompt or self.prompt()
        declaration = discover(self.path.read_text(encoding="utf-8"), "python")[0]
        return {"promptEventId": prompt["id"], "workspaceRoot": str(self.root), "path": str(self.path),
                "sessionId": prompt["sessionId"], "turnId": prompt["turnId"], "source": "codex",
                "symbolId": declaration.symbol_id, "symbolName": declaration.name,
                "codeHash": declaration.code_hash, "promptHash": digest(prompt["prompt"]),
                "quote": quote, "commentText": "# User context: " + quote, **extra}

    def synthesized_requirement(self, prompt=None, quote="Keep equal-score entries stable.", **extra):
        prompt = prompt or self.prompt(quote)
        start = len(prompt["prompt"][:prompt["prompt"].index(quote)].encode("utf-16-le")) // 2
        evidence = [{"quote": quote, "start": start, "end": start + len(quote.encode("utf-16-le")) // 2,
                     "promptHash": digest(prompt["prompt"])}]
        return self.requirement(prompt, quote, commentKind="synthesized", evidence=evidence,
                                generation={"provider": "local", "model": "test.gguf"},
                                **{"commentText": "# Sort entries without disturbing equal-score order.", **extra})

    def test_exact_session_turn_and_tool_bind_a_verified_file_result(self):
        prompt = self.prompt()
        self.history.before_tool(self.write(), "codex", self.root)
        self.path.write_text(self.after, encoding="utf-8", newline="")
        result = self.history.after_tool(self.write(), "codex", self.root)
        self.assertEqual(result["kind"], "edit")
        self.assertEqual(result["eventId"], prompt["id"])
        self.assertEqual(result["prompt"], prompt["prompt"])
        self.assertEqual(result["files"][0]["beforeSource"], self.before)
        self.assertEqual(result["files"][0]["afterHash"], digest(self.after))
        self.assertIsNone(self.history.after_tool(self.write(), "codex", self.root))

    def test_completed_bom_edit_retains_decoded_source_and_exact_disk_hash(self):
        self.path.write_bytes(self.before.encode("utf-8-sig"))
        def send(name, event):
            return capture(io.BytesIO(json.dumps(event).encode()), self.inbox, "codex", name, self.root)
        send("UserPromptSubmit", self.event(prompt="rank must preserve ties."))
        send("PreToolUse", self.write())
        self.path.write_bytes(self.after.encode("utf-8-sig"))
        send("PostToolUse", self.write())
        send("Stop", self.event())
        completed = [json.loads(path.read_text()) for path in self.inbox.glob("*.json")
                     if json.loads(path.read_text())["kind"] == "edit"]
        self.assertEqual(len(completed), 1)
        file = completed[0]["files"][0]
        self.assertEqual(file["beforeSource"], self.before)
        self.assertEqual(file["afterSource"], self.after)
        self.assertEqual(file["afterHash"], digest(self.after))
        self.assertEqual(file["beforeRawHash"], hashlib.sha256(self.before.encode("utf-8-sig")).hexdigest())
        self.assertEqual(file["afterRawHash"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertNotEqual(file["afterRawHash"], file["afterHash"])

    def test_bom_change_after_the_last_tool_rejects_the_completed_snapshot(self):
        self.prompt()
        self.history.before_tool(self.write(), "codex", self.root)
        self.path.write_bytes(self.after.encode("utf-8"))
        self.history.after_tool(self.write(), "codex", self.root)
        self.path.write_bytes(self.after.encode("utf-8-sig"))
        self.assertIsNone(self.history.finish_turn(self.event(), "codex", self.root))

    def test_external_bom_change_between_tools_breaks_the_raw_snapshot_chain(self):
        self.prompt()
        self.history.before_tool(self.write(), "codex", self.root)
        self.path.write_bytes(self.after.encode("utf-8"))
        self.history.after_tool(self.write(), "codex", self.root)
        self.path.write_bytes(self.after.encode("utf-8-sig"))
        final = self.after + "\ndef other():\n    return 1\n"
        second = self.event(tool="tool-b", tool_name="Write", tool_input={"file_path": str(self.path), "content": final})
        self.history.before_tool(second, "codex", self.root)
        self.path.write_bytes(final.encode("utf-8-sig"))
        self.history.after_tool(second, "codex", self.root)
        self.assertIsNone(self.history.finish_turn(self.event(), "codex", self.root))

    def test_immutable_prompt_cannot_be_replaced_in_the_same_turn(self):
        first = self.prompt()
        self.assertEqual(self.prompt()["id"], first["id"])
        with self.assertRaises(ValueError):
            self.prompt("rank may reorder ties.")

    def test_wrong_turn_and_post_without_pre_cannot_borrow_the_latest_prompt(self):
        self.prompt()
        self.history.before_tool(self.write(), "codex", self.root)
        self.path.write_text(self.after, encoding="utf-8", newline="")
        self.assertIsNone(self.history.after_tool(self.write(turn="other-turn"), "codex", self.root))
        self.assertIsNone(self.history.after_tool(self.write(tool="other-tool"), "codex", self.root))

    def test_tool_without_an_original_prompt_is_not_associated(self):
        self.history.before_tool(self.write(), "codex", self.root)
        self.path.write_text(self.after, encoding="utf-8", newline="")
        self.assertIsNone(self.history.after_tool(self.write(), "codex", self.root))

    def test_missing_turn_is_capture_only(self):
        self.history.record_prompt({"session_id": "one", "cwd": str(self.root), "prompt": "rank must stay stable."}, "codex")
        event = self.write()
        del event["turn_id"]
        self.history.before_tool(event, "codex", self.root)
        self.path.write_text(self.after, encoding="utf-8", newline="")
        self.assertIsNone(self.history.after_tool(event, "codex", self.root))

    def test_unexpected_other_writer_changes_cannot_be_attributed_to_the_tool(self):
        self.prompt()
        self.history.before_tool(self.write(), "codex", self.root)
        self.path.write_text(self.after + "# another writer\n", encoding="utf-8", newline="")
        self.assertIsNone(self.history.after_tool(self.write(), "codex", self.root))

    def test_overlapping_sessions_are_both_rejected_even_if_results_match(self):
        self.prompt()
        self.prompt("rank must return sorted values.", session="session-b", turn="turn-b")
        self.history.before_tool(self.write(), "codex", self.root)
        second = self.write(session="session-b", turn="turn-b", tool="tool-b")
        self.history.before_tool(second, "codex", self.root)
        self.path.write_text(self.after, encoding="utf-8", newline="")
        self.assertIsNone(self.history.after_tool(self.write(), "codex", self.root))
        self.assertIsNone(self.history.after_tool(second, "codex", self.root))

    def test_exact_apply_patch_is_verified_without_executing_it(self):
        self.prompt()
        patch = "*** Begin Patch\n*** Update File: rank.py\n@@\n def rank(items):\n-    return items\n+    return sorted(items)\n*** End Patch"
        event = self.event(tool_name="apply_patch", tool_input={"command": patch})
        self.history.before_tool(event, "codex", self.root)
        self.path.write_text(self.after, encoding="utf-8", newline="")
        self.assertEqual(self.history.after_tool(event, "codex", self.root)["files"][0]["afterSource"], self.after)

    def test_ambiguous_patch_hunk_is_not_guessed(self):
        self.prompt()
        self.path.write_text("def rank(items):\n    pass\n    pass\n", encoding="utf-8", newline="")
        patch = "*** Begin Patch\n*** Update File: rank.py\n@@\n-    pass\n+    return items\n*** End Patch"
        event = self.event(tool_name="apply_patch", tool_input={"command": patch})
        self.history.before_tool(event, "codex", self.root)
        self.path.write_text("def rank(items):\n    return items\n    pass\n", encoding="utf-8", newline="")
        self.assertIsNone(self.history.after_tool(event, "codex", self.root))

    def test_shell_commands_and_outside_paths_are_not_interpreted(self):
        self.prompt()
        shell = self.event(tool_name="Bash", tool_input={"command": "echo unsafe > rank.py"})
        self.assertEqual(target_paths(shell, self.root), [])
        outside = self.event(tool_name="Write", tool_input={"file_path": str(self.root.parent / "outside.py"), "content": self.after})
        self.assertEqual(target_paths(outside, self.root), [])

    def test_requirement_checks_original_text_identity_scope_and_current_code(self):
        original = self.requirement()
        for change in ({"quote": "rank may reorder ties."}, {"sessionId": "someone-else"}, {"codeHash": "0" * 64}, {"promptHash": "0" * 64}):
            with self.subTest(change=change), self.assertRaises((ValueError, WorkerError)):
                self.history.record_requirement({**original, **change})
        saved = self.history.record_requirement(original)
        self.assertEqual(self.history.record_requirement(original)["id"], saved["id"])

    def test_synthesized_comment_is_stored_separately_from_original_evidence(self):
        saved = self.history.record_requirement(self.synthesized_requirement())
        self.assertEqual(saved["commentKind"], "synthesized")
        self.assertEqual(saved["quote"], "Keep equal-score entries stable.")
        self.assertNotIn(saved["quote"], saved["commentText"])
        context = self.history.context_for_paths([str(self.path)], str(self.root))
        self.assertIn(saved["quote"], context)
        self.assertNotIn(saved["commentText"], context)
        self.assertEqual(saved["generation"], {"provider": "local", "model": "test.gguf"})

    def test_multiline_conditions_and_utf16_evidence_survive_reinjection(self):
        quote = "Do not reorder tied entries\nunless reverse=True was explicitly requested."
        original = self.prompt("😀\n" + quote)
        saved = self.history.record_requirement(self.synthesized_requirement(original, quote))
        self.assertEqual(saved["evidence"][0]["start"], 3)
        context = self.history.context_for_paths([str(self.path)], str(self.root))
        self.assertIn("Do not reorder tied entries", context)
        self.assertIn("unless reverse=True was explicitly requested.", context)

    def test_synthesized_records_reject_fabricated_spans_unsafe_text_and_missing_model(self):
        original = self.synthesized_requirement()
        invalid = [{"evidence": [{**original["evidence"][0], "quote": "Drop tied entries."}]},
                   {"evidence": [{**original["evidence"][0], "start": 1}]},
                   {"evidence": [{**original["evidence"][0], "promptHash": "0" * 64}]},
                   {"commentText": "# noqa: F401"}, {"commentText": "# first\nsecond"},
                   {"generation": {"provider": "local", "model": ""}}]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises((ValueError, WorkerError)):
                self.history.record_requirement({**original, **change})

    def test_generated_revisions_do_not_replace_original_requirements(self):
        original = self.synthesized_requirement()
        first = self.history.record_requirement(original)
        second = self.history.record_requirement({**original, "commentText": "# Preserve stable ties while sorting entries."})
        self.assertNotEqual(first["id"], second["id"])
        active = self.history.requirements_for_paths([str(self.path)], str(self.root))
        self.assertEqual({record["id"] for record in active}, {first["id"], second["id"]})
        self.assertTrue(all(record["prompt"] == first["prompt"] for record in active))

    def test_synthesized_supersession_and_retirement_keep_original_rows(self):
        first = self.history.record_requirement(self.synthesized_requirement())
        quote = "Reverse equal-score entries under the revised ordering contract."
        prompt = self.prompt("Supersede " + first["id"] + "\n" + quote, turn="replacement-turn")
        second = self.history.record_requirement(self.synthesized_requirement(
            prompt, quote, commentText="# Reverse tie order under the revised contract.", supersedes=[first["id"]]))
        active = self.history.requirements_for_paths([str(self.path)], str(self.root))
        self.assertEqual([record["id"] for record in active], [second["id"]])
        self.history.retire_requirement(second["id"], str(self.root))
        self.assertEqual(self.history.requirements_for_paths([str(self.path)], str(self.root)), [])
        with closing(sqlite3.connect(self.history.database)) as db:
            rows = db.execute("SELECT status,record_json FROM requirements ORDER BY timestamp").fetchall()
            self.assertEqual([row[0] for row in rows], ["superseded", "retired"])
            self.assertEqual(json.loads(rows[0][1])["prompt"], first["prompt"])
            self.assertEqual(db.execute("SELECT count(*) FROM prompts").fetchone()[0], 2)

    def test_provider_failure_and_unchanged_pass_preserve_original_requirements(self):
        from btl_agent.agent import Agent

        class FailingProvider:
            def generate(self, *args):
                raise WorkerError("provider_unavailable", "The configured provider is unavailable.")

        first = self.history.record_requirement(self.synthesized_requirement())
        previous = first["commentText"] + "\n" + self.before
        source = first["commentText"] + "\n" + self.after
        self.path.write_text(source, encoding="utf-8", newline="")
        known = self.history.requirements_for_paths([str(self.path)], str(self.root))
        request = {"uri": self.path.as_uri(), "language": "python", "source": source,
                   "previousSource": previous, "knownRequirements": known,
                   "intent": {"source": "codex", "prompt": "Optimize the ranking implementation.",
                              "eventId": "current-event", "sessionId": "current-session", "turnId": "current-turn"}}
        agent = Agent(FailingProvider())
        with self.assertRaises(WorkerError) as failure:
            agent.annotate_observed(request)
        self.assertEqual(failure.exception.code, "provider_unavailable")
        self.assertEqual(self.path.read_text(encoding="utf-8"), source)
        unchanged = agent.annotate_observed({**request, "previousSource": source})
        self.assertEqual(unchanged["metrics"]["modelCalls"], 0)
        self.assertEqual(unchanged["requirements"], [])
        active = self.history.requirements_for_paths([str(self.path)], str(self.root))
        self.assertEqual([record["id"] for record in active], [first["id"]])
        context = self.history.context_for_paths([str(self.path)], str(self.root))
        self.assertIn(first["quote"], context)
        self.assertNotIn(first["commentText"], context)

    def test_context_budget_reports_omitted_originals_without_clipping_them(self):
        lines = ["Constraint " + str(index) + ": " + "x" * 980 for index in range(8)]
        prompt = self.prompt("\n".join(lines))
        record = self.synthesized_requirement(prompt, lines[0])
        position = 0
        record["evidence"] = []
        for line in lines:
            record["evidence"].append({"quote": line, "start": position, "end": position + len(line),
                                       "promptHash": digest(prompt["prompt"])})
            position += len(line) + 1
        # Keep total evidence within its separate 8000-character limit.
        self.history.record_requirement(record)
        context = self.history.context_for_paths([str(self.path)], str(self.root))
        self.assertLessEqual(len(context), 8000)
        self.assertIn("did not fit", context)
        self.assertNotIn(lines[0], context)

    def test_more_than_one_hundred_originals_remain_visible_and_omissions_are_counted(self):
        original = self.synthesized_requirement()
        for index in range(101):
            self.history.record_requirement({**original, "commentText": f"# Preserve stable score ordering, revision {index}."})
        for paths in ([str(self.path)], []):
            with self.subTest(paths=paths):
                self.assertEqual(len(self.history.requirements_for_paths(paths, str(self.root))), 101)
                context = self.history.context_for_paths(paths, str(self.root))
                shown = context.count('"requirementId"')
                self.assertGreater(shown, 0)
                self.assertLess(shown, 101)
                self.assertIn(f"{101 - shown} original requirement records did not fit", context)
                self.assertLessEqual(len(context), 8000)
        from btl_agent.synthesis import original_contexts
        with self.assertRaises(WorkerError) as failure:
            original_contexts({"source": self.before, "language": "python",
                               "knownRequirements": self.history.requirements_for_paths([str(self.path)], str(self.root))})
        self.assertEqual(failure.exception.code, "context_limit")
        with closing(sqlite3.connect(self.history.database)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM requirements WHERE status='active'").fetchone()[0], 101)

    def test_restarting_and_body_changes_retain_original_context(self):
        saved = self.history.record_requirement(self.requirement())
        self.path.write_text(self.after, encoding="utf-8", newline="")
        restored = History(self.inbox)
        records = restored.requirements_for_paths([str(self.path)], str(self.root))
        self.assertEqual(records[0]["id"], saved["id"])
        self.assertEqual(records[0]["scopeStatus"], "changed")
        self.assertIn(saved["quote"], restored.context_for_paths([str(self.path)], str(self.root)))

    def test_renames_do_not_silently_move_a_requirement_to_another_symbol(self):
        self.history.record_requirement(self.requirement())
        self.path.write_text(self.before.replace("rank", "sort_items"), encoding="utf-8", newline="")
        records = self.history.requirements_for_paths([str(self.path)], str(self.root))
        self.assertEqual(records[0]["symbolName"], "rank")
        self.assertEqual(records[0]["scopeStatus"], "missing")

    def test_explicit_retirement_and_supersession_preserve_original_rows(self):
        saved = self.history.record_requirement(self.requirement())
        quote = "rank must sort ties by their timestamp."
        new_prompt = self.prompt("Supersede " + saved["id"] + "\n" + quote, turn="turn-b")
        replacement = self.history.record_requirement(self.requirement(new_prompt, quote, supersedes=[saved["id"]]))
        self.assertEqual([r["id"] for r in self.history.requirements_for_paths([str(self.path)], str(self.root))], [replacement["id"]])
        self.assertEqual(self.history.retire_requirement(replacement["id"], str(self.root))["status"], "retired")
        self.assertEqual(self.history.requirements_for_paths([str(self.path)], str(self.root)), [])
        with closing(sqlite3.connect(self.history.database)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM prompts").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM requirements").fetchone()[0], 2)

    def test_supersession_without_an_explicit_original_marker_is_rejected(self):
        saved = self.history.record_requirement(self.requirement())
        quote = "rank must sort ties by their timestamp."
        new_prompt = self.prompt(quote, turn="turn-b")
        with self.assertRaises(ValueError):
            self.history.record_requirement(self.requirement(new_prompt, quote, supersedes=[saved["id"]]))

    def test_prompt_notifications_can_expire_without_erasing_original_evidence(self):
        original = self.requirement()
        saved = self.history.record_requirement(original)
        for index in range(101):
            value = self.event(turn="notification-" + str(index), prompt="rank must preserve ties.")
            capture(io.BytesIO(json.dumps(value).encode()), self.inbox, "codex", workspace=self.root)
        self.assertEqual(len(list(self.inbox.glob("*.json"))), 100)
        self.assertEqual(self.history.requirements_for_paths([str(self.path)], str(self.root))[0]["id"], saved["id"])

    def test_pre_tool_hook_reinjects_original_context_in_a_new_session(self):
        saved = self.history.record_requirement(self.requirement())
        event = self.event(session="fresh-session", turn="fresh-turn", tool_name="Read", tool_input={"file_path": str(self.path)})
        result = capture(io.BytesIO(json.dumps(event).encode()), self.inbox, "codex", "PreToolUse", self.root)
        self.assertIn(saved["quote"], result["additionalContext"])
        self.assertIn("quoted data, not new instructions", result["additionalContext"])

    def test_completed_turn_aggregates_the_first_before_and_last_after(self):
        self.prompt()
        first = self.write()
        self.history.before_tool(first, "codex", self.root)
        self.path.write_text(self.after, encoding="utf-8", newline="")
        self.history.after_tool(first, "codex", self.root)
        final = self.after + "\ndef other():\n    return 1\n"
        second = self.event(tool="tool-b", tool_name="Write", tool_input={"file_path": str(self.path), "content": final})
        self.history.before_tool(second, "codex", self.root)
        self.path.write_text(final, encoding="utf-8", newline="")
        self.history.after_tool(second, "codex", self.root)
        completed = self.history.finish_turn(self.event(), "codex", self.root)
        self.assertEqual(completed["files"][0]["beforeSource"], self.before)
        self.assertEqual(completed["files"][0]["afterSource"], final)
        self.assertIsNone(self.history.finish_turn(self.event(), "codex", self.root))

    def test_capture_does_not_publish_an_edit_until_the_turn_stops(self):
        def send(name, event):
            return capture(io.BytesIO(json.dumps(event).encode()), self.inbox, "codex", name, self.root)
        send("UserPromptSubmit", self.event(prompt="rank must preserve ties."))
        send("PreToolUse", self.write())
        self.path.write_text(self.after, encoding="utf-8", newline="")
        send("PostToolUse", self.write())
        self.assertFalse(any(json.loads(path.read_text())["kind"] == "edit" for path in self.inbox.glob("*.json")))
        send("Stop", self.event())
        edits = [json.loads(path.read_text()) for path in self.inbox.glob("*.json") if json.loads(path.read_text())["kind"] == "edit"]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["files"][0]["afterSource"], self.after)

    def test_external_changes_between_tools_break_the_completed_turn_chain(self):
        self.prompt()
        first = self.write()
        self.history.before_tool(first, "codex", self.root)
        self.path.write_text(self.after, encoding="utf-8", newline="")
        self.history.after_tool(first, "codex", self.root)
        unexpected = self.after + "# another writer\n"
        self.path.write_text(unexpected, encoding="utf-8", newline="")
        second = self.event(tool="tool-b", tool_name="Write", tool_input={"file_path": str(self.path), "content": unexpected + "# final\n"})
        self.history.before_tool(second, "codex", self.root)
        self.path.write_text(unexpected + "# final\n", encoding="utf-8", newline="")
        self.history.after_tool(second, "codex", self.root)
        self.assertIsNone(self.history.finish_turn(self.event(), "codex", self.root))

    def test_changes_after_the_last_tool_are_rejected_when_the_turn_stops(self):
        self.prompt()
        self.history.before_tool(self.write(), "codex", self.root)
        self.path.write_text(self.after, encoding="utf-8", newline="")
        self.history.after_tool(self.write(), "codex", self.root)
        self.path.write_text(self.after + "# changed after tool\n", encoding="utf-8", newline="")
        self.assertIsNone(self.history.finish_turn(self.event(), "codex", self.root))

    def test_failed_publication_is_retried_without_losing_originals(self):
        self.prompt()
        self.history.before_tool(self.write(), "codex", self.root)
        self.path.write_text(self.after, encoding="utf-8", newline="")
        self.history.after_tool(self.write(), "codex", self.root)
        with patch("btl_agent.capture._publish", side_effect=OSError("interrupted publication")):
            with self.assertRaises(OSError):
                capture(io.BytesIO(json.dumps(self.event()).encode()), self.inbox, "codex", "Stop", self.root)
        pending = self.history.pending_completed(str(self.root))
        self.assertEqual(len(pending), 1)
        with closing(sqlite3.connect(self.history.database)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM verified_edits").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT before_json FROM tools").fetchone()[0], "[]")
            self.assertEqual(db.execute("SELECT count(*) FROM prompts").fetchone()[0], 1)
        fresh = self.event(session="next-session", turn="next-turn", prompt="rank must preserve ties.")
        capture(io.BytesIO(json.dumps(fresh).encode()), self.inbox, "codex", "UserPromptSubmit", self.root)
        self.assertEqual(self.history.pending_completed(str(self.root)), [])
        recovered = json.loads((self.inbox / (pending[0]["id"] + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(recovered, pending[0])
