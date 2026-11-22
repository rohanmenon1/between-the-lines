"""Prepare this extracted bundle's launch path without editing Codex settings."""

import json
import os
from pathlib import Path
import sys
import tempfile

from .store import default_data_dir


EXECUTABLE = Path("runtime/between-the-lines-plugin/between-the-lines-plugin.exe")
PLACEHOLDER = "${PLUGIN_ROOT}/" + EXECUTABLE.as_posix()


def _plain_file(root, relative):
    path = root / relative
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("The extracted plugin package is incomplete.")
    for part in [path, *path.parents]:
        if part == root.parent:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Install from an extracted folder without linked package files.")
    return path


def prepare_install(plugin_root=None):
    """An explicit root is for tests; end users run the bundled executable."""
    if plugin_root is None:
        if not getattr(sys, "frozen", False):
            raise ValueError("Run --prepare-install with the executable in the extracted Windows package.")
        plugin_root = Path(sys.executable).absolute().parents[2]
    root = Path(plugin_root).absolute()
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise ValueError("Install from an extracted folder without links.")
    root = root.resolve(strict=True)
    manifest = json.loads(_plain_file(root, ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    if manifest.get("name") != "between-the-lines" or manifest.get("mcpServers") != "./.mcp.json":
        raise ValueError("This is not the expected between-the-lines plugin package.")
    executable = _plain_file(root, EXECUTABLE)
    catalog = root.parent / ".agents/plugins/marketplace.json"
    if not catalog.is_file() or catalog.is_symlink():
        raise ValueError("Extract the complete ZIP, including its outer marketplace folder.")
    target = _plain_file(root, ".mcp.json")
    original = target.read_bytes()
    config = json.loads(original)
    if (not isinstance(config, dict) or set(config) != {"mcpServers"}
            or not isinstance(config["mcpServers"], dict) or set(config["mcpServers"]) != {"between-the-lines"}):
        raise ValueError("Setup will not overwrite an unrelated MCP configuration.")
    server = config["mcpServers"]["between-the-lines"]
    if not isinstance(server, dict):
        raise ValueError("The bundled MCP launch configuration is invalid.")
    command = server.get("command", "")
    previous = Path(command)
    known_absolute = (previous.is_absolute() and previous.parts[-3:] == EXECUTABLE.parts
                      and server.get("cwd") == str(previous.parents[2]))
    known_fields = set(server) in ({"command", "args", "cwd"}, {"command", "args", "cwd", "env"})
    environment = server.get("env")
    if environment is not None and (not isinstance(environment, dict) or set(environment) != {"BETWEEN_THE_LINES_PLUGIN_DATA"}
                                    or not isinstance(environment["BETWEEN_THE_LINES_PLUGIN_DATA"], str)
                                    or not Path(environment["BETWEEN_THE_LINES_PLUGIN_DATA"]).is_absolute()):
        raise ValueError("Setup will not overwrite customized MCP environment settings.")
    if (not known_fields or server.get("args") != ["--mcp"]
            or not (command == PLACEHOLDER and server["cwd"] == "${PLUGIN_ROOT}" or known_absolute)):
        raise ValueError("Setup will not overwrite a customized MCP launch command.")
    # Codex deliberately filters environment inheritance for MCP children. Give
    # the server and hook wrapper the same explicit storage location instead.
    data_directory = default_data_dir().resolve()
    prepared = {"mcpServers": {"between-the-lines": {"command": str(executable), "args": ["--mcp"], "cwd": str(root),
                "env": {"BETWEEN_THE_LINES_PLUGIN_DATA": str(data_directory)}}}}
    if config != prepared:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".mcp-install-", suffix=".tmp", dir=root, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write((json.dumps(prepared, indent=2) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            if target.read_bytes() != original:
                raise ValueError("The launch configuration changed during setup; retry after that edit finishes.")
            os.replace(temporary, target)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
    return {"prepared": True, "pluginRoot": str(root), "marketplaceRoot": str(root.parent),
            "nextCommand": ["codex", "plugin", "marketplace", "add", str(root.parent)]}
