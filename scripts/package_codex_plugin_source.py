"""Package reproducible project source for the Codex plugin, without local state."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    "AGENTS.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "README.md", ".gitignore", ".vscodeignore",
    "package.json", "package-lock.json", "tsconfig.json", "worker/requirements.txt", "worker/worker.spec",
    "plugin_runtime/plugin.spec", "plugin_runtime/requirements-build.txt",
)
SOURCE_DIRECTORIES = (
    ".github/workflows", ".vscode", "config", "docs", "eval", "media", "scripts", "src",
    "worker/btl_agent", "worker/tests", "worker/licenses", "plugin_runtime/between_the_lines_plugin",
    "plugin_runtime/tests", "codex-plugin",
)
REQUIRED_FILES = ROOT_FILES + (
    "codex-plugin/.codex-plugin/plugin.json", "codex-plugin/.mcp.json", "codex-plugin/hooks/hooks.json",
    "codex-plugin/marketplace.json", "codex-plugin/README.md",
    "codex-plugin/Install.cmd",
    "codex-plugin/skills/annotate/SKILL.md", "codex-plugin/scripts/invoke-hook.ps1",
    "plugin_runtime/between_the_lines_plugin/__init__.py", "plugin_runtime/between_the_lines_plugin/__main__.py", "plugin_runtime/between_the_lines_plugin/coordinator.py",
    "plugin_runtime/between_the_lines_plugin/store.py", "plugin_runtime/between_the_lines_plugin/annotations.py",
    "plugin_runtime/between_the_lines_plugin/mcp.py", "worker/btl_agent/__init__.py", "worker/btl_agent/history.py",
    "plugin_runtime/between_the_lines_plugin/install.py",
    "worker/btl_agent/parsing.py", "worker/btl_agent/patches.py", "worker/btl_agent/synthesis.py",
    "worker/btl_agent/types.py", "scripts/package_codex_plugin.py", "scripts/verify_codex_plugin.py",
    "scripts/package_codex_plugin_source.py",
)
EXCLUDED_DIRECTORIES = {
    "__pycache__", ".pytest_cache", ".test-temp", ".temp", ".git", ".venv", "node_modules", "models",
    "artifacts", "build", "dist", "runtime", "bin", "private", ".codex", ".claude",
}
EXCLUDED_SUFFIXES = {
    ".pyc", ".pyo", ".exe", ".dll", ".pyd", ".so", ".dylib", ".gguf", ".safetensors",
    ".onnx", ".sqlite3", ".sqlite", ".db", ".pem", ".key", ".log", ".zip", ".vsix",
}
EXCLUDED_NAMES = {"auth.json", "auth.toml", "credentials.json", "credentials.toml", "secrets.json", "tokens.json"}


def _linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _include_file(path):
    name = path.name.casefold()
    return (not name.startswith(".env") and name not in EXCLUDED_NAMES
            and path.suffix.casefold() not in EXCLUDED_SUFFIXES)


def source_files(root=ROOT):
    """An explicit source allowlist avoids user data and generated test evidence."""
    root = Path(root).resolve(strict=True)
    paths = {root / relative for relative in ROOT_FILES}
    for relative in SOURCE_DIRECTORIES:
        top = root / relative
        if not top.exists():
            continue
        if _linked(top) or not top.resolve().is_relative_to(root):
            raise ValueError("Source directories must remain inside the checkout without links.")
        for directory, names, filenames in os.walk(top, followlinks=False):
            parent = Path(directory)
            names[:] = sorted(name for name in names if name.casefold() not in EXCLUDED_DIRECTORIES)
            for name in names:
                child = parent / name
                if _linked(child) or not child.resolve().is_relative_to(root):
                    raise ValueError("Source directories cannot contain links outside their checkout.")
            paths.update(parent / name for name in filenames if _include_file(parent / name))
    selected = {}
    for path in sorted(paths):
        if not path.is_file() or _linked(path) or not path.resolve().is_relative_to(root):
            raise ValueError(f"Required source file is missing or linked: {path.relative_to(root)}")
        selected[path.relative_to(root).as_posix()] = path
    if any(relative not in selected for relative in REQUIRED_FILES):
        raise ValueError("The checkout lacks source, templates or scripts needed to rebuild the Codex plugin.")
    return selected


def _snapshot(root):
    files = source_files(root)
    content = {}
    for relative, path in files.items():
        data = path.read_bytes()
        if len(data) > 5 * 1024 * 1024:
            raise ValueError(f"Unexpected source file over 5 MiB: {relative}")
        content[relative] = data
    if sum(map(len, content.values())) > 32 * 1024 * 1024:
        raise ValueError("The source archive exceeds its expected 32 MiB input budget.")
    plugin = json.loads(content["codex-plugin/.codex-plugin/plugin.json"])
    version = plugin.get("version")
    if plugin.get("name") != "between-the-lines" or not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("The plugin compatibility manifest needs its name and release version.")
    manifest = {"schemaVersion": 1, "name": "between-the-lines Codex plugin source", "version": version,
                "files": [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                          for name, data in sorted(content.items())]}
    return files, content, manifest


def _member(name):
    # Normalize timestamps and permissions; no absolute source paths or build
    # timestamps enter the ZIP. Repeated packaging of identical bytes is stable.
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def package_source(output, root=ROOT):
    files, content, manifest = _snapshot(root)
    output = Path(output).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Never silently replace a previously reviewed release or the VSIX source ZIP.
    with output.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative, data in sorted(content.items()):
                archive.writestr(_member(relative), data, compresslevel=9)
            archive.writestr(_member("SOURCE-MANIFEST.json"), json.dumps(manifest, indent=2) + "\n", compresslevel=9)
    if any(path.read_bytes() != content[relative] for relative, path in files.items()):
        raise ValueError("Source changed while packaging. Preserve this failed attempt and use a fresh output path after edits settle.")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("The source ZIP failed its integrity check.")
    return {"file": output.name, "version": manifest["version"], "sourceFiles": len(content),
            "bytes": output.stat().st_size, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="A new source ZIP path; existing releases are never overwritten.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and count source files without creating an archive.")
    args = parser.parse_args()
    try:
        if args.dry_run:
            _, content, manifest = _snapshot(ROOT)
            result = {"version": manifest["version"], "sourceFiles": len(content), "sourceBytes": sum(map(len, content.values())), "archiveCreated": False}
        else:
            version = json.loads((ROOT / "codex-plugin/.codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
            output = args.output or ROOT / "artifacts" / f"between-the-lines-codex-{version}-source.zip"
            result = package_source(output)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"Source packaging stopped: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
