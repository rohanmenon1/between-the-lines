"""Check the actual Windows VSIX and hook-to-reader boundary without an IDE or model."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
HOOK_CHECK = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {spawn, spawnSync} = require('node:child_process');
const [extension, temporary, executable] = process.argv.slice(1);
const {mergeHookConfiguration} = require(path.join(extension, 'dist/hookConfig.js'));
const {readObservedEditsDetailed} = require(path.join(extension, 'dist/automation.js'));
(async () => {
  const checked = [];
  for (const bom of [false, true]) {
    const workspace = path.join(temporary, bom ? "BOM workspace café ' & literal" : "plain workspace café ' & literal");
    const inbox = path.join(workspace, 'inbox');
    await fs.mkdir(workspace, {recursive:true});
    const file = path.join(workspace, 'rank.py');
    const before = 'def rank(items):\n    return items\n';
    const after = 'def rank(items):\n    return sorted(items)\n';
    const prefix = bom ? '\uFEFF' : '';
    await fs.writeFile(file, prefix + before, 'utf8');
    const prompt = 'Sort café names without changing the caller\'s list.';
    const base = {cwd:workspace, session_id:'package-session', turn_id:'package-turn'};
    const config = mergeHookConfiguration({}, 'codex', executable, inbox, workspace);
    const hookTimings = [];
    async function hook(name, extra={}) {
      const handler = config.hooks[name][0].hooks[0];
      const command = handler.commandWindows;
      const timeoutMs = handler.timeout * 1000;
      assert.ok(Number.isFinite(timeoutMs) && timeoutMs > 0);
      assert.ok(command.startsWith('"' + executable + '" '), 'Call the packaged executable directly');
      assert.doesNotMatch(command, /powershell|pwsh|EncodedCommand/i, 'No PowerShell launcher');
      const started = performance.now();
      const result = await new Promise((resolve, reject) => {
        // Match Codex's Windows command runner: COMSPEC /C with a quoted raw
        // command line. No PowerShell process or encoded script is involved.
        const child = spawn(process.env.ComSpec || process.env.COMSPEC || 'cmd.exe', ['/C', '"' + command + '"'], {
          windowsHide:true, windowsVerbatimArguments:true,
        });
        let stdout = '', stderr = '', timedOut = false;
        child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
        child.stdout.on('data', value => {stdout = (stdout + value).slice(-65536);});
        child.stderr.on('data', value => {stderr = (stderr + value).slice(-65536);});
        const timer = setTimeout(() => {
          timedOut = true;
          // This PID is the native command child just created by this verifier.
          // Killing its tree also closes a worker that is waiting for stdin.
          if (child.pid && child.exitCode === null && child.signalCode === null) {
            spawnSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {windowsHide:true, timeout:10000});
          }
        }, timeoutMs);
        child.on('error', error => {clearTimeout(timer); reject(error);});
        child.on('close', status => {clearTimeout(timer); resolve({status, stdout, stderr, timedOut});});
        child.stdin.on('error', () => {});
        child.stdin.end(JSON.stringify({...base, hook_event_name:name, ...extra}), 'utf8');
      });
      assert.equal(result.timedOut, false, `${name} timed out; stderr: ${result.stderr}`);
      assert.equal(result.status, 0, `${name} failed (${result.status}); stderr: ${result.stderr}`);
      assert.equal(result.stderr, '', `${name} reported capture failure: ${result.stderr}`);
      assert.equal(result.stdout.trim(), '', 'Fresh history should not inject extra context');
      const elapsedMs = Math.round(performance.now() - started);
      assert.ok(elapsedMs <= timeoutMs, `${name} exceeded the configured hook timeout (${elapsedMs}ms)`);
      hookTimings.push({event:name, elapsedMs, timeoutMs});
    }
    await hook('UserPromptSubmit', {prompt});
    assert.equal((await readObservedEditsDetailed(inbox, [workspace])).edits.length, 0);
    const tool = {tool_use_id:'package-tool', tool_name:'Write', tool_input:{file_path:file, content:after}};
    await hook('PreToolUse', tool);
    await fs.writeFile(file, prefix + after, 'utf8');
    await hook('PostToolUse', tool);
    assert.equal((await readObservedEditsDetailed(inbox, [workspace])).edits.length, 0, 'Wait until Stop');
    await hook('Stop');
    const result = await readObservedEditsDetailed(inbox, [workspace]);
    assert.equal(result.edits.length, 1, JSON.stringify(result.skipped));
    const edit = result.edits[0];
    assert.equal(edit.prompt, prompt);
    assert.equal(edit.sessionId, base.session_id);
    assert.equal(edit.turnId, base.turn_id);
    assert.equal(edit.files[0].beforeSource, before);
    assert.equal(edit.files[0].afterSource, after);
    assert.equal(await fs.readFile(file, 'utf8'), prefix + after, 'Capture and reading must not edit source');
    assert.equal((await readObservedEditsDetailed(inbox, [workspace], new Set([edit.id]))).edits.length, 0);
    await fs.appendFile(file, '# later edit\n');
    assert.equal((await readObservedEditsDetailed(inbox, [workspace])).edits.length, 0, 'Reject stale disk');
    checked.push({encoding:bom ? 'UTF-8 BOM' : 'UTF-8', verifiedEdits:1,
      launcher:'cmd-native-executable', hookTimings,
      identityAndPrompt:true, waitsForStop:true, sourceUnchanged:true, processedIdSkipped:true, staleDiskRejected:true});
  }
  process.stdout.write(JSON.stringify(checked));
})().catch(error => {process.stderr.write(String(error.stack || error)); process.exitCode=1;});
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run_owned(command, label, timeout, request=None):
    """Keep subprocess diagnostics and stop only our own process tree on timeout."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    timed_out = False
    try:
        stdout, stderr = process.communicate(request, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "nt" and process.poll() is None:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        elif process.poll() is None:
            process.kill()
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired as error:
            raise ValueError(f"{label} timed out and its pipes did not close after stopping its process tree.") from error
    if timed_out or process.returncode:
        detail = stderr.decode("utf-8", errors="replace")[-8000:] or stdout.decode("utf-8", errors="replace")[-2000:]
        status = "timed out" if timed_out else f"exited with status {process.returncode}"
        raise ValueError(f"{label} {status}.\n{detail}")
    return stdout


def verify(vsix, node, root=ROOT):
    vsix, root = Path(vsix).resolve(strict=True), Path(root).resolve(strict=True)
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    temporary = Path(tempfile.mkdtemp(prefix="between-the-lines package ")).resolve()
    try:
        with zipfile.ZipFile(vsix) as archive:
            if archive.testzip() is not None or sum(item.file_size for item in archive.infolist()) > 512 * 1024 * 1024:
                raise ValueError("The VSIX is corrupt or exceeds the verification size budget.")
            names = archive.namelist()
            if len(set(names)) != len(names):
                raise ValueError("The VSIX contains duplicate paths.")
            for item in archive.infolist():
                member = PurePosixPath(item.filename)
                if (member.is_absolute() or ".." in member.parts or "\\" in item.filename or ":" in item.filename
                        or (item.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError("The VSIX contains an unsafe archive path or symbolic link.")
            installed = json.loads(archive.read("extension/package.json"))
            if installed != package:
                raise ValueError("The packaged extension manifest differs from current source.")
            info = json.loads(archive.read("extension/worker/bin/build-info.json"))
            sources = {path.relative_to(root).as_posix(): digest(path.read_bytes())
                       for path in sorted((root / "worker/btl_agent").glob("*.py"))}
            if info.get("target") != "win32-x64" or info.get("sources") != sources:
                raise ValueError("The packaged worker does not match current source and Windows x64 target.")
            javascript = [name for name in names if name.startswith("extension/dist/") and name.endswith(".js")]
            if not javascript or any(archive.read(name) != (root / name.removeprefix("extension/")).read_bytes() for name in javascript):
                raise ValueError("The packaged JavaScript differs from the compiled source.")
            license_root = "extension/worker/bin/btl-worker/third-party-licenses/"
            for dependency in info["dependencies"]:
                if any(license_root + name not in names for name in dependency["licenses"]):
                    raise ValueError("The VSIX omits a required dependency notice.")
            archive.extractall(temporary)
        extension = temporary / "extension"
        executable = extension / "worker/bin/btl-worker/btl-worker.exe"
        request = b'{"id":"status","method":"status"}\n{"id":"shutdown","method":"shutdown"}\n'
        stdout = run_owned([str(executable)], "Frozen worker status/shutdown", 30, request)
        messages = [json.loads(line) for line in stdout.splitlines()]
        if (len(messages) != 2 or messages[0].get("id") != "status"
                or messages[0].get("result", {}).get("protocolVersion") != 1
                or messages[0]["result"].get("modelLoaded") is not False
                or messages[1] != {"id": "shutdown", "result": {"stopped": True}}):
            raise ValueError("The packaged worker status/shutdown protocol failed.")
        stdout = run_owned([str(node), "-e", HOOK_CHECK, str(extension), str(temporary), str(executable)],
                           "Packaged Windows hooks and event reader", 150)
        hooks = json.loads(stdout)
    except Exception:
        # Keep failed evidence and any locked files. Cleanup must not hide the
        # original error or replace useful stderr with a Windows sharing error.
        print(f"Failed verification files preserved at: {temporary}", file=sys.stderr)
        raise
    else:
        try:
            shutil.rmtree(temporary)
        except OSError as error:
            print(f"Checks passed; temporary files retained at {temporary}: {error}", file=sys.stderr)
    return {"version": package["version"], "file": vsix.name, "bytes": vsix.stat().st_size,
            "sha256": digest(vsix.read_bytes()), "target": info["target"],
            "workerSourceModulesVerified": len(sources), "compiledJavascriptFilesVerified": len(javascript),
            "workerStatusAndShutdown": True, "hookProducerAndReader": hooks,
            "modelCalls": 0, "desktopInteraction": False,
            "limits": ["Controlled hook events; no live Codex session or VS Code editor was started.",
                       "No model inference or semantic comment-quality measurement."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vsix", type=Path)
    parser.add_argument("--node", default=shutil.which("node"), help="Node.js executable used to run packaged JavaScript.")
    parser.add_argument("--output", type=Path, help="New JSON report path; an existing report is never replaced.")
    arguments = parser.parse_args()
    if not arguments.node:
        parser.error("Pass --node with a Node.js executable.")
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    vsix = arguments.vsix or ROOT / "artifacts" / f"between-the-lines-{package['version']}-win32-x64.vsix"
    try:
        report = verify(vsix, arguments.node)
        rendered = json.dumps(report, indent=2) + "\n"
        if arguments.output:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            with arguments.output.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(2, f"Package verification stopped: {error}\n")
    print(rendered, end="")


if __name__ == "__main__":
    main()
