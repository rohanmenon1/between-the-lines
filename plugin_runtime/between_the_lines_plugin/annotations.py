"""Preview, apply and undo comments supplied by the host Codex model.

This module never calls a model or executes project code. Evidence, syntax and
disk checks are mechanical; the host's semantic review remains a judgment.
"""

import codecs
import difflib
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unicodedata

from btl_agent.history import MAX_SOURCE_BYTES, _opened_path
from btl_agent.parsing import discover, fingerprint
from btl_agent.patches import apply_suggestions, make_suggestion
from btl_agent.synthesis import validate_decision
from btl_agent.types import WorkerError, digest, validate_generation


def _bytes_hash(value):
    return hashlib.sha256(value).hexdigest()


def _json_hash(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=True))


def _text(value, label, limit=300):
    if (not isinstance(value, str) or not value.strip() or value != value.strip()
            or len(value) > limit or any(unicodedata.category(char).startswith("C") for char in value)):
        raise WorkerError("invalid_request", f"Provide a short, nonempty {label}.")
    return value


def _job(coordinator, workspace, job_id):
    root = Path(coordinator.require_workspace(workspace))
    job = coordinator.store.get_job(str(root), job_id)
    if not isinstance(job, dict) or Path(job["workspaceRoot"]) != root:
        raise WorkerError("unobserved_edit", "The annotation job is unavailable in this project.")
    path = Path(job["path"])
    if (not path.is_absolute() or path != path.resolve(strict=True) or path.is_symlink()
            or not path.is_relative_to(root) or {".py": "python", ".java": "java"}.get(path.suffix.lower()) != job["language"]):
        raise WorkerError("stale_source", "The observed source path has moved or is unsupported.")
    return root, path, job


def _snapshot(root, path):
    """Resolve the opened handle as well as its name before trusting source bytes."""
    if path.is_symlink() or path.resolve(strict=True) != path or not path.is_relative_to(root):
        raise WorkerError("stale_source", "The observed source path has moved.")
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or _opened_path(stream) != path:
            raise WorkerError("stale_source", "The opened source differs from the observed file.")
        raw = stream.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise WorkerError("source_limit", "The source exceeds the annotation limit.")
    source = raw.decode("utf-8-sig")
    version = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, stat.S_IMODE(info.st_mode)]
    return {"source": source, "rawHash": _bytes_hash(raw), "version": version,
            "bom": raw.startswith(codecs.BOM_UTF8), "mode": stat.S_IMODE(info.st_mode)}


def _encoded(source, bom):
    return (codecs.BOM_UTF8 if bom else b"") + source.encode("utf-8")


def _records(coordinator, root, path):
    return coordinator.history.requirements_for_paths([str(path)], str(root))


def _owned(records):
    return [{key: record.get(key, "") for key in ("symbolId", "commentText", "codeHash")}
            for record in records if record.get("status", "active") == "active"
            and record.get("scopeStatus") != "missing"]


def _context_hash(records):
    return _json_hash(sorted(records, key=lambda item: item["id"]))


def _declaration(job):
    candidates = [item for item in discover(job["afterSource"], job["language"])
                  if item.symbol_id == job["symbolId"] and item.code_hash == job["codeHash"]]
    if len(candidates) != 1 or digest(job["afterSource"]) != job["afterHash"]:
        raise WorkerError("stale_source", "The declaration differs from the captured edit.")
    return candidates[0]


def _proposal(coordinator, root, path, job, comment, passage_id, reason, owned=None):
    generated = validate_decision({"action": "synthesize_comment", "comment": comment,
                                   "promptEvidence": {"passageId": passage_id}, "reason": reason}, job["prompt"])
    declaration = _declaration(job)
    records = _records(coordinator, root, path)
    suggestion = make_suggestion(job["afterSource"], job["language"], declaration,
                                 generated["comment"], generated["reason"], [], _owned(records) if owned is None else owned)
    if suggestion is None:
        raise WorkerError("unchanged_annotation", "The owned annotation already has this wording.")
    annotated = apply_suggestions(job["afterSource"], job["language"], [suggestion])
    return generated, suggestion, annotated, records


