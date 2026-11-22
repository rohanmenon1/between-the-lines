"""Check release source integrity and privacy using disposable checkouts."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from package_codex_plugin import windows_hook_command
from package_codex_plugin_source import REQUIRED_FILES as PROJECT_FILES
from package_extension_source import REQUIRED_FILES, package_source, snapshot


class ExtensionSourcePackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.checkout = self.directory / "checkout"
        for name in set(PROJECT_FILES) | set(REQUIRED_FILES):
            self.write(name, "source fixture\n")
        self.write_json("package.json", {"name": "between-the-lines", "version": "0.4.1"})
        self.write_json("package-lock.json", {"name": "between-the-lines", "version": "0.4.1",
                                             "packages": {"": {"version": "0.4.1"}}})
        self.write_json("codex-plugin/.codex-plugin/plugin.json", {
            "name": "between-the-lines", "version": "0.1.0", "mcpServers": "./.mcp.json",
            "hooks": "./hooks/hooks.json", "skills": "./skills/"})
        self.mcp = {"mcpServers": {"between-the-lines": {
            "command": "${PLUGIN_ROOT}/runtime/between-the-lines-plugin/between-the-lines-plugin.exe",
            "args": ["--mcp"], "cwd": "${PLUGIN_ROOT}"}}}
        self.write_json("codex-plugin/.mcp.json", self.mcp)
        self.write_json("codex-plugin/marketplace.json", {"plugins": [{
            "source": {"source": "local", "path": "./codex-plugin"}}]})
        self.write_json("codex-plugin/hooks/hooks.json", {"hooks": {
            event: [{"hooks": [{"commandWindows": windows_hook_command(event)}]}]
            for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")}})

    def write(self, name, text):
        path = self.checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_json(self, name, value):
        self.write(name, json.dumps(value))

    def test_archive_uses_extension_version_and_checksums_every_source(self):
        output = self.directory / "source.zip"
        result = package_source(output, self.checkout)
        self.assertEqual(result["version"], "0.4.1")
        with zipfile.ZipFile(output) as archive:
            manifest = json.loads(archive.read("SOURCE-MANIFEST.json"))
            self.assertEqual(manifest["version"], "0.4.1")
            self.assertEqual(set(archive.namelist()) - {"SOURCE-MANIFEST.json"},
                             {entry["path"] for entry in manifest["files"]})
            for entry in manifest["files"]:
                data = archive.read(entry["path"])
                self.assertEqual(entry["bytes"], len(data))
                self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())

    def test_private_state_and_binaries_are_excluded(self):
        for name in ("artifacts/report.json", "models/model.gguf", "scripts/private/token.txt",
                     "scripts/.env", "scripts/auth.json", "worker/bin/runtime.dll",
                     "codex-plugin/runtime/private.exe", "src/history.sqlite3", ".codex/hooks.json"):
            self.write(name, "PRIVATE_STATE")
        output = self.directory / "source.zip"
        package_source(output, self.checkout)
        with zipfile.ZipFile(output) as archive:
            self.assertFalse(any(b"PRIVATE_STATE" in archive.read(name) for name in archive.namelist()))

    def test_identical_source_is_reproducible_and_existing_output_is_preserved(self):
        first, second = self.directory / "first.zip", self.directory / "second.zip"
        package_source(first, self.checkout)
        package_source(second, self.checkout)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with self.assertRaises(FileExistsError):
            package_source(first, self.checkout)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_prepared_machine_paths_fail_before_archive_creation(self):
        self.mcp["mcpServers"]["between-the-lines"]["env"] = {
            "BETWEEN_THE_LINES_PLUGIN_DATA": "C:/Users/private/history"}
        self.write_json("codex-plugin/.mcp.json", self.mcp)
        with self.assertRaisesRegex(ValueError, "prepared machine paths"):
            package_source(self.directory / "source.zip", self.checkout)
        self.assertFalse((self.directory / "source.zip").exists())

    def test_missing_extension_source_or_mismatched_lock_fails(self):
        self.write_json("package-lock.json", {"name": "between-the-lines", "version": "0.4.0"})
        with self.assertRaisesRegex(ValueError, "package lock"):
            snapshot(self.checkout)
        (self.checkout / "src/extension.ts").unlink()
        with self.assertRaisesRegex(ValueError, "rebuild the extension"):
            snapshot(self.checkout)


if __name__ == "__main__":
    unittest.main()
