"""Verify source release exclusions and reproducibility without building a release."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from package_codex_plugin_source import REQUIRED_FILES, package_source, source_files


class SourcePackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.checkout = self.directory / "checkout"
        self.checkout.mkdir()
        for name in REQUIRED_FILES:
            path = self.checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("source fixture\n", encoding="utf-8")
        manifest = self.checkout / "codex-plugin/.codex-plugin/plugin.json"
        manifest.write_text(json.dumps({"name": "between-the-lines", "version": "0.1.0"}), encoding="utf-8")

    def test_archive_contains_rebuild_sources_but_no_local_state_or_binary(self):
        for relative in ("artifacts/live/auth.json", "models/model.gguf", "codex-plugin/runtime/private.exe",
                         "scripts/private/token.txt", "scripts/evaluation/.env", "scripts/evaluation/auth.json",
                         "plugin_runtime/.test-temp/source.py", "worker/bin/runtime.dll", "scripts/session.log"):
            path = self.checkout / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("DO NOT SHIP", encoding="utf-8")
        output = self.directory / "source.zip"
        result = package_source(output, self.checkout)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(set(REQUIRED_FILES) | {"SOURCE-MANIFEST.json"}, set(archive.namelist()))
            metadata = json.loads(archive.read("SOURCE-MANIFEST.json"))
            self.assertEqual(result["sourceFiles"], len(metadata["files"]))
            self.assertFalse(any("DO NOT SHIP" in archive.read(name).decode("utf-8") for name in archive.namelist()))

    def test_identical_source_produces_identical_archives_and_preserves_existing(self):
        first, second = self.directory / "first.zip", self.directory / "second.zip"
        left = package_source(first, self.checkout)
        right = package_source(second, self.checkout)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(left["sha256"], right["sha256"])
        with self.assertRaises(FileExistsError):
            package_source(first, self.checkout)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_missing_required_source_fails_without_creating_an_archive(self):
        (self.checkout / "plugin_runtime/between_the_lines_plugin/coordinator.py").unlink()
        with self.assertRaisesRegex(ValueError, "lacks source"):
            source_files(self.checkout)
        self.assertFalse((self.directory / "source.zip").exists())


if __name__ == "__main__":
    unittest.main()
