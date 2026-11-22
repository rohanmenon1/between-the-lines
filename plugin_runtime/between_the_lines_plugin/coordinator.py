"""Associate completed Codex edits with original intent and one annotation pass."""

from collections import Counter
import json
from pathlib import Path
import re

from btl_agent.history import History, digest, identity, read_source, timestamp, workspace_path
from btl_agent.parsing import changed_declarations, discover
from btl_agent.patches import preceding_comment
from btl_agent.synthesis import generation_payload
from btl_agent.types import WorkerError

from .store import Store


def _qualified(declaration):
    name = re.sub(r"\([a-f0-9]{12}\)", "", declaration.symbol_id)
    return re.sub(r"#\d+$", "", name)


def _recovery(job):
    write, undo = job.get("pendingWrite"), job.get("pendingUndo")
    if not write and not undo:
        return None
    if write and undo:
        raise ValueError("The annotation contains conflicting recovery journals.")
    journal = write or undo
    preview = job.get("preview")
    if (not isinstance(journal, dict) or not isinstance(preview, dict)
            or not isinstance(preview.get("id"), str) or journal.get("previewId") != preview["id"]
            or (write and job["state"] != "previewed") or (undo and job["state"] != "applied")):
        raise ValueError("The unfinished annotation needs its original approved preview.")
    return {"action": "apply_annotation" if write else "undo_annotation", "previewId": preview["id"]}


