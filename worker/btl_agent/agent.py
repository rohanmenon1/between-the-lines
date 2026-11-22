"""Bounded decisions, allowlisted context, and exact-input result caching."""

from collections import OrderedDict
import json
import os
from pathlib import Path
import stat
import sys
import time

from .grounding import build_grounded_comment
from .parsing import changed_declarations, discover
from .patches import make_suggestion, preceding_comment
from .synthesis import GENERATION_SYSTEM, generation_identity, generation_payload, original_contexts, validate_decision
from .types import Configuration, WorkerError, digest


SYSTEM = GENERATION_SYSTEM


def validate_params(params):
    for key in ("uri", "source", "language"):
        if not isinstance(params.get(key), str):
            raise WorkerError("invalid_request", f"{key} must be a string.")
    if params["language"] not in {"python", "java"}:
        raise WorkerError("invalid_request", "Supported languages are python and java.")
    if "previousSource" in params and not isinstance(params["previousSource"], str):
        raise WorkerError("invalid_request", "previousSource must be a string.")
    intent = params.get("intent")
    if intent is not None and (
        not isinstance(intent, dict) or not isinstance(intent.get("prompt"), str)
        or intent.get("source") not in {"manual", "codex", "copilot"}
        or len(intent["prompt"]) > 16000
    ):
        raise WorkerError("invalid_request", "intent must contain a bounded prompt and supported source.")
    paths = params.get("contextFiles", [])
    if not isinstance(paths, list) or len(paths) > 12 or not all(
        isinstance(path, str) and len(path) <= 512 for path in paths
    ):
        raise WorkerError("invalid_request", "Provide at most 12 workspace-relative context paths.")
    root = params.get("workspaceRoot")
    if root is not None and not isinstance(root, str):
        raise WorkerError("invalid_request", "workspaceRoot must be a local path.")
    owned = params.get("ownedComments", [])
    if not isinstance(owned, list) or len(owned) > 1000 or not all(
        isinstance(item, dict) and all(isinstance(item.get(key), str)
                                      for key in ("symbolId", "commentText", "codeHash"))
        for item in owned
    ):
        raise WorkerError("invalid_request", "ownedComments must contain valid ownership records.")
    limit = params.get("maxSuggestions", 3)
    if type(limit) is not int or not 1 <= limit <= 10:
        raise WorkerError("invalid_request", "maxSuggestions must be 1–10.")
    if "force" in params and type(params["force"]) is not bool:
        raise WorkerError("invalid_request", "force must be boolean.")


def read_context(params, relative_path):
    """Read a bounded UTF-8 regular file, resolving links inside the workspace."""
    if relative_path not in params.get("contextFiles", []) or not params.get("workspaceRoot"):
        raise WorkerError("context_denied", "The requested file is not allowlisted.")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or ":" in relative_path:
        raise WorkerError("context_denied", "Context paths must remain inside the workspace.")
    try:
        root = Path(params["workspaceRoot"]).resolve(strict=True)
        target = (root / relative).resolve(strict=True)
        if not target.is_relative_to(root) or not target.is_file():
            raise WorkerError("context_denied", "Context paths must remain inside the workspace.")
        if target.stat().st_size > 16384:
            raise WorkerError("context_limit", "Context files must be at most 16 KiB.")
        # Cap the read as well as stat to tolerate a concurrent growing file.
        with target.open("rb") as stream:
            # Validate the opened handle, not only the pre-open path, so a link
            # swapped during opening cannot redirect a read outside the root.
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or not opened_path(stream).is_relative_to(root):
                raise WorkerError("context_denied", "The opened file is outside the workspace.")
            data = stream.read(16385)
        if len(data) > 16384:
            raise WorkerError("context_limit", "Context files must be at most 16 KiB.")
        return data.decode("utf-8-sig")
    except WorkerError:
        raise
    except (OSError, UnicodeError) as exc:
        raise WorkerError("context_unavailable", "The selected context file could not be read safely.") from exc


