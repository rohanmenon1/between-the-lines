"""Exercise the public stdin interface with Codex-shaped events."""

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().parent / "plugin" / "scripts" / "annotate.py"


class FlowTest(unittest.TestCase):
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
            changed = b"def total(items):\n    return sum(items)\n"
            file.write_bytes(changed)
            hook("PostToolUse", **tool)
            self.assertEqual(hook("Stop")["decision"], "block")
            self.assertEqual(hook("Stop"), {})
            self.assertEqual(request("context", session)["prompt"], reason)
            draft = {**session, "line": 1, "comment": "Sum the items so callers can pass iterators.",
                     "evidence": reason}
            self.assertIn("error", request("preview", {**draft, "evidence": "invented"}))
            proposal = request("preview", draft)
            approval = {**session, "approval_id": proposal["approval_id"]}
            self.assertIn("error", request("apply", approval))
            self.assertEqual(file.read_bytes(), changed)
            hook("UserPromptSubmit", turn_id="approval", prompt="Apply " + proposal["approval_id"])
            file.write_bytes(changed + b"# another edit\n")
            self.assertIn("stale", request("apply", approval)["error"])
            file.write_bytes(changed)
            self.assertEqual(request("apply", approval), {"applied": True})
            self.assertTrue(file.read_bytes().startswith(b"# Sum the items"))
            self.assertEqual(ast.dump(ast.parse(changed)), ast.dump(ast.parse(file.read_bytes())))
            self.assertIn("error", request("apply", approval))


if __name__ == "__main__":
    unittest.main()