class Coordinator:
    def __init__(self, data_dir=None):
        self.store = Store(data_dir)
        self.data_dir = self.store.data_dir
        self.history = History(self.data_dir / "intents")

    def require_workspace(self, workspace):
        return self.store.approved_workspace(workspace)

    @staticmethod
    def _hook_context(name, context):
        return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": context}} if context else {}

    def handle_hook(self, event_name, event):
        """Entrypoint catches failures; errors never authorize source mutations."""
        if not isinstance(event, dict) or event_name not in {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}:
            raise ValueError("Unsupported plugin hook event.")
        if event.get("hook_event_name", event_name) != event_name:
            raise ValueError("Hook event does not match its installed handler.")
        session_id, turn_id, _ = identity(event)
        if not session_id or not turn_id:
            return {}
        session = self.store.session(session_id)
        root = workspace_path(event, session["workspace"] if session else None)
        if event_name == "UserPromptSubmit":
            prompt = event.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
                raise ValueError("The original task is missing or excessive.")
            if session and self.store.mark_synthetic_prompt(session_id, prompt, turn_id):
                return {}
            self.history.record_prompt(event, "codex", root)
            self.store.register_prompt(session_id, str(root), turn_id)
            return self._hook_context(event_name, self.history.context_for_paths([], str(root)))
        if not session or session["turn"] != turn_id or session["synthetic_turn"] == turn_id:
            return {}
        if event_name == "PreToolUse":
            paths = self.history.before_tool(event, "codex", root)
            context = self.history.context_for_paths(paths, str(root)) if paths else ""
            return self._hook_context(event_name, context)
        if event_name == "PostToolUse":
            self.history.after_tool(event, "codex", root)
            return {}
        self.history.finish_turn(event, "codex", root)
        current_jobs = []
        for completed in self.history.pending_completed(str(root)):
            jobs = self._enqueue(completed, event.get("model"))
            # Publication is acknowledged only after durable jobs exist. Failed
            # queue writes leave the original outbox available on the next Stop.
            self.history.acknowledge_completed(completed["id"])
            current_jobs.extend(job for job in jobs if job["sessionId"] == session_id and job["turnId"] == turn_id
                                and job["state"] in {"pending", "previewed"})
        if event.get("stop_hook_active") is True or not current_jobs:
            return {}
        job_ids = [job["id"] for job in current_jobs]
        reason = (
            "between-the-lines annotation pass for verified completed edits. "
            "This is plugin continuation context, not a new original user requirement. "
            f"Workspace: {json.dumps(str(root))}. Job IDs: {json.dumps(job_ids)}. "
            "For each job, call get_annotation_context, then synthesize a short readable comment from its ORIGINAL task and changed code. "
            "Keep the original evidence separate; never paste the task as the comment or invent a reason. "
            "Call preview_annotation with exact evidence passage IDs. Review the returned source and explanation, then use apply_annotation "
            "only if every claim and condition is supported. Otherwise call skip_annotation with a short reason. "
            "Use only the plugin tools for annotation writes; do not apply source patches or alter executable code for this pass. "
            "A support judgment is fallible; AST checks prove structure only. Complete one bounded pass and stop."
        )
        if self.store.issue_continuation(session_id, turn_id, reason):
            return {"decision": "block", "reason": reason}
        return {}

    def _enqueue(self, completed, model):
        root = self.require_workspace(completed["workspaceRoot"])
        jobs = []
        for item in completed["files"][:5]:
            job = None
            try:
                language = {".py": "python", ".java": "java"}[Path(item["path"]).suffix.lower()]
                if read_source(Path(root), item["path"]) != item["afterSource"]:
                    continue
                current = discover(item["afterSource"], language)
                previous = discover(item["beforeSource"], language)
                candidates = changed_declarations(current, previous)
                counts = Counter(_qualified(declaration) for declaration in current)
                records = self.history.requirements_for_paths([item["path"]], root)
                owned = [{key: record.get(key, "") for key in ("symbolId", "commentText", "codeHash")}
                         for record in records if record.get("scopeStatus") != "missing"]
                for declaration in candidates:
                    if counts[_qualified(declaration)] > 1 or preceding_comment(item["afterSource"], declaration, owned)[0] == "human":
                        continue
                    scope_records = [record for record in records if record["symbolId"] == declaration.symbol_id]
                    old = next((value for value in previous if value.symbol_id == declaration.symbol_id), None)
                    removed_manually = (scope_records and preceding_comment(item["afterSource"], declaration, owned)[0] == "none"
                                        and not (old and preceding_comment(item["beforeSource"], old, owned)[0] == "owned"))
                    superseded = {line.strip()[len("Supersede "):] for line in completed["prompt"].splitlines()
                                  if line.strip().startswith("Supersede ")}
                    if removed_manually and any(record["id"] not in superseded for record in scope_records):
                        continue
                    job_id = digest(json.dumps([completed["id"], item["path"], declaration.symbol_id]))
                    job = {"id": job_id, "workspaceRoot": root, "eventId": completed["id"],
                           "promptEventId": completed["eventId"], "source": "codex",
                           "sessionId": completed["sessionId"], "turnId": completed["turnId"],
                           "prompt": completed["prompt"], "promptHash": digest(completed["prompt"]),
                           "modelAtCapture": model if isinstance(model, str) and 0 < len(model) <= 200 else "unknown",
                           "path": item["path"], "language": language, "beforeSource": item["beforeSource"],
                           "afterSource": item["afterSource"], "afterHash": item["afterHash"],
                           "symbolId": declaration.symbol_id, "symbolName": declaration.name,
                           "codeHash": declaration.code_hash, "state": "pending", "created": timestamp()}
                    break
            except (OSError, UnicodeError, ValueError, KeyError, SyntaxError, WorkerError):
                # One malformed/unavailable file cannot suppress a valid later
                # file from the same verified completed transaction.
                continue
            if job is not None:
                # Do not swallow a database failure as a malformed source file:
                # the original completed-event outbox must survive that failure.
                jobs.append(self.store.save_job(job))
        return jobs

    def pending(self, workspace):
        root = self.require_workspace(workspace)
        jobs = self.store.pending(root)
        summaries = []
        for job in jobs:
            item = {key: job[key] for key in ("id", "path", "symbolId", "state", "created")}
            recovery = _recovery(job)
            if recovery:
                item["recovery"] = recovery
            summaries.append(item)
        return {"workspaceRoot": root, "jobs": summaries, "limit": 30}

    def context(self, workspace, job_id):
        root = self.require_workspace(workspace)
        job = self.store.get_job(root, job_id)
        if _recovery(job):
            return self._recovery_context(root, job_id)
        if job["state"] not in {"pending", "previewed"}:
            raise ValueError("This annotation job is already finished.")
        current = read_source(Path(root), job["path"])
        if digest(current) != job["afterHash"] or current != job["afterSource"]:
            raise ValueError("Source changed after capture; no annotation should be applied.")
        declaration = next((item for item in discover(current, job["language"]) if item.symbol_id == job["symbolId"]), None)
        if declaration is None or declaration.code_hash != job["codeHash"]:
            raise ValueError("The captured declaration changed.")
        records = self.history.requirements_for_paths([job["path"]], root)
        params = {"language": job["language"], "source": current, "previousSource": job["beforeSource"],
                  "intent": {"prompt": job["prompt"]}, "knownRequirements": records}
        payload = generation_payload(params, declaration)
        return {**payload, "jobId": job_id, "workspaceRoot": root, "path": job["path"],
                "afterHash": job["afterHash"], "modelAtCapture": job["modelAtCapture"],
                "allowedContextFiles": [],
                "notice": "Generate prose from the actual task and code. Evidence proves source wording, not semantic truth."}

    def _recovery_context(self, workspace, job_id):
        """Expose an approved retry, never a new task or regenerated proposal."""
        from .annotations import _check_original, _job, _snapshot, _verified_preview

        with self.store.job_lock(workspace, job_id):
            root, path, job = _job(self, workspace, job_id)
            recovery = _recovery(job)
            if recovery is None:
                raise ValueError("The annotation no longer needs recovery; read its current state again.")
            _check_original(self, job)
            preview, _ = _verified_preview(self, root, path, job, recovery["previewId"], check_context=False)
            current = _snapshot(root, path)
            if current["rawHash"] == preview["rawBeforeHash"]:
                disk_state = "approved_before_annotation"
            elif current["rawHash"] == preview["rawAfterHash"]:
                disk_state = "approved_after_annotation"
            else:
                raise ValueError("External source edits prevent recovery; the changed file was preserved.")
            if recovery["action"] == "apply_annotation":
                recovery["supportReason"] = job["pendingWrite"]["record"]["supportReview"]["reason"]
            declaration = next(item for item in discover(job["afterSource"], job["language"])
                               if item.symbol_id == job["symbolId"])
            return {"task": "annotation_recovery", "jobId": job_id, "workspaceRoot": str(root),
                    "path": str(path), "symbol": job["symbolId"], "language": job["language"],
                    "intent": {"prompt": job["prompt"]}, "currentSource": declaration.source,
                    "sourceSnapshot": "captured_after_edit", "diskState": disk_state,
                    "approvedComment": preview["comment"], "originalEvidence": preview["evidence"],
                    "recovery": recovery,
                    "notice": "Recover the saved approved operation using this action and preview ID. Do not generate a new comment, replace the preview, or treat generated wording as original user intent. The write/history journal retains the earlier review."}

    def skip(self, workspace, job_id, reason):
        root = self.require_workspace(workspace)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
            raise ValueError("A skip needs a short nonempty reason.")
        with self.store.job_lock(root, job_id):
            job = self.store.get_job(root, job_id)
            if job.get("pendingWrite") or job.get("pendingUndo"):
                raise ValueError("Recover the pending annotation write before skipping this job.")
            if job["state"] == "skipped":
                return {"jobId": job_id, "state": "skipped"}
            if job["state"] not in {"pending", "previewed"}:
                raise ValueError("The annotation job is already finished.")
            self.store.update_job(root, job_id, state="skipped", skipReason=reason, finished=timestamp())
        return {"jobId": job_id, "state": "skipped"}

    def retire(self, workspace, requirement_id):
        """A model cannot retire an original merely because its code conflicts."""
        root = self.require_workspace(workspace)
        if not isinstance(requirement_id, str) or not re.fullmatch(r"[a-f0-9]{64}", requirement_id):
            raise ValueError("Use an existing requirement ID from context_history.")
        with self.history._connection() as db:
            latest = db.execute("SELECT prompt FROM prompts WHERE workspace=? AND source='codex' ORDER BY rowid DESC LIMIT 1", (root,)).fetchone()
        marker = "Retire " + requirement_id
        if latest is None or marker not in {line.strip() for line in latest["prompt"].splitlines()}:
            raise ValueError("Retirement needs the current captured user request to contain this standalone line: " + marker)
        return {"record": self.history.retire_requirement(requirement_id, root)}
