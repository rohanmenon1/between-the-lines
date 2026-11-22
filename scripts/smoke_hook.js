// Exercise the actual Windows hook command and frozen capture entry point.
const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const { mkdirSync, mkdtempSync, readdirSync, readFileSync } = require("node:fs");
const { join } = require("node:path");
const { mergeHookConfiguration } = require("../dist/hookConfig");

const root = join(__dirname, "..");
mkdirSync(join(root, "artifacts"), { recursive: true });
const inbox = mkdtempSync(join(root, "artifacts", "hook-smoke-"));
const executable = join(root, "worker", "bin", "btl-worker", "btl-worker.exe");
const config = mergeHookConfiguration({}, "codex", executable, inbox);
const command = config.hooks.UserPromptSubmit[0].hooks[0].commandWindows;
const prompt = "Preserve café names and don't change behavior.";
const started = Date.now();
assert.ok(command.startsWith('"' + executable + '" '));
assert.doesNotMatch(command, /powershell|pwsh|EncodedCommand/i);
const result = spawnSync(process.env.ComSpec || process.env.COMSPEC || "cmd.exe", ["/C", '"' + command + '"'], {
  input: JSON.stringify({ prompt, cwd: root, session_id: "smoke-session" }),
  encoding: "utf8", timeout: 10000, windowsHide: true, windowsVerbatimArguments: true,
});
assert.equal(result.status, 0, result.error?.message || result.stderr);
assert.equal(result.stdout, "", "Capture must not inject text into the coding tool");
assert.equal(result.stderr, "", "Hook capture failed");
const names = readdirSync(inbox).filter(name => name.endsWith(".json"));
assert.equal(names.length, 1);
const event = JSON.parse(readFileSync(join(inbox, names[0]), "utf8"));
assert.equal(event.prompt, prompt);
assert.equal(event.source, "codex");
assert.equal(event.sessionId, "smoke-session");
console.log(`Frozen Windows hook passed (${Date.now() - started} ms).`);