def opened_path(stream):
    """Resolve the actual descriptor on each supported desktop platform."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        import msvcrt

        get_path = ctypes.windll.kernel32.GetFinalPathNameByHandleW
        get_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        get_path.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_path(msvcrt.get_osfhandle(stream.fileno()), buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise OSError("Cannot resolve context file handle.")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return Path(value)
    if sys.platform == "darwin":
        import fcntl

        # F_GETPATH is part of macOS's fcntl API.
        value = fcntl.fcntl(stream.fileno(), 50, b"\0" * 1024)
        return Path(os.fsdecode(value.split(b"\0", 1)[0]))
    return Path(f"/proc/self/fd/{stream.fileno()}").resolve(strict=True)


class Agent:
    def __init__(self, backend):
        self.backend = backend
        self.configuration = Configuration()
        self.cache = OrderedDict()

    def configure(self, params):
        configuration = Configuration.from_params(params)
        if configuration != self.configuration:
            self.cache.clear()
            self.configuration = configuration
        self.backend.configure(configuration)
        return self.backend.status()

    def annotate_observed(self, params):
        from .automatic import annotate_observed

        return annotate_observed(params, self.backend)

    def _key(self, params, declaration):
        # Full source in the key means range/ownership and sibling context cannot
        # accidentally be reused after unrelated edits or line movements.
        relevant = {key: params.get(key) for key in (
            "uri", "source", "language", "previousSource", "intent",
            "workspaceRoot", "contextFiles", "ownedComments",
            "knownRequirements",
        )}
        # Routing provenance remains in capture/history records. It neither
        # changes the semantic task nor deserves another inference/cache entry.
        relevant["intent"] = {"prompt": (params.get("intent") or {}).get("prompt", "")}
        relevant["knownRequirements"] = original_contexts(params, declaration)
        return digest(json.dumps({
            "input": relevant, "symbol": declaration.symbol_id,
            "model": self.backend.identity(), "configuration": self.configuration.__dict__,
        }, sort_keys=True))

    def _decision(self, params, declaration, metrics):
        key = self._key(params, declaration)
        cached = self.cache.get(key)
        if cached:
            decision, evidence, path, context_hash = cached
            try:
                if path is None or digest(read_context(params, path)) == context_hash:
                    self.cache.move_to_end(key)
                    return decision, evidence
            except WorkerError:
                pass
            del self.cache[key]
        payload = generation_payload(params, declaration)
        payload["existingGeneratedComment"] = next((x["commentText"] for x in params.get("ownedComments", [])
                                                    if x["symbolId"] == declaration.symbol_id), None)
        evidence = ["Current declaration"]
        if params.get("intent"):
            evidence.append("Selected task intent")
        path = None
        context_hash = None
        for attempt in range(2):
            if metrics["modelCalls"] >= params.get("maxSuggestions", 3) * 2:
                raise WorkerError("tool_limit", "The analysis model-call budget is exhausted.")
            metrics["modelCalls"] += 1
            decision, usage = self.backend.generate(SYSTEM, payload)
            metrics["inputTokens"] += usage.get("inputTokens", 0)
            metrics["outputTokens"] += usage.get("outputTokens", 0)
            self._validate_decision(decision)
            if decision["action"] != "read_context":
                break
            if attempt == 1:
                raise WorkerError("tool_limit", "The context-read budget is exhausted.")
            path = decision.get("path")
            context = read_context(params, path)
            context_hash = digest(context)
            evidence.append("Workspace file: " + path)
            payload["contextFile"] = {"path": path, "content": context}
            payload["allowedContextFiles"] = []
        # Cache only completed decisions. Invalid or unsafe proposals are removed
        # by analyze; transient inference and context failures never enter cache.
        self.cache[key] = (decision, evidence, path, context_hash)
        while len(self.cache) > 128:
            self.cache.popitem(last=False)
        return decision, evidence

    @staticmethod
    def _validate_decision(decision):
        if not isinstance(decision, dict) or decision.get("action") not in {
            "skip", "read_context", "propose_comment", "synthesize_comment", "flag_mismatch"
        } or not isinstance(decision.get("reason"), str) or len(decision["reason"]) > 300:
            raise WorkerError("invalid_model_output", "The model returned an invalid decision.")
        if decision["action"] == "read_context" and not isinstance(decision.get("path"), str):
            raise WorkerError("invalid_model_output", "A context read requires an allowlisted path.")
        if decision["action"] in {"propose_comment", "synthesize_comment"} and not isinstance(decision.get("comment"), str):
            raise WorkerError("invalid_model_output", "A proposal requires a plain text comment.")
        fields = {"action", "reason"}
        if decision["action"] == "propose_comment":
            fields.add("comment")
        elif decision["action"] == "synthesize_comment":
            fields.update(("comment", "promptEvidence"))
        elif decision["action"] == "read_context":
            fields.add("path")
        if set(decision) != fields:
            raise WorkerError("invalid_model_output", "The decision contains fields unrelated to its action.")

    def analyze(self, params):
        started = time.perf_counter()
        validate_params(params)
        source, language = params["source"], params["language"]
        current = discover(source, language)
        previous = discover(params["previousSource"], language) if "previousSource" in params else []
        candidates = changed_declarations(current, previous, params.get("force", False))
        owned = params.get("ownedComments", [])
        result = {
            "uri": params["uri"], "sourceHash": digest(source), "suggestions": [],
            "skipped": [], "metrics": {"inputTokens": 0, "outputTokens": 0, "modelCalls": 0, "elapsedMs": 0},
        }
        for index, declaration in enumerate(candidates):
            if index >= 64:
                result["skipped"].append({"symbolId": declaration.symbol_id, "reason": "Analysis candidate budget reached."})
                break
            if len(result["suggestions"]) >= params.get("maxSuggestions", 3):
                break
            if preceding_comment(source, declaration, owned)[0] == "human":
                result["skipped"].append({"symbolId": declaration.symbol_id, "reason": "Existing human documentation is preserved."})
                continue
            if result["metrics"]["modelCalls"] >= params.get("maxSuggestions", 3) * 2:
                result["skipped"].append({"symbolId": declaration.symbol_id, "reason": "Analysis model-call budget reached."})
                break
            try:
                decision, evidence = self._decision(params, declaration, result["metrics"])
                if decision["action"] == "synthesize_comment":
                    intent = params.get("intent") or {}
                    generated = validate_decision(decision, intent.get("prompt"))
                    cited = ["Generated comment; semantic relevance requires review.",
                             "Original prompt SHA-256: " + generated["promptHash"],
                             *[item["quote"] for item in generated["evidence"]]]
                    suggestion = make_suggestion(
                        source, language, declaration, generated["comment"],
                        "Generated from the task and changed code. Review its meaning before accepting.", cited, owned,
                    )
                    if suggestion:
                        suggestion.update({"commentKind": "synthesized", "promptEvidence": generated["evidence"],
                                           "generation": generation_identity(self.backend)})
                        result["suggestions"].append(suggestion)
                        continue
                    reason = "The existing generated comment already matches."
                elif decision["action"] == "propose_comment":
                    intent = params.get("intent") or {}
                    comment = build_grounded_comment(decision, intent.get("prompt"), declaration.name)
                    prompt = intent["prompt"]
                    excerpt = decision["comment"]
                    offset = prompt.find(excerpt)
                    cited = [
                        "Verbatim user context; relevance and active meaning require review.",
                        f"Source: {intent.get('source', 'unknown')}; prompt SHA-256: {digest(prompt)}",
                        f"Characters {offset}:{offset + len(excerpt)}: {excerpt}",
                    ]
                    if intent.get("eventId"):
                        cited.append("Captured event: " + str(intent["eventId"]))
                    suggestion = make_suggestion(
                        source, language, declaration, comment,
                        "Verbatim context naming this symbol. Review its relevance; implementation is not verified.", cited, owned,
                    )
                    if suggestion:
                        result["suggestions"].append(suggestion)
                        continue
                    reason = "The existing comment already matches."
                else:
                    reason = decision["reason"]
                    if decision["action"] == "flag_mismatch":
                        reason = "Possible intent mismatch (unverified): " + reason
                result["skipped"].append({"symbolId": declaration.symbol_id, "reason": reason})
            except WorkerError as exc:
                try:
                    self.cache.pop(self._key(params, declaration), None)
                except WorkerError:
                    # A context-limit failure can occur while constructing the
                    # semantic cache key, before any decision was cached.
                    pass
                # Missing runtime/model is an actionable request-level error.
                if exc.code in {"model_unavailable", "runtime_unavailable"} or exc.code.startswith("provider_"):
                    raise
                result["skipped"].append({"symbolId": declaration.symbol_id, "reason": f"{exc.code}: {exc}"})
        result["metrics"]["elapsedMs"] = round((time.perf_counter() - started) * 1000)
        return result
