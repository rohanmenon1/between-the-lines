"""Check bounded, repeatable package preparation without touching Codex settings."""

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from between_the_lines_plugin.install import EXECUTABLE, PLACEHOLDER, prepare_install


class InstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.outer = self.directory / "package with spaces"
        environment = patch.dict(os.environ, {"BETWEEN_THE_LINES_PLUGIN_DATA": str(self.directory / "history")})
        environment.start()
        self.addCleanup(environment.stop)
        self.root = self.outer / "codex-plugin"
        for name, text in {
            ".codex-plugin/plugin.json": json.dumps({"name": "between-the-lines", "mcpServers": "./.mcp.json"}),
            ".mcp.json": json.dumps({"mcpServers": {"between-the-lines": {"command": PLACEHOLDER, "args": ["--mcp"], "cwd": "${PLUGIN_ROOT}"}}}),
            EXECUTABLE.as_posix(): "INERT TEST EXECUTABLE",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        catalog = self.outer / ".agents/plugins/marketplace.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text("{}", encoding="utf-8")

    def test_prepares_only_this_bundle_and_is_idempotent(self):
        original_files = {p.relative_to(self.outer): p.read_bytes() for p in self.outer.rglob("*") if p.is_file()}
        result = prepare_install(self.root)
        config = json.loads((self.root / ".mcp.json").read_text())
        self.assertEqual(config["mcpServers"]["between-the-lines"], {
            "command": str(self.root / EXECUTABLE), "args": ["--mcp"], "cwd": str(self.root),
            "env": {"BETWEEN_THE_LINES_PLUGIN_DATA": str(self.directory / "history")}})
        prepared_bytes = (self.root / ".mcp.json").read_bytes()
        self.assertEqual(prepare_install(self.root), result)
        self.assertEqual((self.root / ".mcp.json").read_bytes(), prepared_bytes)
        self.assertEqual(result["nextCommand"], ["codex", "plugin", "marketplace", "add", str(self.outer)])
        for relative, before in original_files.items():
            if relative != Path("codex-plugin/.mcp.json"):
                self.assertEqual((self.outer / relative).read_bytes(), before)

    def test_moved_bundle_can_be_prepared_again(self):
        prepare_install(self.root)
        moved = self.outer.parent / "moved package"
        self.outer.resolve().relative_to(self.directory)
        moved.resolve().relative_to(self.directory)
        shutil.move(str(self.outer), str(moved))
        root = moved / "codex-plugin"
        prepare_install(root)
        self.assertEqual(json.loads((root / ".mcp.json").read_text())["mcpServers"]["between-the-lines"]["command"], str(root / EXECUTABLE))

    def test_custom_configuration_is_not_overwritten(self):
        path = self.root / ".mcp.json"
        data = json.loads(path.read_text())
        data["mcpServers"]["between-the-lines"]["command"] = "some-other-program"
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "customized"):
            prepare_install(self.root)
        self.assertEqual(path.read_bytes(), before)

    def test_incomplete_outer_package_does_not_change_launch_config(self):
        (self.outer / ".agents/plugins/marketplace.json").unlink()
        before = (self.root / ".mcp.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "complete ZIP"):
            prepare_install(self.root)
        self.assertEqual((self.root / ".mcp.json").read_bytes(), before)

    def test_relative_data_override_is_rejected_before_config_changes(self):
        before = (self.root / ".mcp.json").read_bytes()
        with patch.dict(os.environ, {"BETWEEN_THE_LINES_PLUGIN_DATA": "relative/history"}):
            with self.assertRaisesRegex(ValueError, "absolute"):
                prepare_install(self.root)
        self.assertEqual((self.root / ".mcp.json").read_bytes(), before)

    def test_unrelated_environment_customization_is_preserved(self):
        path = self.root / ".mcp.json"
        data = json.loads(path.read_text())
        data["mcpServers"]["between-the-lines"]["env"] = {"OTHER_SETTING": "fixture"}
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "customized MCP environment"):
            prepare_install(self.root)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