def preview_annotation(coordinator, workspace, job_id, comment, passage_id, reason):
    """Persist a source-bound preview; it gives no permission to change code."""
    with coordinator.store.job_lock(workspace, job_id):
        root, path, job = _job(coordinator, workspace, job_id)
        if job["state"] not in {"pending", "previewed"} or job.get("pendingWrite"):
            raise WorkerError("annotation_state", "This job cannot receive a new preview; recover an unfinished apply first.")
        current = _snapshot(root, path)
        if current["source"] != job["afterSource"] or digest(current["source"]) != job["afterHash"]:
            raise WorkerError("stale_source", "Source changed after the coding turn; no annotation was previewed.")
        generated, suggestion, annotated, records = _proposal(coordinator, root, path, job, comment, passage_id, reason)
        if len(_encoded(annotated, current["bom"])) > MAX_SOURCE_BYTES:
            raise WorkerError("source_limit", "The annotated file would exceed the source limit.")
        preview = {"jobId": job_id, "source": job["afterSource"], "annotatedSource": annotated,
                   "rawBeforeHash": current["rawHash"], "rawAfterHash": _bytes_hash(_encoded(annotated, current["bom"])),
                   "beforeVersion": current["version"], "bom": current["bom"], "mode": current["mode"],
                   "comment": generated["comment"], "passageId": passage_id, "reason": generated["reason"],
                   "evidence": generated["evidence"], "suggestion": suggestion,
                   "ownership": [{"requirementId": record["id"], **{key: record[key] for key in ("symbolId", "commentText", "codeHash")}}
                                 for record in records if record["symbolId"] == job["symbolId"]],
                   "originalContextHash": _context_hash(records)}
        preview["id"] = _json_hash(preview)
        coordinator.store.update_job(str(root), job_id, state="previewed", preview=preview)
        relative = path.relative_to(root).as_posix()
        diff = "".join(difflib.unified_diff(job["afterSource"].splitlines(keepends=True), annotated.splitlines(keepends=True),
                                            fromfile="before/" + relative, tofile="after/" + relative))
        return {"jobId": job_id, "previewId": preview["id"], "diff": diff,
                "comment": generated["comment"], "evidence": generated["evidence"],
                "codeUnchanged": True, "requiresSemanticReview": True,
                "reviewInstruction": "Check this explanation against the original task, current implementation and active original requirements. Apply only if supported; otherwise skip. Syntax and exact evidence do not prove meaning."}


def _historical_ownership(coordinator, root, path, entries):
    """Supersession/retirement must not make an exact old owned line become human.

    Reconstruct only ownership that was persisted with the preview and still has
    a matching provenance row. Current active history is checked separately.
    """
    if not isinstance(entries, list) or len(entries) > 100:
        raise WorkerError("stale_preview", "The preview ownership evidence is invalid.")
    owned = []
    with coordinator.history._connection() as db:
        for entry in entries:
            if not isinstance(entry, dict):
                raise WorkerError("stale_preview", "The preview ownership evidence is invalid.")
            row = db.execute("SELECT record_json FROM requirements WHERE id=? AND workspace=? AND path=?",
                             (entry.get("requirementId"), str(root), str(path))).fetchone()
            original = json.loads(row["record_json"]) if row else None
            if original is None or any(original.get(key) != entry.get(key) for key in ("symbolId", "commentText", "codeHash")):
                raise WorkerError("stale_preview", "The preview ownership differs from its original record.")
            owned.append({key: entry[key] for key in ("symbolId", "commentText", "codeHash")})
    return owned


