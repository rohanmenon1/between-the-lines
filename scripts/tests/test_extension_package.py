"""Check package freshness and immutable output without building native code."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from package_extension import package_extension


class ExtensionPackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write("package.json", json.dumps({"name": "between-the-lines", "version": "0.4.1"}))
        source = self.write("worker/btl_agent/__main__.py", "print('fixture')\n")
        self.write("worker/bin/btl-worker/btl-worker.exe", "fixture executable")
        self.info = {"target": "win32-x64", "sources": {
            "worker/btl_agent/__main__.py": hashlib.sha256(source.read_bytes()).hexdigest()}}
        self.write_info()
        self.write("node_modules/@vscode/vsce/package.json", json.dumps({"bin": {"vsce": "vsce"}}))
        self.output = self.root / "release" / "candidate.vsix"

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_info(self):
        self.write("worker/bin/build-info.json", json.dumps(self.info))

    def fake_package(self, command, **kwargs):
        self.assertEqual(command[command.index("--target") + 1], "win32-x64")
        with zipfile.ZipFile(command[command.index("--out") + 1], "w") as archive:
            for name in ("config/model.json", "worker/bin/btl-worker/btl-worker.exe",
                         "worker/bin/btl-worker/third-party-licenses/index.json",
                         "worker/bin/btl-worker/llama.dll"):
                archive.writestr("extension/" + name, "fixture")

    def test_new_output_is_validated_and_existing_package_is_preserved(self):
        with patch("package_extension.shutil.which", return_value="node"), \
                patch("package_extension.subprocess.run", side_effect=self.fake_package) as run:
            self.assertEqual(package_extension("win32-x64", self.output, self.root), self.output)
            original = self.output.read_bytes()
            with self.assertRaisesRegex(SystemExit, "already exists"):
                package_extension("win32-x64", self.output, self.root)
            self.assertEqual(self.output.read_bytes(), original)
            self.assertEqual(run.call_count, 1)

    def test_stale_worker_and_wrong_target_fail_before_packaging(self):
        with patch("package_extension.subprocess.run") as run:
            self.write("worker/btl_agent/__main__.py", "print('changed')\n")
            with self.assertRaisesRegex(SystemExit, "sources changed"):
                package_extension("win32-x64", self.output, self.root)
            self.info["target"] = "win32-arm64"
            self.write_info()
            with self.assertRaisesRegex(SystemExit, "architecture"):
                package_extension("win32-x64", self.output, self.root)
            run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_incomplete_package_never_becomes_release_output(self):
        def incomplete(command, **kwargs):
            with zipfile.ZipFile(command[command.index("--out") + 1], "w") as archive:
                archive.writestr("extension/config/model.json", "fixture")
        with patch("package_extension.shutil.which", return_value="node"), \
                patch("package_extension.subprocess.run", side_effect=incomplete):
            with self.assertRaisesRegex(SystemExit, "Package validation failed"):
                package_extension("win32-x64", self.output, self.root)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
