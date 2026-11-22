"""Synthesize intent-aware comments for verified coding-tool transactions."""

from collections import Counter
import re
import time

from .grounding import build_grounded_comment
from .parsing import changed_declarations, discover
from .patches import apply_suggestions, make_suggestion, preceding_comment
from .synthesis import synthesize_comment
from .types import (WorkerError, digest, source_lines, validate_comment_text,
                    validate_evidence, validate_generation)


MAX_CANDIDATES = 64


def _identity(intent):
    if not isinstance(intent, dict) or intent.get("source") not in {"codex", "copilot"}:
        raise WorkerError("unobserved_edit", "Automatic annotation requires a captured coding-tool event.")
    for field in ("eventId", "sessionId", "turnId"):
        value = intent.get(field)
        if not isinstance(value, str) or not value or len(value) > 200:
            raise WorkerError("unobserved_edit", "Automatic annotation requires event, session and turn identity.")


def _known(params):
    records = params.get("knownRequirements", [])
    if not isinstance(records, list) or len(records) > 500:
        raise WorkerError("invalid_request", "Known requirements must be a bounded list.")
    for record in records:
        if not isinstance(record, dict) or not all(
            isinstance(record.get(key), str) and len(record[key]) <= limit
            for key, limit in (("id", 200), ("symbolId", 1024), ("symbolName", 1024),
                               ("quote", 2000), ("commentText", 260))
        ):
            raise WorkerError("invalid_request", "Known requirements contain an invalid source record.")
    return [record for record in records if record.get("status", "active") == "active"
            and record.get("scopeStatus") != "missing"]


def _qualified(declaration):
    qualified = re.sub(r"\([a-f0-9]{12}\)", "", declaration.symbol_id.split(":", 1)[-1])
    return re.sub(r"#\d+$", "", qualified)


def _owned_record(record):
    return {key: record.get(key, "") for key in ("symbolId", "commentText", "codeHash")}


def _historical_comment(record, language, declaration):
    """Restore exact prior output only when its immutable evidence is available."""
    prompt = record.get("prompt")
    if not isinstance(prompt, str) or digest(prompt) != record.get("promptHash"):
        raise WorkerError("invalid_evidence", "The original prompt evidence is unavailable.")
    prefix = "# " if language == "python" else "// "
    if record.get("commentKind", "quotation") == "synthesized":
        validate_evidence(prompt, record.get("evidence"))
        validate_generation(record.get("generation"))
        return validate_comment_text(record.get("commentText"), language)[len(prefix):]
    expected = prefix + build_grounded_comment({"comment": record["quote"]}, prompt, declaration.name)
    if expected != record.get("commentText"):
        raise WorkerError("invalid_evidence", "The legacy annotation differs from its original quotation.")
    return expected[len(prefix):]


