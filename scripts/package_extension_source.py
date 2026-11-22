"""Package versioned extension source without binaries, credentials or local state."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

from package_codex_plugin import validate_template
from package_codex_plugin_source import ROOT, _member, source_files


REQUIRED_FILES = (
    "CHANGELOG.md",
    "src/extension.ts", "src/runtime.ts", "src/view.ts", "src/automaticController.ts",
    "media/sidebar.css", "media/sidebar.js", "media/icon.svg", "config/model.json",
    "scripts/build_worker.py", "scripts/package_extension.py", "scripts/package_extension_source.py",
    "scripts/tests/test_extension_source_package.py",
)


def snapshot(root=ROOT):
    """Share the complete project's source allowlist; version this release as the VSIX."""
    root = Path(root).resolve(strict=True)
    files = source_files(root)
    changelog = root / "CHANGELOG.md"
    if not changelog.is_file() or changelog.is_symlink() or not changelog.resolve().is_relative_to(root):
        raise ValueError("The release changelog is missing or linked.")
    files["CHANGELOG.md"] = changelog
    if any(name not in files for name in REQUIRED_FILES):
        raise ValueError("The checkout lacks source needed to rebuild the extension.")
    content = {name: path.read_bytes() for name, path in files.items()}
    if any(len(data) > 5 * 1024 * 1024 for data in content.values()) or sum(map(len, content.values())) > 32 * 1024 * 1024:
        raise ValueError("The source archive exceeds its expected file or total size budget.")
    package = json.loads(content["package.json"])
    version = package.get("version")
    if package.get("name") != "between-the-lines" or not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", version):
        raise ValueError("The extension package needs its name and release version.")
    lock = json.loads(content["package-lock.json"])
    if lock.get("name") != package["name"] or lock.get("version") != version or lock.get("packages", {}).get("", {}).get("version") != version:
        raise ValueError("The package lock must match the extension release version.")
    # Include the optional plugin's source templates, never a prepared install's
    # absolute executable, user storage directory or customized hook commands.
    validate_template(root)
    expected = {"mcpServers": {"between-the-lines": {
        "command": "${PLUGIN_ROOT}/runtime/between-the-lines-plugin/between-the-lines-plugin.exe",
        "args": ["--mcp"], "cwd": "${PLUGIN_ROOT}",
    }}}
    if json.loads(content["codex-plugin/.mcp.json"]) != expected:
        raise ValueError("Restore the plugin MCP source template before packaging; prepared machine paths cannot ship.")
    manifest = {"schemaVersion": 1, "name": "between-the-lines extension source", "version": version,
                "files": [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                          for name, data in sorted(content.items())]}
    return files, content, manifest


def package_source(output, root=ROOT):
    files, content, manifest = snapshot(root)
    output = Path(output).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(content.items()):
                archive.writestr(_member(name), data, compresslevel=9)
            archive.writestr(_member("SOURCE-MANIFEST.json"), json.dumps(manifest, indent=2) + "\n", compresslevel=9)
    if any(path.read_bytes() != content[name] for name, path in files.items()):
        raise ValueError("Source changed while packaging. Preserve this failed attempt and use a fresh output path after edits settle.")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("The source ZIP failed its integrity check.")
    return {"file": output.name, "version": manifest["version"], "sourceFiles": len(content),
            "bytes": output.stat().st_size, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New source ZIP path; existing files are never replaced.")
    parser.add_argument("--dry-run", action="store_true", help="Validate source without creating an archive.")
    arguments = parser.parse_args()
    try:
        if arguments.dry_run:
            _, content, manifest = snapshot()
            result = {"version": manifest["version"], "sourceFiles": len(content),
                      "sourceBytes": sum(map(len, content.values())), "archiveCreated": False}
        else:
            version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
            output = arguments.output or ROOT / "artifacts" / f"between-the-lines-{version}-source.zip"
            result = package_source(output)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"Source packaging stopped: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
