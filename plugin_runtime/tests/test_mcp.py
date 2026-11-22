"""Verify the plugin process contract without model calls or project writes."""

import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "plugin_runtime"), str(ROOT / "worker")]

from between_the_lines_plugin.mcp import MAX_MESSAGE_BYTES, TOOLS, call_tool, serve


class FixtureCoordinator:
    def __init__(self):
        self.calls = []

    def require_workspace(self, workspace):
        if workspace != "registered-project":
            raise ValueError("The project has no trusted captured session.")
        return workspace

    def pending(self, workspace):
        self.calls.append(workspace)
        return {"jobs": [{"id": "job-one"}]}

    def context(self, workspace, job_id):
        return {"jobId": job_id, "intent": {"prompt": "Preserve ties."}}

    def skip(self, workspace, job_id, reason):
        return {"skipped": job_id, "reason": reason}


class ProtocolTests(unittest.TestCase):
    def run_protocol(self, requests):
        source = b"".join((json.dumps(request) + "\n").encode() for request in requests)
        output = io.StringIO()
        coordinator = FixtureCoordinator()
        serve(io.BytesIO(source), output, coordinator)
        return [json.loads(line) for line in output.getvalue().splitlines()], coordinator

    def test_negotiation_tools_and_structured_results(self):
        result, coordinator = self.run_protocol([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "pending_annotations", "arguments": {"workspace": "registered-project"}}},
        ])
        self.assertEqual(3, len(result))
        self.assertEqual("2025-03-26", result[0]["result"]["protocolVersion"])
        self.assertEqual(8, len(result[1]["result"]["tools"]))
        self.assertEqual({"jobs": [{"id": "job-one"}]}, result[2]["result"]["structuredContent"])
        self.assertFalse(result[2]["result"]["isError"])
        self.assertEqual(["registered-project"], coordinator.calls)

    def test_unregistered_paths_and_unknown_arguments_are_tool_errors(self):
        for arguments in ({"workspace": "other-project"}, {"workspace": "registered-project", "prompt": "invented"}):
            with self.subTest(arguments=arguments):
                result, coordinator = self.run_protocol([
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "pending_annotations", "arguments": arguments}},
                ])
                self.assertTrue(result[1]["result"]["isError"])
                self.assertEqual([], coordinator.calls)

    def test_process_recovers_after_oversize_or_invalid_input(self):
        oversized = b"x" * (MAX_MESSAGE_BYTES + 5) + b"\n"
        ping = b'{"jsonrpc":"2.0","id":"ping","method":"ping"}\n'
        output = io.StringIO()
        serve(io.BytesIO(oversized + b"not-json\n" + ping), output, FixtureCoordinator())
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(3, len(messages))
        self.assertEqual({}, messages[-1]["result"])

    def test_tools_need_initialization_and_unknown_methods_are_bounded(self):
        result, _ = self.run_protocol([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 3, "method": "unknown"},
        ])
        self.assertEqual(-32002, result[0]["error"]["code"])
        self.assertEqual(-32601, result[2]["error"]["code"])

    def test_tool_annotations_and_write_arguments_are_explicit(self):
        read_only = {tool["name"] for tool in TOOLS if tool["annotations"]["readOnlyHint"]}
        self.assertEqual({"pending_annotations", "get_annotation_context", "context_history"}, read_only)
        for tool in TOOLS:
            self.assertFalse(tool["annotations"]["openWorldHint"])
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            self.assertNotIn("source", tool["inputSchema"]["properties"])
            self.assertNotIn("prompt", tool["inputSchema"]["properties"])

    def test_required_review_reason_is_checked_before_apply(self):
        with self.assertRaises(ValueError):
            call_tool(FixtureCoordinator(), "apply_annotation", {"workspace": "registered-project", "jobId": "x", "previewId": "y", "supportReason": ""})

    def test_preview_dispatches_only_bound_job_fields(self):
        arguments = {"workspace": "registered-project", "jobId": "job-one", "comment": "Preserve ties.", "passageId": "p0", "reason": "Stable ordering."}
        with patch("between_the_lines_plugin.annotations.preview_annotation", return_value={"previewId": "p"}) as preview:
            self.assertEqual({"previewId": "p"}, call_tool(FixtureCoordinator(), "preview_annotation", arguments))
            preview.assert_called_once_with(unittest.mock.ANY, "registered-project", "job-one", "Preserve ties.", "p0", "Stable ordering.")


if __name__ == "__main__":
    unittest.main()
