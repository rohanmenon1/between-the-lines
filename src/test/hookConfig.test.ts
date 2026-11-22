import { strict as assert } from "node:assert";
import { test } from "node:test";
import { connectionStatus, mergeHookConfiguration, powershellQuote, shellQuote, windowsArgument } from "../hookConfig";

test("shell quoting keeps apostrophes and shell metacharacters literal", () => {
  assert.equal(powershellQuote("a'b$()"), "'a''b$()'");
  assert.equal(shellQuote("a'b$()"), "'a'\"'\"'b$()'");
});

test("native Windows argument quoting preserves empty strings, quotes and trailing slashes", () => {
  assert.equal(windowsArgument(""), '""');
  assert.equal(windowsArgument('a"b'), '"a\\"b"');
  assert.equal(windowsArgument("C:\\space dir\\"), '"C:\\space dir\\\\"');
  assert.equal(windowsArgument('one\\"two'), '"one\\\\\\"two"');
  assert.equal(windowsArgument("caf\u00e9 e\u0301 $() 'folder'"), '"caf\u00e9 e\u0301 $() \'folder\'"');
});

test("Codex merge preserves foreign handlers and repeated setup is idempotent", () => {
  const existing = { hooks: { UserPromptSubmit: [{ hooks: [{ type: "command", command: "other" }] }], Stop: [{ hooks: [] }] } };
  const once = mergeHookConfiguration(existing, "codex", "C:\\Program Files\\worker.exe", "C:\\Users\\a'b\\inbox");
  assert.deepEqual(mergeHookConfiguration(once, "codex", "C:\\Program Files\\worker.exe", "C:\\Users\\a'b\\inbox"), once);
  assert.deepEqual((once.hooks as any).Stop[0], existing.hooks.Stop[0]);
  for (const name of ["UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]) {
    assert.ok((once.hooks as any)[name].some((group: any) => group.hooks.some((item: any) => item.command.includes(`'--event' '${name}'`))));
  }
  const handler = (once.hooks as any).UserPromptSubmit[1].hooks[0];
  assert.equal(handler.commandWindows, ["C:\\Program Files\\worker.exe", "--capture-hook", "--source", "codex", "--inbox", "C:\\Users\\a'b\\inbox", "--event", "UserPromptSubmit"].map(windowsArgument).join(" "));
});

test("Windows hooks name the versioned worker directly for review and reconnect", () => {
  const config = mergeHookConfiguration({}, "codex", "C:\\extension-0.3.0\\worker.exe", "C:\\storage\\inbox", "C:\\project");
  const handler = (config.hooks as any).PreToolUse[0].hooks[0];
  assert.equal(handler.commandWindows, '"C:\\extension-0.3.0\\worker.exe" "--capture-hook" "--source" "codex" "--inbox" "C:\\storage\\inbox" "--event" "PreToolUse" "--workspace" "C:\\project"');
  assert.doesNotMatch(handler.commandWindows, /powershell|EncodedCommand|hook-runtime|Get-Content/i);
  assert.equal(handler.timeout, 10);
});

test("Windows hook commands quote paths with Unicode, spaces and shell metacharacters", () => {
  const inbox = "C:\\caf\u00e9\\e\u0301 a'b $() & ^ folder\\inbox";
  const config = mergeHookConfiguration({}, "codex", "C:\\tool dir\\worker.exe", inbox);
  const handler = (config.hooks as any).PreToolUse[0].hooks[0];
  assert.equal(handler.commandWindows, ["C:\\tool dir\\worker.exe", "--capture-hook", "--source", "codex", "--inbox", inbox, "--event", "PreToolUse"].map(windowsArgument).join(" "));
});

test("Windows beta rejects paths that cmd could expand or split", () => {
  for (const unsafe of ["percent%PATH%", "delayed!name!", 'double"quote', "line\nbreak", "carriage\rreturn", "nul\u0000", "control\u001f", "delete\u007f"]) {
    const value = `C:\\${unsafe}\\path`;
    const message = /Windows hook paths cannot contain/;
    assert.throws(() => mergeHookConfiguration({}, "codex", value, "C:\\inbox", "C:\\project"), message);
    assert.throws(() => mergeHookConfiguration({}, "codex", "C:\\worker.exe", value, "C:\\project"), message);
    assert.throws(() => mergeHookConfiguration({}, "codex", "C:\\worker.exe", "C:\\inbox", value), message);
  }
});

test("reconnect replaces old owned hooks and preserves other Windows handlers", () => {
  const foreign = { type: "command", commandWindows: '"C:\\other.exe" "--event" "Stop"' };
  const old = { type: "command", command: "worker --capture-hook --inbox old", commandWindows: "legacy wrapper" };
  const config = mergeHookConfiguration({ hooks: { Stop: [{ hooks: [foreign, old] }] } }, "codex", "C:\\new\\worker.exe", "C:\\inbox");
  assert.deepEqual((config.hooks as any).Stop[0].hooks, [foreign]);
  assert.equal((config.hooks as any).Stop.length, 2);
  assert.match((config.hooks as any).Stop[1].hooks[0].commandWindows, /^"C:\\new\\worker\.exe" /);
});

test("connection metadata requires explicit reconnect after extension updates", () => {
  assert.equal(connectionStatus(undefined, "0.3.0"), undefined);
  assert.equal(connectionStatus({ source: "codex" }, "0.3.0"), "Reconnect Codex after extension update.");
  assert.equal(connectionStatus({ source: "codex", extensionVersion: "0.2.0" }, "0.3.0"), "Reconnect Codex after extension update.");
  assert.equal(connectionStatus({ source: "codex", extensionVersion: "0.3.0" }, "0.3.0"), "Codex hooks configured · approve them in Codex /hooks.");
  assert.equal(connectionStatus({ source: "copilot" }, "0.3.0"), "Reconnect Copilot after extension update.");
});

test("Copilot schema and malformed existing configuration", () => {
  const config = mergeHookConfiguration({}, "copilot", "worker", "inbox");
  assert.equal(config.version, 1);
  assert.equal((config.hooks as any).userPromptSubmitted[0].powershell, "& 'worker' '--capture-hook' '--source' 'copilot' '--inbox' 'inbox' '--event' 'UserPromptSubmit'");
  assert.equal((config.hooks as any).userPromptSubmitted[0].timeoutSec, 10);
  assert.throws(() => mergeHookConfiguration({ hooks: [] }, "codex", "worker", "inbox"));
  assert.throws(() => mergeHookConfiguration({ version: 7 }, "copilot", "worker", "inbox"));
});
