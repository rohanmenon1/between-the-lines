"""Isolated, opt-in live Codex integration fixture. Never changes user config."""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "artifacts" / "real-codex-fixture"
EVIDENCE = REPO / "artifacts" / "real-codex-evidence"
INBOX = Path(os.environ.get("BETWEEN_THE_LINES_TEST_INBOX", str(FIXTURE / ".between-the-lines-inbox")))
WORKER = Path(os.environ.get("BETWEEN_THE_LINES_TEST_WORKER", str(REPO / "worker" / "bin" / "btl-worker" / "btl-worker.exe")))

FIRST_PROMPT = """This is an isolated integration test; edit only rank.py.
rank must preserve the input order of ties.
count_positive must ignore zero and negative numbers.
Implement rank as a descending stable sort of dictionaries by their score field.
Implement count_positive as the number of values greater than zero.
Use exactly two apply_patch calls: update rank first, then count_positive.
Do not write files with shell commands. Do not edit hook settings or the inbox.
After both patches, stop and give one short sentence describing the changes.
The current file is exactly the following; no file-read command is needed:
```python
def rank(items):
    return items


def count_positive(values):
    return len(values)
```
"""
SECOND_PROMPT = """This is a new-session retrieval test. Do not modify files.
List the original user requirements supplied by between-the-lines hooks,
including their requirement IDs. If none were supplied, answer NONE.
Do not read the inbox database, hook files, or other test artifacts.
"""


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def setup():
    if FIXTURE.exists():
        raise RuntimeError("Fixture already exists; preserve existing evidence.")
    FIXTURE.mkdir(parents=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (FIXTURE / ".codex").mkdir()
    (FIXTURE / "rank.py").write_text(
        "def rank(items):\n    return items\n\n\ndef count_positive(values):\n    return len(values)\n",
        encoding="utf-8", newline="")
    write_json(FIXTURE / "hook-runtime.json", {"executable": str(WORKER)})
    generator = """
const fs = require('node:fs');
const {mergeHookConfiguration} = require('./dist/hookConfig');
const [worker,inbox,workspace,runtime,destination] = process.argv.slice(1);
const config = mergeHookConfiguration({},'codex',worker,inbox,workspace,runtime);
fs.writeFileSync(destination, JSON.stringify(config,null,2));
"""
    subprocess.run(["node", "-e", generator, str(WORKER), str(INBOX), str(FIXTURE),
                    str(FIXTURE / "hook-runtime.json"), str(FIXTURE / ".codex" / "hooks.json")],
                   cwd=REPO, check=True)
    # A separate audit handler records only this disposable fixture's hook input.
    # The production handler is unchanged and invokes the packaged executable.
    hook_path = FIXTURE / ".codex" / "hooks.json"
    config = json.loads(hook_path.read_text(encoding="utf-8"))
    for event, groups in config["hooks"].items():
        destination = str(EVIDENCE).replace("'", "''")
        command = ("[System.IO.File]::WriteAllText((Join-Path '" + destination + "' ('" + event
                   + "-' + [Guid]::NewGuid().ToString() + '.json')), [Console]::In.ReadToEnd(),"
                   + " [System.Text.UTF8Encoding]::new($false))")
        import base64
        encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
        groups.append({"hooks": [{"type": "command", "command": "true",
                                   "commandWindows": "powershell.exe -NoProfile -NonInteractive -EncodedCommand " + encoded,
                                   "timeout": 5}]})
    write_json(hook_path, config)
    (EVIDENCE / "first-prompt.txt").write_text(FIRST_PROMPT, encoding="utf-8")
    (EVIDENCE / "second-prompt.txt").write_text(SECOND_PROMPT, encoding="utf-8")
    print(FIXTURE)


def inspect():
    notifications = [json.loads(path.read_text(encoding="utf-8")) for path in INBOX.glob("*.json")]
    edits = [item for item in notifications if item.get("kind") == "edit" and item.get("workspaceRoot") == str(FIXTURE)]
    audits = []
    for path in EVIDENCE.glob("*.json"):
        try:
            event = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(event, dict) or not event.get("hook_event_name"):
                continue
            audits.append({"event": event.get("hook_event_name"), "sessionId": event.get("session_id"),
                           "turnId": event.get("turn_id"), "tool": event.get("tool_name"),
                           "toolUseId": event.get("tool_use_id"), "path": str(path)})
        except (ValueError, OSError):
            pass
    report = {"fixture": str(FIXTURE), "worker": str(WORKER),
              "audits": audits, "notificationKinds": [item.get("kind") for item in notifications],
              "editCount": len(edits)}
    if (INBOX / "history.sqlite3").exists():
        with sqlite3.connect(INBOX / "history.sqlite3") as connection:
            report["ledgerCounts"] = {table: connection.execute("SELECT count(*) FROM " + table).fetchone()[0]
                                     for table in ("prompts", "tools", "verified_edits", "finished_turns", "requirements")}
    if edits:
        event = max(edits, key=lambda item: item["timestamp"])
        report["edit"] = {key: event[key] for key in ("id", "eventId", "sessionId", "turnId", "workspaceRoot", "timestamp")}
        report["edit"]["files"] = [{"path": item["path"], "beforeHash": item["beforeHash"], "afterHash": item["afterHash"],
                                      "beforeSource": item["beforeSource"], "afterSource": item["afterSource"]}
                                     for item in event["files"]]
        original = next(json.loads(path.read_text(encoding="utf-8-sig")) for path in EVIDENCE.glob("UserPromptSubmit-*.json")
                        if json.loads(path.read_text(encoding="utf-8-sig")).get("turn_id") == event["turnId"])
        report["promptPreserved"] = event["prompt"] == original["prompt"]
        report["matchesProvidedPrompt"] = event["prompt"].replace("\r\n", "\n") == FIRST_PROMPT
        sys.path.insert(0, str(REPO / "worker"))
        from btl_agent.parsing import changed_declarations, discover
        report["changedDeclarations"] = [declaration.name for item in event["files"]
                                         for declaration in changed_declarations(discover(item["afterSource"], "python"),
                                                                                 discover(item["beforeSource"], "python"))]
    observation = EVIDENCE / "editor-observation.json"
    if observation.exists():
        report["editor"] = json.loads(observation.read_text(encoding="utf-8"))
    second = EVIDENCE / "second-final.txt"
    if second.exists() and observation.exists():
        answer = second.read_text(encoding="utf-8")
        records = report["editor"]["records"]
        report["reinjectionVerified"] = all(record["quote"] in answer and record["id"] in answer for record in records)
        report["freshSessionAnswer"] = answer
        fresh = next(json.loads(path.read_text(encoding="utf-8-sig")) for path in EVIDENCE.glob("UserPromptSubmit-*.json")
                     if json.loads(path.read_text(encoding="utf-8-sig")).get("prompt", "").replace("\r\n", "\n") == SECOND_PROMPT)
        report["freshSessionDistinct"] = fresh["session_id"] != event["sessionId"]
        report["freshSessionToolCalls"] = sum(1 for audit in audits if audit["sessionId"] == fresh["session_id"]
                                              and audit["event"] == "PreToolUse")
    write_json(REPO / "artifacts" / "real-codex-result.json", report)
    print(json.dumps(report, indent=2))


def repoint():
    """Connect the existing fixture to an isolated, installed VS Code profile."""
    if not WORKER.is_file() or not INBOX.is_absolute():
        raise RuntimeError("The test worker and inbox must be verified absolute paths.")
    write_json(FIXTURE / "hook-runtime.json", {"executable": str(WORKER)})
    generator = """
const fs = require('node:fs');
const {mergeHookConfiguration} = require('./dist/hookConfig');
const [worker,inbox,workspace,runtime,destination] = process.argv.slice(1);
const existing = JSON.parse(fs.readFileSync(destination,'utf8'));
const config = mergeHookConfiguration(existing,'codex',worker,inbox,workspace,runtime);
fs.writeFileSync(destination, JSON.stringify(config,null,2));
"""
    subprocess.run(["node", "-e", generator, str(WORKER), str(INBOX), str(FIXTURE),
                    str(FIXTURE / "hook-runtime.json"), str(FIXTURE / ".codex/hooks.json")], cwd=REPO, check=True)
    print(json.dumps({"worker": str(WORKER), "inbox": str(INBOX)}))


def check_editor():
    """Observe installed extension output without writing comments or history."""
    import ast
    import hashlib

    request = {"id": "query", "method": "knownRequirements", "params": {
        "inbox": str(INBOX), "workspaceRoot": str(FIXTURE), "paths": [str(FIXTURE / "rank.py")]}}
    requests = json.dumps(request) + "\n" + json.dumps({"id": "stop", "method": "shutdown"}) + "\n"
    completed = subprocess.run([str(WORKER)], input=requests, text=True, encoding="utf-8",
                               capture_output=True, timeout=30, check=True)
    response = json.loads(completed.stdout.splitlines()[0])
    if "error" in response:
        raise RuntimeError(response["error"])
    records = response["result"]["records"]
    if {record["symbolName"] for record in records} != {"rank", "count_positive"}:
        raise RuntimeError("Both actual editor-written requirements have not been committed.")
    edits = [json.loads(path.read_text(encoding="utf-8")) for path in INBOX.glob("*.json")]
    event = max((item for item in edits if item.get("kind") == "edit" and item.get("workspaceRoot") == str(FIXTURE)),
                key=lambda item: item["timestamp"])
    observed = event["files"][0]["afterSource"]
    annotated = (FIXTURE / "rank.py").read_text(encoding="utf-8")
    if ast.dump(ast.parse(observed)) != ast.dump(ast.parse(annotated)):
        raise RuntimeError("The editor changed executable code while adding annotations.")
    for record in records:
        if record["commentText"] not in annotated or record["promptEventId"] != event["eventId"]:
            raise RuntimeError("An editor annotation is missing or has different provenance.")
        if hashlib.sha256(event["prompt"].encode("utf-8")).hexdigest() != record["promptHash"]:
            raise RuntimeError("The persisted prompt hash differs from the actual hook prompt.")
    observation = {"automaticallyAnnotatedSymbols": [record["symbolName"] for record in records],
                   "codeAstPreserved": True, "records": records, "annotatedSource": annotated}
    write_json(EVIDENCE / "editor-observation.json", observation)
    print(json.dumps({"symbols": observation["automaticallyAnnotatedSymbols"], "codeAstPreserved": True}))


def run(stage):
    """Launch the installed CLI's native executable with exact argv quoting."""
    cli = REPO / "artifacts/codex-test-tool/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
    trust = 'projects={' + json.dumps(str(FIXTURE)) + '={trust_level="trusted"}}'
    command = [str(cli), "--no-daemon", "exec", "--ignore-user-config", "--ephemeral",
               "--skip-git-repo-check", "--dangerously-bypass-hook-trust",
               "--disable", "apps", "-c", 'model_reasoning_effort="low"', "-c", trust,
               "-c", 'default_permissions=":workspace"', "-c", 'windows.sandbox="elevated"',
               "-C", str(FIXTURE), "--json", "-o", str(EVIDENCE / (stage + "-final.txt")), "-"]
    write_json(EVIDENCE / (stage + "-argv.json"), command)
    environment = os.environ.copy()
    environment.update(PYTHONUTF8="1", TEMP=str(REPO / "artifacts/release-temp"), TMP=str(REPO / "artifacts/release-temp"))
    with (EVIDENCE / (stage + "-cli.log")).open("w", encoding="utf-8") as output:
        result = subprocess.run(command, input=FIRST_PROMPT if stage == "first" else SECOND_PROMPT,
                                text=True, encoding="utf-8", stdout=output, stderr=subprocess.STDOUT,
                                env=environment, cwd=REPO, timeout=240)
    print("Codex exit status:", result.returncode)
    sys.exit(result.returncode)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["setup", "inspect", "repoint", "check-editor", "run-first", "run-second"])
    args = parser.parse_args()
    if args.stage.startswith("run-"):
        run(args.stage.removeprefix("run-"))
    else:
        {"setup": setup, "inspect": inspect, "repoint": repoint, "check-editor": check_editor}[args.stage]()
