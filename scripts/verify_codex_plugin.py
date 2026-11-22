"""Check the shipped plugin process; optionally exercise an isolated live Codex turn."""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BEFORE = 'def rank(items):\n    return items\n'
AFTER = 'def rank(items):\n    return sorted(items, key=lambda item: item["score"], reverse=True)\n'
PROMPT = ("Please fix the leaderboard output. Higher scores must come first, and tied scores must keep arrival order "
          "so the display stays stable between refreshes. Leave the caller's list unchanged. Names such as caf\u00e9 and \U0001f7e2 stay intact.")


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _extract(bundle, directory):
    directory = Path(directory).absolute()
    if directory.exists():
        raise ValueError("Choose a new verification directory; previous evidence is never overwritten.")
    directory.mkdir(parents=True)
    with zipfile.ZipFile(bundle) as archive:
        for name in archive.namelist():
            parts = Path(name).parts
            if Path(name).is_absolute() or ".." in parts:
                raise ValueError("Bundle contains an escaping path.")
            if name.endswith("/") or not (name.startswith("codex-plugin/") or name in {".agents/plugins/marketplace.json", "Install.cmd"}):
                continue
            destination = directory / name
            destination.resolve().relative_to(directory.resolve())
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(archive.read(name))
    return directory, directory / "codex-plugin"