def annotate_observed(params, backend=None):
    """Use the configured model, then validate a complete comment-only batch.

    Tool attribution and evidence ranges are mechanical checks. Model generation
    and its separate support review are fallible judgments, never semantic proof.
    """
    from .agent import validate_params

    started = time.perf_counter()
    validate_params(params)
    intent = params.get("intent")
    _identity(intent)
    if "previousSource" not in params:
        raise WorkerError("unobserved_edit", "Automatic annotation requires the observed before snapshot.")
    source, before, language = params["source"], params["previousSource"], params["language"]
    if len(source.encode("utf-8")) > 256 * 1024 or len(before.encode("utf-8")) > 256 * 1024:
        raise WorkerError("source_limit", "Automatic annotation supports files up to 256 KiB.")
    current, previous = discover(source, language), discover(before, language)
    candidates = changed_declarations(current, previous)
    scope_counts = Counter(_qualified(item) for item in current)
    previous_by_id = {item.symbol_id: item for item in previous}
    records = _known(params)
    active_originals = [record for record in params.get("knownRequirements", [])
                        if record.get("status", "active") == "active"]
    owned = [*params.get("ownedComments", []), *map(_owned_record, records)]
    candidate_ids = {item.symbol_id for item in candidates}
    for declaration in current:
        old = previous_by_id.get(declaration.symbol_id)
        if declaration.symbol_id in candidate_ids or old is None:
            continue
        if preceding_comment(source, declaration, owned)[0] != "none":
            continue
        if any(record["symbolId"] == declaration.symbol_id
               and preceding_comment(before, old, [_owned_record(record)])[0] == "owned"
               for record in records):
            candidates.append(declaration)
            candidate_ids.add(declaration.symbol_id)
    candidates.sort(key=lambda item: (item.start_line, item.end_line))
    prompt = intent["prompt"]
    explicit_supersession = {line.strip()[len("Supersede "):]
                             for line in prompt.splitlines() if line.strip().startswith("Supersede ")}
    result = {
        "uri": params["uri"], "sourceHash": digest(source), "suggestions": [], "skipped": [],
        "requirements": [], "metrics": {"inputTokens": 0, "outputTokens": 0, "modelCalls": 0, "elapsedMs": 0},
    }
    attempts = 0
    budget = params.get("maxSuggestions", 3)

    def skip(declaration, reason):
        result["skipped"].append({"symbolId": declaration.symbol_id, "reason": reason})

    for index, declaration in enumerate(candidates):
        if index >= MAX_CANDIDATES or attempts >= budget or len(result["suggestions"]) >= budget:
            skip(declaration, "Automatic annotation budget reached.")
            continue
        scope_records = [record for record in records if record["symbolId"] == declaration.symbol_id
                         and record["symbolName"] == declaration.name]
        try:
            ownership, _ = preceding_comment(source, declaration, owned)
            if ownership == "human":
                skip(declaration, "Existing human documentation is preserved.")
                continue
            if scope_counts[_qualified(declaration)] > 1:
                skip(declaration, "Duplicate declaration scope needs explicit review.")
                continue
            old = previous_by_id.get(declaration.symbol_id)
            restorable = [record for record in scope_records if old is not None
                          and preceding_comment(before, old, [_owned_record(record)])[0] == "owned"]
            if (ownership == "none" and scope_records and not restorable
                    and any(record["id"] not in explicit_supersession for record in scope_records)):
                skip(declaration, "A previously removed annotation stays removed until explicitly reviewed.")
                continue
            # Restoring an unchanged declaration is not a new model interpretation.
            # A changed body requires fresh synthesis; stale explanations are not copied.
            exact = {record["commentText"]: record for record in restorable
                     if record.get("codeHash") == declaration.code_hash
                     and record["id"] not in explicit_supersession}
            if ownership == "none" and len(exact) == 1:
                record = next(iter(exact.values()))
                comment = _historical_comment(record, language, declaration)
                suggestion = make_suggestion(source, language, declaration, comment,
                                             "Restore the exact annotation removed from unchanged code.",
                                             ["Original requirement: " + record["id"]], owned)
                if suggestion:
                    result["suggestions"].append(suggestion)
                continue
            if backend is None:
                raise WorkerError("model_unavailable", "Configure a local model or custom provider to generate comments.")
            attempts += 1
            generated = synthesize_comment(backend, {**params, "knownRequirements": active_originals},
                                            declaration, result["metrics"], verify=True)
            evidence = validate_evidence(prompt, generated.get("evidence"))
            generation = validate_generation(generated.get("generation"))
            review = [
                "Model-written explanation; separate model support review is fallible, not proof.",
                "Captured event: " + intent["eventId"],
                "Prompt SHA-256: " + digest(prompt),
                "Model: " + generation["provider"] + "/" + generation["model"],
                *[f"Original prompt UTF-16 characters {span['start']}:{span['end']}: {span['quote']}"
                  for span in evidence],
            ]
            suggestion = make_suggestion(source, language, declaration, generated.get("comment"),
                                         generated.get("reason", "Explain this code using the captured task context."),
                                         review, owned)
            if suggestion is None:
                skip(declaration, "The existing generated explanation already matches.")
                continue
            validate_comment_text(suggestion["commentText"], language)
            result["suggestions"].append(suggestion)
            first = evidence[0]
            result["requirements"].append({
                "symbolId": declaration.symbol_id, "symbolName": declaration.name,
                "commentKind": "synthesized", "quote": first["quote"],
                "quoteStart": first["start"], "quoteEnd": first["end"],
                "evidence": evidence, "generation": generation,
                "promptHash": digest(prompt), "promptEventId": intent["eventId"], "eventId": intent["eventId"],
                "sessionId": intent["sessionId"], "turnId": intent["turnId"], "source": intent["source"],
                "commentText": suggestion["commentText"], "codeHash": declaration.code_hash,
                "sourceHash": digest(source), "observedBeforeHash": digest(before),
                "supersedes": sorted(record["id"] for record in scope_records
                                     if record["id"] in explicit_supersession),
            })
        except WorkerError as exc:
            if exc.code in {"model_unavailable", "runtime_unavailable"} or exc.code.startswith("provider_"):
                raise
            skip(declaration, f"{exc.code}: {exc}")
    annotated = apply_suggestions(source, language, result["suggestions"])
    result["annotatedSource"] = annotated
    result["annotatedSourceHash"] = digest(annotated)
    result["metrics"]["elapsedMs"] = round((time.perf_counter() - started) * 1000)
    return result


