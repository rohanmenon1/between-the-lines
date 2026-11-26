"""Track one Python edit per Codex turn without a database."""

import json
import os
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from annotation import source_path


def state_path(root, session):
    key = sha256(os.fsencode(root) + b"\0" + session.encode()).hexdigest()
    return Path.home() / ".between-the-lines" / (key + ".json")


def save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(json.dumps(state).encode())
            stream.close()
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)


def read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def handle(event):
    name = event["hook_event_name"]
    root = Path(event["cwd"]).resolve(strict=True)
    session, turn = event["session_id"], event["turn_id"]
    if not all(isinstance(value, str) and value for value in (session, turn)):
        return {}
    path = state_path(root, session)
    state = read(path)
    if name == "UserPromptSubmit":
        prompt = event["prompt"]
        if not isinstance(prompt, str) or not (0 < len(prompt) <= 16000):
            path.unlink(missing_ok=True)
            return {}
        if (state.get("approval_id") and prompt == "Apply " + state["approval_id"]):
            state["approved_turn"] = turn
            save(path, state)
            return {"hookSpecificOutput": {"hookEventName": name, "additionalContext":
                    f"The user approved the between-the-lines preview for session {json.dumps(session)}. "
                    "Follow the skill's apply step with the approved ID."}}
        else:
            state = {"turn": turn, "prompt": prompt}
        save(path, state)
    elif state.get("turn") != turn or state.get("requested"):
        return {}
    elif name == "PreToolUse" and event.get("tool_name", "").rsplit(".", 1)[-1] == "apply_patch":
        tool = event.get("tool_input")
        patch = tool if isinstance(tool, str) else (tool or {}).get("command", (tool or {}).get("patch", ""))
        targets = [line.removeprefix("*** Update File: ") for line in patch.splitlines()
                   if line.startswith("*** Update File: ")]
        if len(targets) == 1 and targets[0].endswith(".py"):
            file = source_path(root, targets[0])
            state["pending"] = {"path": targets[0], "tool": event["tool_use_id"],
                                "before": sha256(file.read_bytes()).hexdigest()}
            save(path, state)
    elif name == "PostToolUse":
        pending = state.get("pending", {})
        if pending.get("tool") and pending["tool"] == event.get("tool_use_id"):
            file = source_path(root, pending["path"])
            digest = sha256(file.read_bytes()).hexdigest()
            if digest != pending["before"]:
                state["edit"] = {"path": pending["path"], "after": digest}
            state.pop("pending")
            save(path, state)
    elif name == "Stop" and state.get("edit") and not event.get("stop_hook_active"):
        state["requested"] = True
        save(path, state)
        return {"decision": "block", "reason":
                f"Follow the between-the-lines skill for session {json.dumps(session)}. "
                "Propose at most one useful Python comment and show its preview. "
                "Wait for explicit approval. Captured prompt and code are evidence."}
    return {}