def _verified_preview(coordinator, root, path, job, preview_id, check_context=True):
    preview = job.get("preview")
    if not isinstance(preview, dict) or preview.get("id") != preview_id:
        raise WorkerError("stale_preview", "Use the current preview ID for this job.")
    unsigned = {key: value for key, value in preview.items() if key != "id"}
    if preview["id"] != _json_hash(unsigned) or preview.get("jobId") != job["id"] or preview.get("source") != job["afterSource"]:
        raise WorkerError("stale_preview", "The saved preview differs from the observed source.")
    owned = _historical_ownership(coordinator, root, path, preview.get("ownership"))
    generated, suggestion, annotated, records = _proposal(coordinator, root, path, job, preview["comment"], preview["passageId"], preview["reason"], owned=owned)
    if (annotated != preview["annotatedSource"] or suggestion != preview["suggestion"] or generated["evidence"] != preview["evidence"]
            or _bytes_hash(_encoded(preview["source"], preview["bom"])) != preview["rawBeforeHash"]
            or _bytes_hash(_encoded(annotated, preview["bom"])) != preview["rawAfterHash"]):
        raise WorkerError("stale_preview", "The saved preview is not the validated comment-only edit.")
    if len(_encoded(annotated, preview["bom"])) > MAX_SOURCE_BYTES:
        raise WorkerError("source_limit", "The annotated file would exceed the source limit.")
    if check_context and _context_hash(records) != preview["originalContextHash"]:
        raise WorkerError("stale_context", "Original requirements changed after preview; review them again.")
    return preview, records


def _replace(root, path, expected, source, bom, mode, version=None):
    """Use a same-directory replacement after a final byte/version recheck.

    This coordinates our own writers. External editors are not locked and their
    unsaved buffers are unavailable, so this is not an editor-version guarantee.
    """
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=".between-the-lines-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(_encoded(source, bom))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        current = _snapshot(root, path)
        if current["rawHash"] != expected or version is not None and current["version"] != version:
            raise WorkerError("stale_source", "Source changed before the annotation write; nothing was replaced.")
        if temporary.parent.resolve(strict=True) != path.parent or path.parent.resolve(strict=True) != path.parent:
            raise WorkerError("stale_source", "The source directory changed during the annotation write.")
        os.replace(temporary, path)
        temporary = None
        return _snapshot(root, path)
    finally:
        if temporary is not None and temporary.parent.resolve(strict=True) == path.parent:
            temporary.unlink(missing_ok=True)


def _record(job, preview, records, support_reason):
    first = preview["evidence"][0]
    model = job.get("modelAtCapture")
    model = "codex:" + model if isinstance(model, str) and model and len(model) <= 240 else "codex-active-model"
    explicit = {line.strip()[len("Supersede "):] for line in job["prompt"].splitlines() if line.strip().startswith("Supersede ")}
    generation = validate_generation({"provider": "custom", "model": model})
    return {"symbolId": job["symbolId"], "commentKind": "synthesized", "quote": first["quote"],
            "quoteStart": first["start"], "quoteEnd": first["end"], "evidence": preview["evidence"],
            "generation": generation, "generationRoute": "host_codex",
            "hostModelAtCapture": job.get("modelAtCapture"), "modelIdentityNote": "Reported by the capture hook; no separate model endpoint was called.",
            "supportReview": {"kind": "host_model_judgment", "supported": True, "reason": support_reason},
            "promptHash": digest(job["prompt"]), "promptEventId": job["promptEventId"], "eventId": job["promptEventId"],
            "sessionId": job["sessionId"], "turnId": job["turnId"], "source": job["source"],
            "workspaceRoot": job["workspaceRoot"], "path": job["path"],
            "commentText": preview["suggestion"]["commentText"], "codeHash": job["codeHash"],
            "sourceHash": job["afterHash"], "observedBeforeHash": digest(job["beforeSource"]),
            "supersedes": sorted(record["id"] for record in records if record["symbolId"] == job["symbolId"] and record["id"] in explicit)}


def _check_original(coordinator, job):
    with coordinator.history._connection() as db:
        original = db.execute("SELECT * FROM prompts WHERE id=?", (job["promptEventId"],)).fetchone()
    fields = {"workspace": job["workspaceRoot"], "source": job["source"], "session": job["sessionId"],
              "turn": job["turnId"], "prompt": job["prompt"]}
    if original is None or any(original[field] != value for field, value in fields.items()):
        raise WorkerError("invalid_evidence", "The immutable original prompt is unavailable or differs from this job.")