def _prepare_bundle(directory, plugin, environment):
    # Calling the real installer in preparation-only mode exercises its packaged
    # path handling without registering a marketplace or changing user settings.
    completed = subprocess.run(["cmd.exe", "/d", "/c", "Install.cmd", "--prepare-only"], cwd=directory, env=environment,
                               capture_output=True, text=True, encoding="utf-8", timeout=30)
    (directory / "installer.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (directory / "installer.stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise ValueError("The packaged installer failed before any model inference.")
    server = json.loads((plugin / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["between-the-lines"]
    expected = plugin / "runtime/between-the-lines-plugin/between-the-lines-plugin.exe"
    if server != {"command": str(expected), "args": ["--mcp"], "cwd": str(plugin),
                  "env": {"BETWEEN_THE_LINES_PLUGIN_DATA": environment["BETWEEN_THE_LINES_PLUGIN_DATA"]}}:
        raise ValueError("The installer did not resolve its own bundled executable.")


def _environment(directory, plugin):
    environment = os.environ.copy()
    environment.update(PLUGIN_ROOT=str(plugin), BETWEEN_THE_LINES_PLUGIN_DATA=str(directory / "plugin-data"), PYTHONUTF8="1")
    return environment


def _hook(plugin, environment, name, event):
    hooks = json.loads((plugin / "hooks/hooks.json").read_text(encoding="utf-8"))
    command = hooks["hooks"][name][0]["hooks"][0]["commandWindows"]
    prefix = "powershell.exe -NoProfile -NonInteractive -EncodedCommand "
    if not command.startswith(prefix):
        raise ValueError("Expected the reviewed encoded Windows hook launcher.")
    completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", command[len(prefix):]],
                               input=json.dumps({**event, "hook_event_name": name}, ensure_ascii=False), text=True, encoding="utf-8",
                               capture_output=True, env=environment, timeout=20)
    if completed.returncode != 0 or completed.stderr.strip():
        raise ValueError("The packaged hook wrapper failed: " + completed.stderr[:300])
    return json.loads(completed.stdout or "{}")


class Client:
    """Read one bounded JSON response per request and always close our process."""

    def __init__(self, executable, environment, log):
        self.log = Path(log).open("wb")
        self.process = subprocess.Popen([str(executable), "--mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.log, env=environment,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.responses = queue.Queue()
        self.sequence = 0
        threading.Thread(target=self._read, daemon=True).start()
        self.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                    "clientInfo": {"name": "between-the-lines-native-test", "version": "1"}})

    def _read(self):
        try:
            while line := self.process.stdout.readline(2 * 1024 * 1024 + 1):
                if len(line) > 2 * 1024 * 1024:
                    raise ValueError("Native MCP response exceeds its test limit.")
                self.responses.put(json.loads(line))
        except Exception as error:
            self.responses.put(error)

    def request(self, method, params):
        self.sequence += 1
        value = {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params}
        self.process.stdin.write((json.dumps(value) + "\n").encode("utf-8"))
        self.process.stdin.flush()
        result = self.responses.get(timeout=20)
        if isinstance(result, Exception):
            raise result
        if result.get("id") != self.sequence or "error" in result:
            raise ValueError("Native MCP request failed or response identity differed.")
        return result["result"]

    def tool(self, name, **arguments):
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise ValueError(result["content"][0]["text"])
        return result["structuredContent"]

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=10)
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
            self.log.close()


def verify_native(bundle, directory):
    directory, plugin = _extract(bundle, directory)
    environment = _environment(directory, plugin)
    _prepare_bundle(directory, plugin, environment)
    path = directory / "leaderboard.py"
    path.write_text(BEFORE, encoding="utf-8", newline="")
    event = {"cwd": str(directory), "session_id": "native-session", "turn_id": "native-turn", "model": "fixture-model"}
    conflicting_parent = {**environment, "BETWEEN_THE_LINES_PLUGIN_DATA": str(directory / "wrong-parent-history")}
    _hook(plugin, conflicting_parent, "UserPromptSubmit", {**event, "prompt": PROMPT})
    assert not (directory / "wrong-parent-history").exists()
    tool = {**event, "tool_name": "Write", "tool_use_id": "native-write", "tool_input": {"file_path": str(path), "content": AFTER}}
    _hook(plugin, environment, "PreToolUse", tool)
    path.write_text(AFTER, encoding="utf-8", newline="")
    _hook(plugin, environment, "PostToolUse", tool)
    continuation = _hook(plugin, environment, "Stop", {**event, "stop_hook_active": False})
    assert continuation.get("decision") == "block"
    assert _hook(plugin, environment, "Stop", {**event, "stop_hook_active": True}) == {}
    _hook(plugin, environment, "UserPromptSubmit", {**event, "turn_id": "continuation-turn", "prompt": continuation["reason"]})
    executable = plugin / "runtime/between-the-lines-plugin/between-the-lines-plugin.exe"
    client = Client(executable, environment, directory / "mcp.stderr.log")
    try:
        jobs = client.tool("pending_annotations", workspace=str(directory))["jobs"]
        assert len(jobs) == 1
        job_id = jobs[0]["id"]
        context = client.tool("get_annotation_context", workspace=str(directory), jobId=job_id)
        assert context["intent"]["prompt"] == PROMPT
        preview = client.tool("preview_annotation", workspace=str(directory), jobId=job_id,
                              comment="Preserve arrival order for tied scores so refreshed leaderboards remain stable.",
                              passageId=context["evidenceChoices"][0]["id"], reason="Document the task-specific stable-order constraint.")
        assert path.read_text() == AFTER
        applied = client.tool("apply_annotation", workspace=str(directory), jobId=job_id, previewId=preview["previewId"],
                              supportReason="sorted is stable and reverse ranks higher scores first without mutating the input list.")
        annotated = path.read_text(encoding="utf-8")
        assert ast.dump(ast.parse(annotated)) == ast.dump(ast.parse(AFTER))
        assert applied["record"]["prompt"] == PROMPT and applied["record"]["generationRoute"] == "host_codex"
        assert applied["record"]["commentText"] != "# " + PROMPT
        records = client.tool("context_history", workspace=str(directory), paths=[str(path)])["records"]
        assert len(records) == 1
        retired = client.request("tools/call", {"name": "retire_context", "arguments": {"workspace": str(directory), "requirementId": records[0]["id"]}})
        assert retired["isError"], "The model cannot retire original intent without captured user authorization."
        client.tool("undo_annotation", workspace=str(directory), jobId=job_id)
        assert path.read_text() == AFTER
        assert not client.tool("pending_annotations", workspace=str(directory))["jobs"]
    finally:
        client.close()
    fresh = _hook(plugin, environment, "UserPromptSubmit", {**event, "session_id": "fresh-session", "turn_id": "fresh-turn", "prompt": "Inspect the original requirements; do not edit source."})
    supplied = fresh["hookSpecificOutput"]["additionalContext"]
    entries = [json.loads(line) for line in supplied.splitlines() if line.startswith("{")]
    assert any(entry["originalUserText"] == PROMPT and entry["requirementId"] == records[0]["id"] for entry in entries)
    assert applied["record"]["commentText"] not in supplied
    with sqlite3.connect(directory / "plugin-data/intents/history.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM prompts").fetchone()[0] == 2
    report = {"status": "passed", "kind": "controlled native fixture; no model inference", "bundleSha256": hashlib.sha256(Path(bundle).read_bytes()).hexdigest(),
              "checks": ["Packaged installer resolves its own runtime without global settings", "Hooks and MCP use one explicit history directory despite conflicting parent environment", "Packaged PowerShell hooks forward exact UTF-8 events", "One bounded continuation", "Continuation is not original user intent", "Frozen MCP startup and tools", "Source-bound preview", "Checked apply and AST preservation", "Original evidence separate from prose", "Retirement authorization", "Checked undo without replay", "Fresh-session original-context reinjection"],
              "record": applied["record"]}
    _write(directory / "report.json", report)
    return directory / "report.json"


def _verify_client_plugin(executable, config_arguments, catalog_path, environment, directory):
    """Test-only APIs: check discovery and actual transport before inference."""
    log = (directory / "client-preflight.stderr.log").open("wb")
    process = subprocess.Popen([str(executable), "--no-daemon", "--disable", "apps", *config_arguments,
                                "app-server", "--listen", "stdio://"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=log, env=environment)
    messages = queue.Queue()

    def read():
        try:
            for line in process.stdout:
                messages.put(json.loads(line))
        except Exception as error:
            messages.put(error)
        messages.put(EOFError("Native client exited before metadata verification."))

    threading.Thread(target=read, daemon=True).start()

    def request(identifier, method, params):
        process.stdin.write((json.dumps({"id": identifier, "method": method, "params": params}) + "\n").encode())
        process.stdin.flush()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            response = messages.get(timeout=max(0.01, deadline - time.monotonic()))
            if isinstance(response, Exception):
                raise response
            if response.get("id") == identifier:
                if "error" in response:
                    raise ValueError("Native plugin metadata failed; no inference was started.")
                return response["result"]
        raise TimeoutError("Native plugin metadata timed out; no inference was started.")

    try:
        request(1, "initialize", {"clientInfo": {"name": "between-the-lines-verification", "version": "0.1.0"},
                                   "capabilities": {"experimentalApi": True}})
        process.stdin.write(b'{"method":"initialized","params":{}}\n')
        process.stdin.flush()
        detail = request(2, "plugin/read", {"marketplacePath": str(catalog_path), "pluginName": "between-the-lines"})["plugin"]
        _write(directory / "client-plugin-preflight.json", detail)
        if (not detail["summary"].get("installed") or not detail["summary"].get("enabled")
                or len(detail.get("hooks", [])) != 4 or "between-the-lines" not in detail.get("mcpServers", [])
                or not detail.get("skills")):
            raise ValueError("Codex did not discover the installed skill, four hooks and local MCP server; no inference was started.")
        thread = request(3, "thread/start", {"cwd": str(directory), "ephemeral": True})["thread"]
        deadline = time.monotonic() + 25
        identifier = 4
        while True:
            status = request(identifier, "mcpServerStatus/list", {"threadId": thread["id"], "detail": "toolsAndAuthOnly", "limit": 100})
            server = next((item for item in status["data"] if item["name"] == "between-the-lines"), None)
            # Save only our own transport metadata, never other installed tools.
            _write(directory / "client-transport-preflight.json", server)
            if server and server.get("runtimeStatus") == "connected":
                expected = {"pending_annotations", "get_annotation_context", "preview_annotation", "apply_annotation",
                            "skip_annotation", "undo_annotation", "context_history", "retire_context"}
                if set(server.get("tools", {})) != expected:
                    raise ValueError("Native plugin transport exposes an unexpected tool set; no inference was started.")
                break
            if not server or server.get("runtimeStatus") not in {"starting", "notStarted"} or time.monotonic() >= deadline:
                error = server.get("toolsError") if server else "server missing"
                raise ValueError("Native plugin transport failed: " + str(error) + "; no inference was started.")
            time.sleep(0.5)
            identifier += 1
        return {"installed": True, "enabled": True, "hooks": 4, "mcpServer": "between-the-lines", "transport": "connected", "tools": 8}
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


def verify_live(bundle, directory, codex_executable):
    """This opt-in mode consumes normal Codex usage, never a separate model API."""
    directory, plugin = _extract(bundle, directory)
    environment = _environment(directory, plugin)
    # Native hooks set their own installed root. A fixture must not manufacture
    # that value for the host and accidentally hide a broken launch contract.
    environment.pop("PLUGIN_ROOT", None)
    environment.pop("CLAUDE_PLUGIN_ROOT", None)
    (directory / "rank.py").write_text(BEFORE, encoding="utf-8", newline="")
    catalog_path = directory / ".agents/plugins/marketplace.json"
    catalog = json.loads(catalog_path.read_text())
    # Test installation is deliberately separate from the public bundle's
    # marketplace and never changes the user's globally enabled plugin choices.
    catalog["name"] = "between-the-lines-verification"
    _write(catalog_path, catalog)
    installed = Path.home() / ".codex/plugins/cache/between-the-lines-verification/between-the-lines/local"
    source_files = [path for path in plugin.rglob("*") if path.is_file()]
    if {path.relative_to(plugin).as_posix() for path in source_files} != {path.relative_to(installed).as_posix() for path in installed.rglob("*") if path.is_file()}:
        raise ValueError("The verification cache must contain exactly the packaged files; no inference was started.")
    for source in source_files:
        destination = installed / source.relative_to(plugin)
        if not destination.is_file() or hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(destination.read_bytes()).digest():
            raise ValueError("Install the exact bundle in the isolated verification cache before --live; no inference was started.")
    _prepare_bundle(directory, plugin, environment)
    # This is our explicitly isolated verification cache, whose pristine file
    # hashes were just checked. Desktop installation copies this prepared file.
    (installed / ".mcp.json").write_bytes((plugin / ".mcp.json").read_bytes())
    plugin_key = "between-the-lines@" + catalog["name"]
    trust = "projects={" + json.dumps(str(directory)) + '={trust_level="trusted"}}'
    # Whole TOML tables keep quoted plugin IDs intact. Dotted -c key parsing
    # treats the quote characters literally in some supported CLI releases.
    marketplaces = "marketplaces={" + json.dumps(catalog["name"]) + '={source_type="local",source=' + json.dumps(str(directory)) + "}}"
    plugins = "plugins={" + json.dumps(plugin_key) + "={enabled=true}}"
    preflight = subprocess.run([str(codex_executable), "--no-daemon", "-C", str(directory), "-c", trust,
                                "-c", marketplaces, "-c", plugins, "plugin", "marketplace", "list", "--json"],
                               capture_output=True, text=True, encoding="utf-8", env=environment, timeout=30, check=True)
    sources = json.loads(preflight.stdout)
    if not any(item.get("name") == catalog["name"] and Path(item["root"]) == directory for item in sources["marketplaces"]):
        raise ValueError("The native CLI did not discover this isolated marketplace; no inference was started.")
    _write(directory / "marketplace-preflight.json", sources)
    client_preflight = _verify_client_plugin(codex_executable, ["-c", trust, "-c", marketplaces, "-c", plugins],
                                              catalog_path, environment, directory)
    prompt = (PROMPT + " Edit rank.py using apply_patch. Let between-the-lines handle annotations through its own tools; "
              "do not add comments with your implementation patch. Read rank.py, applicable AGENTS.md instructions, "
              "and the between-the-lines SKILL.md when its workflow requires it. "
              "Do not inspect test logs, catalogs or internal state. Finish normally after the code change.")
    command = [str(codex_executable), "--no-daemon", "--disable", "apps", "exec", "--ignore-user-config", "--ephemeral",
               "--skip-git-repo-check", "--approve-for-me", "--dangerously-bypass-hook-trust",
               "-c", trust, "-c", marketplaces, "-c", plugins,
               "-c", 'model_reasoning_effort="low"', "-c", "mcp_optional_startup_grace_ms=0", "-C", str(directory), "--json",
               "-o", str(directory / "final.txt"), "-"]
    # The one-invocation trust exemption applies only to our reviewed fixture's
    # own packaged hooks. Normal installations use /hooks; no user config is changed.
    _write(directory / "argv.json", command)
    with (directory / "codex.jsonl").open("w", encoding="utf-8") as stream:
        completed = subprocess.run(command, input=prompt, encoding="utf-8", text=True, env=environment,
                                   cwd=directory, stdout=stream, stderr=subprocess.STDOUT, timeout=240)
    database = directory / "plugin-data/plugin.sqlite3"
    jobs = []
    if database.is_file():
        with sqlite3.connect(database) as db:
            jobs = [json.loads(row[0]) for row in db.execute("SELECT record_json FROM jobs")]
    applied = [job for job in jobs if job["state"] == "applied"]
    passed = completed.returncode == 0 and len(applied) == 1
    report = {"status": "passed" if passed else "failed", "kind": "actual Codex host-model integration",
              "exitCode": completed.returncode, "bundleSha256": hashlib.sha256(Path(bundle).read_bytes()).hexdigest(),
              "clientPreflight": client_preflight,
              "jobs": [{key: job.get(key) for key in ("id", "state", "modelAtCapture", "record")} for job in jobs],
              "source": (directory / "rank.py").read_text(encoding="utf-8"),
              "note": "Synthetic plumbing test, not a semantic-quality benchmark. No Qwen or separate model endpoint is used."}
    if applied:
        job = applied[0]
        report["originalPromptPreserved"] = job["prompt"] == prompt
        report["codeAstPreservedByAnnotation"] = ast.dump(ast.parse(job["afterSource"])) == ast.dump(ast.parse(report["source"]))
        report["modelComment"] = job["record"]["commentText"]
        report["status"] = "passed" if passed and report["originalPromptPreserved"] and report["codeAstPreservedByAnnotation"] else "failed"
    _write(directory / "report.json", report)
    return directory / "report.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "artifacts/between-the-lines-codex-0.1.0-win32-x64.zip")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Run one isolated coding turn using normal Codex usage.")
    parser.add_argument("--codex-executable", type=Path)
    args = parser.parse_args()
    if args.live and args.codex_executable is None:
        parser.error("--live requires the native Codex executable path.")
    result = verify_live(args.bundle, args.output, args.codex_executable) if args.live else verify_native(args.bundle, args.output)
    print(result)
    return 0 if json.loads(result.read_text())["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
