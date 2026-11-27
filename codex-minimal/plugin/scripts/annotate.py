"""Read a Codex hook event or annotation request on stdin."""

import argparse
import json
from pathlib import Path
import sys

from annotation import apply, approval_for, preview, source_path
from hashlib import sha256
from intent import lookup, read_records, repository_root
from tempfile import NamedTemporaryFile
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
        workspace = repository_root(Path.cwd())
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
        if args.action == "preview" and context and lookup(workspace, relative, source, context["line"]) != context["result"]:
            raise ValueError("The intent context changed. Review a new preview.")
        if args.action == "preview":
            fields = {key: request[key] for key in ("line", "comment", "evidence")}
            if not context or context["line"] != fields["line"]:
                raise ValueError("Look up intent for the selected function first.")
            checked = preview(source, prompt=state["prompt"], **fields)
            state["draft"] = fields
            state["draft_lookup"] = context
            target = context["result"]
            previous = read_records(workspace).get(target["path"], {}).get(target["symbol"])
            entry = {"intent": fields["comment"].strip(), "ast_hash": target["ast_hash"]}
            change = {"path": target["path"], "symbol": target["symbol"], "before": previous, "after": entry}
            state["intent_change"] = change
            state["source_approval"] = approval_for(path, checked)
            state["approval_id"] = sha256(json.dumps([state["source_approval"], change, context],
                                                     sort_keys=True).encode()).hexdigest()
            state.pop("approved_turn", None)
            save(state_file, state)
            result = {"diff": checked.diff, "intent_change": change, "evidence": checked.evidence,
                      "approval_id": state["approval_id"]}
        else:
            if not state.get("approved_turn") or request["approval_id"] != state["approval_id"]:
                raise ValueError("The user has not approved this preview.")
            # Serialize plugin writers; merge unrelated approved records under the lock.
            change = state["intent_change"]
            lock = workspace / ".intent.json.lock"
            with lock.open("x"):
                pass
            try:
                records = read_records(workspace)
                group = records.setdefault(change["path"], {})
                if not isinstance(group, dict) or group.get(change["symbol"]) != change["before"]:
                    raise ValueError("The intent record changed. Review a new preview.")
                if lookup(workspace, relative, source, context["line"]) != context["result"]:
                    raise ValueError("The intent context changed. Review a new preview.")
                group[change["symbol"]] = change["after"]
                serialized = (json.dumps(records, indent=2, sort_keys=True) + "\n").encode()
                if len(serialized) > 1048576:
                    raise ValueError("The updated intent map exceeds 1 MiB.")
                with NamedTemporaryFile(dir=workspace, delete=False) as stream:
                    staged = Path(stream.name)
                try:
                    staged.write_bytes(serialized)
                    # Publish the map first. A failed map write leaves the source untouched.
                    map_file = workspace / "intent.json"
                    prior_bytes = map_file.read_bytes() if map_file.exists() else None
                    staged.replace(map_file)
                    try:
                        apply(workspace, relative, state["source_approval"], prompt=state["prompt"], **state["draft"])
                    except (ValueError, OSError):
                        if map_file.read_bytes() == serialized:
                            if prior_bytes is None:
                                map_file.unlink()
                            else:
                                staged.write_bytes(prior_bytes)
                                staged.replace(map_file)
                        raise
                finally:
                    staged.unlink(missing_ok=True)
            finally:
                lock.unlink()
            state_file.unlink()
            result = {"applied": True}
        print(json.dumps(result))
    except (KeyError, ValueError, SyntaxError, TypeError, OSError) as error:
        print(json.dumps({"error": str(error)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
