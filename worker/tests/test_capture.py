import io
import json
from pathlib import Path
import tempfile
import unittest

from btl_agent.capture import capture


class CaptureTests(unittest.TestCase):
    def test_both_provider_schemas_and_unicode_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            for provider, session_key in [("codex", "session_id"), ("copilot", "sessionId")]:
                payload = {"prompt": "Keep café names intact", "cwd": directory, session_key: "session-1"}
                event = capture(io.BytesIO(json.dumps(payload).encode()), Path(directory) / "inbox", provider)
                saved = json.loads((Path(directory) / "inbox" / (event["id"] + ".json")).read_text())
                self.assertEqual(saved["prompt"], payload["prompt"])
                self.assertEqual(saved["sessionId"], "session-1")
                self.assertEqual(saved["source"], provider)

    def test_invalid_or_oversized_input_creates_no_event(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory) / "inbox"
            for raw in [b"[]", b"x" * 65537, json.dumps({"prompt": "x", "cwd": "relative"}).encode()]:
                with self.assertRaises(ValueError):
                    capture(io.BytesIO(raw), inbox, "codex")
            self.assertFalse(inbox.exists())

    def test_retention_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory) / "inbox"
            inbox.mkdir()
            unrelated = inbox / "personal.json"
            unrelated.write_text("{}")
            payload = json.dumps({"prompt": "Explain retry limits", "cwd": directory}).encode()
            for _ in range(101):
                capture(io.BytesIO(payload), inbox, "codex")
            self.assertEqual(len(list(inbox.glob("*.json"))), 101)
            self.assertTrue(unrelated.exists())

