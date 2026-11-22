"""Build a Windows Codex plugin with local tools and no inference dependencies."""

import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_NAME = "between-the-lines-plugin"
TEMPLATE_FILES = (".codex-plugin/plugin.json", ".mcp.json",
                  "hooks/hooks.json", "scripts/invoke-hook.ps1", "skills/annotate/SKILL.md", "README.md")
DEPENDENCIES = ("tree-sitter", "tree-sitter-java", "pyinstaller")
REQUIRED_TOOLS = {"pending_annotations", "get_annotation_context", "preview_annotation",
                  "apply_annotation", "skip_annotation", "undo_annotation", "context_history", "retire_context"}


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_hashes(root=ROOT):
    paths = list((root / "plugin_runtime/between_the_lines_plugin").glob("*.py"))
    paths += list((root / "worker/btl_agent").glob("*.py"))
    paths += [root / "plugin_runtime/plugin.spec"]
    return {path.relative_to(root).as_posix(): sha256(path) for path in sorted(paths)}


def template_files(root=ROOT):
    template = root / "codex-plugin"
    paths = [template / name for name in TEMPLATE_FILES]
    if any(not path.is_file() or path.is_symlink() for path in paths):
        raise ValueError("Plugin template is incomplete or contains symbolic links.")
    return paths


def windows_hook_command(event):
    """Resolve the documented plugin environment inside the launched shell."""
    script = "$ProgressPreference='SilentlyContinue'; & (Join-Path $env:PLUGIN_ROOT 'scripts\\invoke-hook.ps1') -Event " + event
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded


