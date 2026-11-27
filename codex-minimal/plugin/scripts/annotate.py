"""Read a Codex hook event or annotation request on stdin."""

import argparse
import json
from pathlib import Path
import sys

from annotation import apply, approval_for, preview, source_path
from hashlib import sha256
from intent import lookup
from turn import handle, read, save, state_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("hook", "context", "preview", "apply"))
    args = parser.parse_args()
    try:
        payload = sys.stdin.buffer.read(65537)
        if len(payload) > 65536:
            raise ValueError("Annotation request exceeds 64 KiB.")
        request = json.loads(payload)
        if args.action == "hook":
            print(json.dumps(handle(request)))
            return 0
        workspace = Path.cwd().resolve(strict=True)
        state_file = state_path(workspace, request["session"])
        state = read(state_file)
        if not state.get("requested"):
            raise ValueError("No completed Python edit in this session.")
        relative = state["edit"]["path"]
        path = source_path(workspace, relative)
        source = path.read_bytes()
        if sha256(source).hexdigest() != state["edit"]["after"]:
            raise ValueError("The captured edit is stale.")
        if args.action == "context":
            result = {"path": relative, "prompt": state["prompt"]}
            if "line" in request:
                line = request["line"]
                result["intent"] = lookup(workspace, relative, source, line)
                state["lookup"] = {"line": line, "result": result["intent"]}
                save(state_file, state)
            print(json.dumps(result))
            return 0
        context = state.get("draft_lookup") if args.action == "apply" else state.get("lookup")
        if context and lookup(workspace, relative, source, context["line"]) != context["result"]:
            raise ValueError("The intent context changed. Review a new preview.")
        if args.action == "preview":
            fields = {key: request[key] for key in ("line", "comment", "evidence")}
            if not context or context["line"] != fields["line"]:
                raise ValueError("Look up intent for the selected function first.")
            checked = preview(source, prompt=state["prompt"], **fields)
            state["draft"] = fields
            state["draft_lookup"] = context
            state["approval_id"] = approval_for(path, checked)
            state.pop("approved_turn", None)
            save(state_file, state)
            result = {"diff": checked.diff, "evidence": checked.evidence,
                      "approval_id": state["approval_id"]}
        else:
            if not state.get("approved_turn") or request["approval_id"] != state["approval_id"]:
                raise ValueError("The user has not approved this preview.")
            apply(workspace, relative, state["approval_id"], prompt=state["prompt"], **state["draft"])
            state_file.unlink()
            result = {"applied": True}
        print(json.dumps(result))
    except (KeyError, ValueError, SyntaxError, TypeError, OSError) as error:
        print(json.dumps({"error": str(error)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
