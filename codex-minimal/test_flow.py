"""Exercise the public stdin interface with Codex-shaped events."""

import ast
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parent / "plugin" / "scripts" / "annotate.py"


class FlowTest(unittest.TestCase):
    def test_fresh_session_intent_tool(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            source = "def helper():\n    return 1\n\ndef caller():\n    return helper()\n"
            (root / "example.py").write_text(source, encoding="utf-8")
            caller = ast.parse(source).body[1]
            digest = sha256(ast.dump(caller).encode()).hexdigest()
            (root / "intent.json").write_text(json.dumps({"example.py": {
                "caller": {"intent": "Preserve the existing return contract.", "ast_hash": "sha256:" + digest},
                "helper": {"intent": "Obsolete rationale.", "ast_hash": "old"},
            }}), encoding="utf-8")
            methods = [("initialize", {"protocolVersion": "2025-06-18"}), ("tools/list", {}),
                       ("tools/call", {"name": "lookup_intent", "arguments":
                                      {"path": "example.py", "symbol": "caller"}})]
            messages = [json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
                        for i, (method, params) in enumerate(methods)]
            run = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT.with_name("intent_server.py"))],
                                 input="\n".join(messages) + "\n", cwd=root, text=True,
                                 capture_output=True, check=True, timeout=10)
            init, listing, call = [json.loads(line)["result"] for line in run.stdout.splitlines()]
            self.assertIn("tools", init["capabilities"])
            self.assertEqual([tool["name"] for tool in listing["tools"]], ["lookup_intent"])
            value = json.loads(call["content"][0]["text"])
            self.assertEqual(value["intent"], "Preserve the existing return contract.")
            self.assertEqual(value["calls"][0]["status"], "stale")
            self.assertNotIn("intent", value["calls"][0])

    def test_preview_approval_and_stale_source(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "fixture.py"
            file.write_bytes(b"def total(items):\n    return 0\n")
            env = dict(os.environ, HOME=directory, USERPROFILE=directory)
            ids = {"session_id": "test-session", "turn_id": "edit", "cwd": directory}
            session = {"session": "test-session"}

            def request(action, data):
                run = subprocess.run([sys.executable, str(SCRIPT), action], cwd=root,
                                     env=env, input=json.dumps(data), capture_output=True,
                                     text=True, timeout=10)
                return json.loads(run.stdout)

            def hook(name, **data):
                return request("hook", {**ids, "hook_event_name": name, **data})

            reason = "Use sum to support iterators."
            hook("UserPromptSubmit", prompt=reason)
            tool = {"tool_use_id": "patch", "tool_name": "apply_patch",
                    "tool_input": {"command": "*** Begin Patch\n*** Update File: fixture.py\n*** End Patch"}}
            hook("PreToolUse", **tool)
            callee = b"def sum_items(items):\n    return sum(items)\n"
            helper = root / "helper.py"
            helper.write_bytes(callee)
            intent = root / "intent.json"
            digest = sha256(ast.dump(ast.parse(callee).body[0]).encode()).hexdigest()
            intent.write_text(json.dumps({"helper.py": {"sum_items": {
                "intent": "Support iterators.", "ast_hash": "sha256:" + digest}}}),
                encoding="utf-8")
            changed = b"from helper import sum_items as add\n\ndef total(items):\n    return add(items)\n"
            file.write_bytes(changed)
            hook("PostToolUse", **tool)
            self.assertEqual(hook("Stop")["decision"], "block")
            self.assertEqual(hook("Stop"), {})
            self.assertEqual(request("context", session)["prompt"], reason)
            context = {**session, "line": 3}
            calls = request("context", context)["intent"]["calls"]
            self.assertEqual([(call["status"], call.get("intent")) for call in calls],
                             [("current", "Support iterators.")])
            draft = {**context, "comment": "Sum the items so callers can pass iterators.",
                     "evidence": reason}
            self.assertIn("error", request("preview", {**draft, "evidence": "invented"}))
            proposal = request("preview", draft)
            approval = {**session, "approval_id": proposal["approval_id"]}
            self.assertIn("error", request("apply", approval))
            self.assertEqual(file.read_bytes(), changed)
            hook("UserPromptSubmit", turn_id="approval", prompt="Apply " + proposal["approval_id"])
            helper.write_bytes(callee.replace(b"sum(items)", b"0"))
            self.assertIn("intent context changed", request("apply", approval)["error"])
            calls = request("context", context)["intent"]["calls"]
            self.assertEqual(calls[0]["status"], "stale")
            self.assertNotIn("intent", calls[0])
            helper.write_bytes(callee)
            file.write_bytes(changed + b"# another edit\n")
            self.assertIn("stale", request("apply", approval)["error"])
            file.write_bytes(changed)
            self.assertEqual(request("apply", approval), {"applied": True})
            self.assertIn(b"# Sum the items", file.read_bytes())
            self.assertEqual(ast.dump(ast.parse(changed)), ast.dump(ast.parse(file.read_bytes())))
            self.assertIn("error", request("apply", approval))


if __name__ == "__main__":
    unittest.main()