def retire_annotation(params):
    """Propose removing only the exact annotation from an explicit ledger record."""
    from .agent import validate_params

    started = time.perf_counter()
    validate_params(params)
    source, language = params["source"], params["language"]
    record = params.get("requirement")
    if not isinstance(record, dict) or not all(
        isinstance(record.get(key), str) and len(record[key]) <= limit
        for key, limit in (("id", 200), ("symbolId", 1024), ("commentText", 260))
    ):
        raise WorkerError("invalid_request", "Retiring an annotation needs its exact requirement record.")
    prefix = "# " if language == "python" else "// "
    if record.get("commentKind", "quotation") == "synthesized":
        validate_comment_text(record["commentText"], language)
    elif not record["commentText"].startswith(prefix + "User context: "):
        raise WorkerError("invalid_request", "Only an attributed generated annotation can be retired.")
    result = {
        "uri": params["uri"], "sourceHash": digest(source), "suggestions": [], "skipped": [],
        "requirements": [], "metrics": {"inputTokens": 0, "outputTokens": 0, "modelCalls": 0, "elapsedMs": 0},
    }
    declarations = [item for item in discover(source, language) if item.symbol_id == record["symbolId"]]
    if len(declarations) == 1:
        declaration = declarations[0]
        ownership, row = preceding_comment(source, declaration, [_owned_record(record)])
        if ownership == "owned":
            lines = source_lines(source)
            character = 1 if row == 0 and lines[row].startswith("\ufeff") else 0
            old_text = lines[row][character:]
            result["suggestions"].append({
                "id": digest(record["id"] + digest(source) + ":retire")[:24],
                "symbolId": declaration.symbol_id, "symbolName": declaration.name,
                "range": {"start": {"line": row, "character": character},
                          "end": {"line": row + 1, "character": 0}},
                "oldText": old_text, "newText": "", "commentText": "", "codeHash": declaration.code_hash,
                "explanation": "Remove the exact generated annotation for an explicitly retired requirement.",
                "evidence": ["Requirement: " + record["id"], "Original annotation matched exactly."],
            })
        else:
            result["skipped"].append({"symbolId": record["symbolId"],
                                      "reason": "The original annotation is absent or was edited; source is preserved."})
    else:
        result["skipped"].append({"symbolId": record["symbolId"],
                                  "reason": "The original declaration is missing or ambiguous; source is preserved."})
    annotated = apply_suggestions(source, language, result["suggestions"])
    result["annotatedSource"] = annotated
    result["annotatedSourceHash"] = digest(annotated)
    result["metrics"]["elapsedMs"] = round((time.perf_counter() - started) * 1000)
    return result
