"""Test the distributable contract with inert fixture files, without inference."""

import importlib.util
import base64
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
specification = importlib.util.spec_from_file_location("package_codex_plugin", ROOT / "scripts/package_codex_plugin.py")
package = importlib.util.module_from_spec(specification)
specification.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(ROOT / "codex-plugin", self.root / "codex-plugin")
        (self.root / "LICENSE").write_text("Fixture license", encoding="utf-8")
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        (self.runtime / "between-the-lines-plugin.exe").write_bytes(b"INERT TEST FIXTURE")
        (self.runtime / "_internal/tree_sitter_java").mkdir(parents=True)
        (self.runtime / "_internal/tree_sitter_java/_binding.pyd").write_bytes(b"INERT PARSER FIXTURE")
        notices = self.runtime / "third-party-licenses"
        notices.mkdir()
        entries = []
        for name in ("Python", *package.DEPENDENCIES):
            path = notices / name / "LICENSE.txt"
            path.parent.mkdir()
            path.write_text("Fixture license", encoding="utf-8")
            entries.append({"name": name, "version": "fixture", "licenses": [name + "/LICENSE.txt"]})
        (notices / "index.json").write_text(json.dumps(entries), encoding="utf-8")

    def test_catalog_resolves_from_extracted_outer_folder(self):
        output = self.root / "plugin.zip"
        result = package.write_package(output, self.runtime, self.root)
        with zipfile.ZipFile(output) as archive:
            catalog = json.loads(archive.read(".agents/plugins/marketplace.json"))
            entry = catalog["plugins"][0]
            manifest_path = entry["source"]["path"].removeprefix("./") + "/.codex-plugin/plugin.json"
            self.assertIn(manifest_path, archive.namelist())
            manifest = json.loads(archive.read(manifest_path))
            self.assertEqual(manifest["version"], result["version"])
            self.assertIn("codex-plugin/runtime/between-the-lines-plugin/between-the-lines-plugin.exe", archive.namelist())
            self.assertIn("codex-plugin/runtime/between-the-lines-plugin/third-party-licenses/tree-sitter-java/LICENSE.txt", archive.namelist())
            self.assertNotIn("codex-plugin/marketplace.json", archive.namelist())
            self.assertNotIn("codex-plugin/plugin.json", archive.namelist())
            self.assertNotIn("codex-plugin/mcp.json", archive.namelist())
            self.assertNotIn("codex-plugin/portable-manifest.example.json", archive.namelist())
            self.assertIn("Install.cmd", archive.namelist())
            self.assertNotIn("codex-plugin/Install.cmd", archive.namelist())
            installer = archive.read("Install.cmd").decode("utf-8")
            self.assertIn("--prepare-install", installer)
            self.assertIn("--prepare-only", installer)
        self.assertEqual(package.sha256(output), result["sha256"])

    def test_unlisted_files_are_not_shipped(self):
        (self.root / "codex-plugin/.env").write_text("TEST_SECRET=not-a-real-secret", encoding="utf-8")
        (self.root / "codex-plugin/accidental.sqlite3").write_bytes(b"fixture")
        output = self.root / "plugin.zip"
        package.write_package(output, self.runtime, self.root)
        with zipfile.ZipFile(output) as archive:
            self.assertFalse(any(name.endswith((".env", ".sqlite3")) for name in archive.namelist()))

    def test_inference_weights_libraries_and_credentials_are_rejected(self):
        for name in ("model.gguf", "llama.dll", "ggml-cpu.dll", "api.key", "history.sqlite3", "numpy.dat"):
            path = self.runtime / name
            path.write_bytes(b"fixture")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unexpected"):
                package.validate_runtime(self.runtime)
            path.unlink()

    def test_missing_native_parser_or_executable_is_rejected(self):
        parser = self.runtime / "_internal/tree_sitter_java/_binding.pyd"
        parser.unlink()
        with self.assertRaisesRegex(ValueError, "native parsers"):
            package.validate_runtime(self.runtime)
        (self.runtime / "between-the-lines-plugin.exe").unlink()
        with self.assertRaisesRegex(ValueError, "executable"):
            package.validate_runtime(self.runtime)

    def test_missing_dependency_notice_is_rejected(self):
        (self.runtime / "third-party-licenses/Python/LICENSE.txt").unlink()
        with self.assertRaisesRegex(ValueError, "notice"):
            package.validate_runtime(self.runtime)

    def test_license_index_cannot_omit_a_required_distribution(self):
        index = self.runtime / "third-party-licenses/index.json"
        index.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "retain Python"):
            package.validate_runtime(self.runtime)

    def test_invalid_canonical_manifest_identity_is_rejected(self):
        path = self.root / "codex-plugin/.codex-plugin/plugin.json"
        data = json.loads(path.read_text())
        data["name"] = "some-other-plugin"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "identity"):
            package.validate_template(self.root)

    def test_launch_contract_cannot_point_outside_bundled_runtime(self):
        path = self.root / "codex-plugin/.mcp.json"
        data = json.loads(path.read_text())
        data["mcpServers"]["between-the-lines"]["command"] = "python"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "bundled local"):
            package.validate_template(self.root)

    def test_active_portable_manifest_cannot_mask_verified_runtime_declarations(self):
        path = self.root / "codex-plugin/plugin.json"
        example = self.root / "codex-plugin/portable-manifest.example.json"
        shutil.copy2(example, path)
        with self.assertRaisesRegex(ValueError, "active portable"):
            package.validate_template(self.root)

    def test_package_requires_local_launch_preparation_installer(self):
        (self.root / "codex-plugin/Install.cmd").unlink()
        with self.assertRaisesRegex(ValueError, "outer installer"):
            package.validate_template(self.root)

    def test_windows_hook_resolves_plugin_environment_in_its_own_shell(self):
        for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"):
            command = package.windows_hook_command(event)
            decoded = base64.b64decode(command.split()[-1]).decode("utf-16le")
            self.assertEqual(decoded, "$ProgressPreference='SilentlyContinue'; & (Join-Path $env:PLUGIN_ROOT 'scripts\\invoke-hook.ps1') -Event " + event)
            self.assertNotIn("ExecutionPolicy", command)

    def test_marketplace_cannot_escape_distribution(self):
        path = self.root / "codex-plugin/marketplace.json"
        data = json.loads(path.read_text())
        data["plugins"][0]["source"]["path"] = "../somewhere-else"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "extracted bundle"):
            package.validate_template(self.root)


if __name__ == "__main__":
    unittest.main()