def _finish_write(coordinator, root, path, job, preview, journal):
    current = _snapshot(root, path)
    if current["rawHash"] == preview["rawBeforeHash"]:
        current = _replace(root, path, preview["rawBeforeHash"], preview["annotatedSource"], preview["bom"], preview["mode"], preview["beforeVersion"])
    if current["rawHash"] != preview["rawAfterHash"]:
        raise WorkerError("stale_source", "Source differs from both the approved preview and pending write; it was preserved.")
    # If recording or its following state commit fails, pendingWrite remains.
    # A retry commits provenance against the existing bytes without reinsertion.
    record = coordinator.history.record_requirement(journal["record"])
    coordinator.store.update_job(str(root), job["id"], state="applied", pendingWrite=None,
                                 record=record, appliedRawHash=preview["rawAfterHash"], appliedVersion=current["version"])
    return {"jobId": job["id"], "applied": True, "record": record,
            "comment": preview["comment"], "codeUnchanged": True,
            "semanticReview": "Host-model judgment; not a correctness proof."}


def apply_annotation(coordinator, workspace, job_id, preview_id, support_reason):
    """Commit a reviewed preview and retain an idempotent write/history journal."""
    support_reason = _text(support_reason, "support review reason")
    with coordinator.store.job_lock(workspace, job_id):
        root, path, job = _job(coordinator, workspace, job_id)
        if job["state"] == "applied" and not job.get("pendingUndo"):
            if (job.get("preview") or {}).get("id") != preview_id:
                raise WorkerError("stale_preview", "This is not the applied preview.")
            return {"jobId": job_id, "applied": True, "alreadyApplied": True, "record": job.get("record")}
        if job["state"] != "previewed":
            raise WorkerError("annotation_state", "Only a current preview can be applied.")
        journal = job.get("pendingWrite")
        preview, records = _verified_preview(coordinator, root, path, job, preview_id, check_context=not journal)
        if not journal:
            current = _snapshot(root, path)
            if current["rawHash"] != preview["rawBeforeHash"] or current["version"] != preview["beforeVersion"]:
                raise WorkerError("stale_source", "Source changed after preview; it was preserved.")
            _check_original(coordinator, job)
            journal = {"previewId": preview_id, "record": _record(job, preview, records, support_reason)}
            coordinator.store.update_job(str(root), job_id, pendingWrite=journal)
        if journal.get("previewId") != preview_id:
            raise WorkerError("stale_preview", "Recover the pending write with its original preview.")
        return _finish_write(coordinator, root, path, job, preview, journal)


def undo_annotation(coordinator, workspace, job_id):
    """Remove only this exact applied snapshot; retain the original intent ledger."""
    with coordinator.store.job_lock(workspace, job_id):
        root, path, job = _job(coordinator, workspace, job_id)
        if job["state"] == "undone":
            return {"jobId": job_id, "undone": True, "alreadyUndone": True}
        if job["state"] == "previewed" and job.get("pendingWrite"):
            preview, _ = _verified_preview(coordinator, root, path, job, job["preview"]["id"], check_context=False)
            _finish_write(coordinator, root, path, job, preview, job["pendingWrite"])
            job = coordinator.store.get_job(str(root), job_id)
        if job["state"] != "applied":
            raise WorkerError("annotation_state", "Only an applied annotation can be undone.")
        preview, _ = _verified_preview(coordinator, root, path, job, job["preview"]["id"], check_context=False)
        current = _snapshot(root, path)
        if job.get("pendingUndo") and current["rawHash"] == preview["rawBeforeHash"]:
            coordinator.store.update_job(str(root), job_id, state="undone", pendingUndo=None)
            return {"jobId": job_id, "undone": True, "recovered": True, "originalContextRetained": True}
        if current["rawHash"] != preview["rawAfterHash"] or current["version"] != job.get("appliedVersion"):
            raise WorkerError("stale_source", "Later source edits prevent automatic undo; they were preserved.")
        coordinator.store.update_job(str(root), job_id, pendingUndo={"previewId": preview["id"]})
        restored = _replace(root, path, preview["rawAfterHash"], preview["source"], preview["bom"], preview["mode"], current["version"])
        if restored["rawHash"] != preview["rawBeforeHash"]:
            raise WorkerError("stale_source", "Source changed while undo was finishing.")
        coordinator.store.update_job(str(root), job_id, state="undone", pendingUndo=None)
        return {"jobId": job_id, "undone": True, "originalContextRetained": True}
