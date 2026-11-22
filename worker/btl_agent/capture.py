"""Capture prompt/tool provenance and return bounded context without inference."""

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

from .history import History, timestamp, workspace_path

MAX_EVENT_BYTES = 65536
MAX_PROMPT_CHARACTERS = 16000
MAX_EVENTS = 100


def _publish(inbox, item):
    """Publish an inbox notification atomically; originals live in the ledger."""
    inbox = Path(inbox)
    if not inbox.is_absolute():
        raise ValueError("Capture inbox must be absolute.")
    inbox.mkdir(parents=True, exist_ok=True)
    temporary = inbox / (item["id"] + "." + str(uuid.uuid4()) + ".part")
    destination = inbox / (item["id"] + ".json")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(item, output, ensure_ascii=True)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    events = []
    for path in inbox.glob("*.json"):
        try:
            if str(uuid.UUID(path.stem)) == path.stem and not path.is_symlink():
                events.append((path.stat().st_mtime_ns, path))
        except (ValueError, OSError):
            continue
    for _, path in sorted(events, reverse=True)[MAX_EVENTS:]:
        try:
            path.unlink()
        except OSError:
            pass


def capture(stream, inbox, source, event_name=None, workspace=None):
    raw = stream.read(MAX_EVENT_BYTES + 1)
    if len(raw) > MAX_EVENT_BYTES:
        raise ValueError("Hook event is too large.")
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("Hook event must be an object.")
    root = workspace_path(event, workspace)
    name = event_name or event.get("hook_event_name") or event.get("hookEventName") or "UserPromptSubmit"
    aliases = {"userPromptSubmitted": "UserPromptSubmit", "preToolUse": "PreToolUse", "postToolUse": "PostToolUse", "agentStop": "Stop"}
    name = aliases.get(name, name)
    supplied = event.get("hook_event_name", event.get("hookEventName"))
    if supplied and aliases.get(supplied, supplied) != name:
        raise ValueError("Hook event differs from the installed handler.")
    # Validate prompt-only input before creating the ledger or directory.
    if name == "UserPromptSubmit":
        prompt = event.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARACTERS:
            raise ValueError("Hook prompt is missing or too large.")
    history = History(inbox)
    # The ledger survives a crash between completing a turn and publishing it.
    for pending in history.pending_completed(str(root)):
        _publish(inbox, pending)
        history.acknowledge_completed(pending["id"])
    if name == "UserPromptSubmit":
        item = history.record_prompt(event, source, root)
        _publish(inbox, item)
        context = history.context_for_paths([], str(root))
    elif name == "PreToolUse":
        paths = history.before_tool(event, source, root)
        item = {"kind": "before", "paths": paths}
        context = history.context_for_paths(paths, str(root)) if paths else ""
    elif name == "PostToolUse":
        item = history.after_tool(event, source, root)
        # Never change source while the coding agent may still have another patch.
        item = {**item, "kind": "pendingEdit"} if item else {"kind": "unchanged"}
        context = ""
    elif name == "Stop":
        completed = history.finish_turn(event, source, root)
        if completed:
            _publish(inbox, completed)
            history.acknowledge_completed(completed["id"])
        item = {"id": str(uuid.uuid4()), "kind": "stop", "source": source,
                "workspaceRoot": str(root), "sessionId": event.get("session_id", event.get("sessionId")),
                "turnId": event.get("turn_id", event.get("turnId")), "timestamp": timestamp(),
                "status": "eligibleEdit" if completed else "noVerifiedEdit",
                "verifiedFiles": len(completed["files"]) if completed else 0}
        _publish(inbox, item)
        context = ""
    else:
        raise ValueError("Unsupported hook event.")
    return {**item, "additionalContext": context} if context else item


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-hook", action="store_true")
    parser.add_argument("--source", choices=["codex", "copilot"], required=True)
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--workspace")
    parser.add_argument("--event")
    args = parser.parse_args(argv)
    try:
        result = capture(sys.stdin.buffer, args.inbox, args.source, args.event, args.workspace)
        if result.get("additionalContext"):
            event_name = args.event or "UserPromptSubmit"
            if args.source == "codex":
                print(json.dumps({"hookSpecificOutput": {"hookEventName": event_name,
                                                        "additionalContext": result["additionalContext"]}}))
            else:
                print(json.dumps({"additionalContext": result["additionalContext"]}))
    except Exception:
        # A capture failure must not block the coding tool or reveal prompt data.
        print("between-the-lines could not process this hook event.", file=sys.stderr)