def validate_template(root=ROOT):
    template_files(root)
    template = root / "codex-plugin"
    if (template / "plugin.json").exists():
        raise ValueError("An active portable plugin.json hides runtime declarations in the verified Codex 0.164 client.")
    installer = template / "Install.cmd"
    if not installer.is_file() or installer.is_symlink():
        raise ValueError("The outer installer is required to prepare absolute launch paths for supported clients.")
    manifest = json.loads((template / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    if manifest.get("name") != "between-the-lines" or not isinstance(manifest.get("version"), str) or not manifest["version"]:
        raise ValueError("The canonical compatibility manifest needs its stable identity and version.")
    if (manifest.get("mcpServers") != "./.mcp.json" or manifest.get("hooks") != "./hooks/hooks.json"
            or manifest.get("skills") != "./skills/"):
        raise ValueError("The compatibility manifest must explicitly declare its packaged runtime components.")
    mcp = json.loads((template / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["between-the-lines"]
    expected_command = "${PLUGIN_ROOT}/runtime/between-the-lines-plugin/between-the-lines-plugin.exe"
    if mcp.get("command") != expected_command or mcp.get("args") != ["--mcp"] or mcp.get("type", "stdio") != "stdio":
        raise ValueError("The plugin must launch its bundled local MCP executable.")
    hooks = json.loads((template / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
    if set(hooks) != {"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}:
        raise ValueError("The plugin must register the four verified lifecycle events.")
    for event, groups in hooks.items():
        command = groups[0]["hooks"][0]["commandWindows"]
        if command != windows_hook_command(event):
            raise ValueError("Hook launch commands must use packaged scripts without changing execution policy.")
    catalog = json.loads((template / "marketplace.json").read_text(encoding="utf-8"))
    entries = catalog.get("plugins", [])
    if len(entries) != 1 or entries[0].get("source") != {"source": "local", "path": "./codex-plugin"}:
        raise ValueError("The marketplace must resolve the plugin inside the extracted bundle.")
    return manifest, catalog


def license_file(path):
    return any(part.casefold() == "licenses" for part in path.parts) or path.name.casefold().startswith(("license", "copying", "notice"))


def bundle_licenses(destination):
    destination.mkdir(parents=True, exist_ok=True)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise ValueError("The Python distribution license is required for this Windows package.")
    target = destination / "Python/LICENSE.txt"
    target.parent.mkdir(exist_ok=True)
    shutil.copy2(python_license, target)
    records = [{"name": "Python", "version": platform.python_version(), "licenses": ["Python/LICENSE.txt"]}]
    for name in DEPENDENCIES:
        distribution = importlib.metadata.distribution(name)
        copied = []
        for relative in distribution.files or []:
            if not license_file(relative):
                continue
            original = Path(distribution.locate_file(relative))
            if not original.is_file():
                continue
            # Wheel metadata paths are retained so nested upstream notices stay distinct.
            target = destination / name / relative
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("A dependency license path escapes the license directory.")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            copied.append(target.relative_to(destination).as_posix())
        if not copied:
            raise ValueError(f"No license notice found for {name}.")
        records.append({"name": name, "version": distribution.version, "licenses": sorted(copied)})
    (destination / "index.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return records


def validate_runtime(runtime):
    runtime = Path(runtime)
    if not (runtime / "between-the-lines-plugin.exe").is_file():
        raise ValueError("The bundled plugin executable is missing.")
    files = [path for path in runtime.rglob("*") if path.is_file()]
    for path in files:
        relative = path.relative_to(runtime)
        if path.is_symlink() or not path.resolve().is_relative_to(runtime.resolve()):
            raise ValueError("Runtime files must stay inside the bundle.")
        name = relative.as_posix().casefold()
        if (any(value in name for value in ("llama", "ggml", "torch", "transformers", "numpy"))
                or path.suffix.casefold() in {".gguf", ".safetensors", ".onnx", ".sqlite3", ".pem", ".key"}):
            raise ValueError(f"Unexpected inference, data or credential file in runtime: {relative}")
    if not any("tree_sitter" in path.as_posix() and path.suffix.lower() in {".dll", ".pyd"} for path in files):
        raise ValueError("The frozen runtime is missing its native parsers.")
    notices = runtime / "third-party-licenses/index.json"
    if not notices.is_file():
        raise ValueError("The runtime license index is missing.")
    dependencies = json.loads(notices.read_text(encoding="utf-8"))
    if not {"Python", *DEPENDENCIES}.issubset({entry.get("name") for entry in dependencies}):
        raise ValueError("The runtime must retain Python, parser and bootloader license notices.")
    for entry in dependencies:
        for name in entry["licenses"]:
            path = runtime / "third-party-licenses" / name
            if not path.resolve().is_relative_to((runtime / "third-party-licenses").resolve()) or not path.is_file():
                raise ValueError("A required third-party notice is missing or outside its directory.")
    return files


def smoke_runtime(executable, data_dir):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "between-the-lines-package-check", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    environment = os.environ.copy()
    environment.update(BETWEEN_THE_LINES_PLUGIN_DATA=str(data_dir), PYTHONUTF8="1")
    result = subprocess.run([str(executable), "--mcp"], input="\n".join(json.dumps(request) for request in requests) + "\n",
                            text=True, encoding="utf-8", capture_output=True, timeout=30, check=True, env=environment)
    messages = [json.loads(line) for line in result.stdout.splitlines()]
    responses = {message.get("id"): message for message in messages}
    if any("error" in response for response in responses.values()) or 1 not in responses or 2 not in responses:
        raise ValueError("Frozen MCP initialization failed.")
    tools = {tool["name"] for tool in responses[2]["result"]["tools"]}
    if not REQUIRED_TOOLS.issubset(tools):
        raise ValueError("The frozen runtime did not expose the required annotation tools.")
    return {"initialized": True, "tools": sorted(tools)}


def write_package(output, runtime, root=ROOT):
    manifest, catalog = validate_template(root)
    files = validate_runtime(runtime)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in template_files(root):
            archive.write(path, "codex-plugin/" + path.relative_to(root / "codex-plugin").as_posix())
        for path in files:
            archive.write(path, "codex-plugin/runtime/between-the-lines-plugin/" + path.relative_to(runtime).as_posix())
        archive.write(root / "LICENSE", "codex-plugin/LICENSE")
        archive.write(root / "codex-plugin/Install.cmd", "Install.cmd")
        archive.writestr(".agents/plugins/marketplace.json", json.dumps(catalog, indent=2) + "\n")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("The package ZIP failed its integrity check.")
        names = archive.namelist()
        if any(name.startswith("/") or ".." in Path(name).parts for name in names):
            raise ValueError("The package contains an escaping path.")
    return {"name": manifest["name"], "version": manifest["version"], "file": output.name,
            "bytes": output.stat().st_size, "sha256": sha256(output), "runtimeFiles": len(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="Package an existing runtime only when its source manifest matches.")
    args = parser.parse_args()
    if platform.system() != "Windows" or platform.machine() not in {"AMD64", "x86_64"}:
        raise SystemExit("Build this Windows x64 package on Windows x64.")
    manifest, _ = validate_template()
    sources = source_hashes()
    build = ROOT / "build/codex-plugin"
    build.resolve().relative_to((ROOT / "build").resolve())
    build.mkdir(parents=True, exist_ok=True)
    runtime = build / "dist" / RUNTIME_NAME
    info_path = build / "build-info.json"
    if not args.skip_build:
        for name in DEPENDENCIES:
            importlib.metadata.version(name)
        (build / "plugin_entry.py").write_text("from between_the_lines_plugin.__main__ import main\n\nmain()\n", encoding="utf-8")
        subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                        "--distpath", str(build / "dist"), "--workpath", str(build / "pyinstaller"),
                        str(ROOT / "plugin_runtime/plugin.spec")], cwd=ROOT, check=True)
        dependencies = bundle_licenses(runtime / "third-party-licenses")
        if source_hashes() != sources:
            raise SystemExit("Plugin or shared sources changed during the build. Rebuild the runtime.")
        smoke = smoke_runtime(runtime / "between-the-lines-plugin.exe", build / "smoke-data")
        info_path.write_text(json.dumps({"target": "win32-x64", "sources": sources,
                                       "dependencies": dependencies, "smoke": smoke}, indent=2) + "\n", encoding="utf-8")
    if not info_path.is_file():
        raise SystemExit("No matching plugin runtime has been built.")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    if info.get("target") != "win32-x64" or info.get("sources") != sources:
        raise SystemExit("The frozen plugin runtime is stale. Build again without --skip-build.")
    artifacts = ROOT / "artifacts"
    output = artifacts / f"between-the-lines-codex-{manifest['version']}-win32-x64.zip"
    result = write_package(output, runtime)
    result.update(target="win32-x64", sources=sources, dependencies=info["dependencies"],
                  smoke=info["smoke"], inference="active Codex model; no local weights bundled")
    (artifacts / "codex-plugin-build-info.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (artifacts / "CODEX-PLUGIN-SHA256SUMS.txt").write_text(f"{result['sha256']}  {output.name}\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("file", "bytes", "sha256", "runtimeFiles")}, indent=2))


if __name__ == "__main__":
    main()
