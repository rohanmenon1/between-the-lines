import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import type { ExtensionContext } from "vscode";
import type { RuntimeState } from "../runtime";
import { AUTOMATIC_PROVIDER_APPROVAL } from "../providers";

// Runtime uses VS Code's UI adapter. Its process and download boundaries are tested separately.
const moduleLoader = require("node:module") as { _load: (name: string, ...args: unknown[]) => unknown };
const originalLoad = moduleLoader._load;
let preparations = 0;
const configured: Record<string, unknown> = {};
class Emitter<T> {
  private listeners = new Set<(value: T) => void>();
  event = (listener: (value: T) => void) => { this.listeners.add(listener); return { dispose: () => this.listeners.delete(listener) }; };
  fire(value: T): void { for (const listener of this.listeners) listener(value); }
  dispose(): void { this.listeners.clear(); }
}
moduleLoader._load = function(name, ...args) {
  if (name === "vscode") return {
    EventEmitter: Emitter, ProgressLocation: { Notification: 15 },
    workspace: { getConfiguration: () => ({ get: (_key: string, fallback: number) => fallback, inspect: (key: string) => ({ globalValue: configured[key] }) }) },
    window: { withProgress: async (_options: unknown, callback: Function) => {
      preparations++;
      return callback({ report() {} }, { onCancellationRequested: (cancel: () => void) => { cancel(); return { dispose() {} }; } });
    } },
  };
  return originalLoad.call(this, name, ...args);
};
const { RuntimeManager, resolveWorkerExecutable, validAnalysisResult } = require("../runtime") as typeof import("../runtime");
moduleLoader._load = originalLoad;

async function contextFixture(binary: boolean, cached: boolean) {
  const directory = await mkdtemp(join(tmpdir(), "btl-runtime-"));
  const body = Buffer.from("local-model-fixture");
  const manifest = {
    name: "test untuned baseline", filename: "model.gguf", url: "https://example.com/model.gguf",
    sha256: createHash("sha256").update(body).digest("hex"), sizeBytes: body.length,
    revision: "a".repeat(40), tuned: false,
  };
  await mkdir(join(directory, "config"), { recursive: true });
  await writeFile(join(directory, "config", "model.json"), JSON.stringify(manifest));
  const executable = resolveWorkerExecutable(directory);
  if (binary) {
    await mkdir(join(directory, "worker", "bin", "btl-worker"), { recursive: true });
    await writeFile(executable, "test binary placeholder");
  }
  const storage = join(directory, "storage");
  const state = new Map<string, unknown>();
  if (cached) {
    await mkdir(join(storage, "models"), { recursive: true });
    await writeFile(join(storage, "models", "model.gguf"), body);
  }
  const context = { extensionPath: directory, globalStorageUri: { fsPath: storage },
    globalState: { get: (key: string) => state.get(key), update: async (key: string, value: unknown) => { state.set(key, value); } },
    secrets: { get: async () => "test-secret-must-not-leak" } } as unknown as ExtensionContext;
  return { context, directory, executable, state };
}

test("an existing verified model becomes ready without download or inference", async () => {
  const f = await contextFixture(true, true);
  const manager = new RuntimeManager(f.context);
  const events: RuntimeState[] = [];
  manager.onDidChangeState(state => events.push(state));
  const before = preparations;
  try {
    await manager.ensureReady();
    assert.equal(manager.getState().ready, true);
    assert.equal(manager.canAnnotateAutomatically(), true);
    assert.match(manager.getState().label, /baseline/);
    assert.equal(preparations, before);
    assert.equal(await manager.getStatus(), undefined);
    await manager.unload();
    assert.equal(manager.getState().ready, true);
    assert.match(events.at(-1)!.label, /unloaded/);
  } finally { manager.dispose(); await rm(f.directory, { recursive: true, force: true }); }
});

test("missing packaged worker fails before downloading a model", async () => {
  const f = await contextFixture(false, false);
  const manager = new RuntimeManager(f.context);
  const before = preparations;
  try {
    await assert.rejects(manager.ensureReady(), /bundled worker is missing/);
    assert.equal(preparations, before);
    assert.equal(manager.getState().ready, false);
    assert.match(manager.getState().error!, /bundled worker/);
  } finally { manager.dispose(); await rm(f.directory, { recursive: true, force: true }); }
});

test("cancelled preparation leaves state unready and makes no model request", async () => {
  const f = await contextFixture(true, false);
  const manager = new RuntimeManager(f.context);
  try {
    await assert.rejects(manager.ensureReady(), /cancelled/);
    assert.equal(manager.getState().ready, false);
    assert.equal(await readFile(f.executable, "utf8"), "test binary placeholder");
  } finally { manager.dispose(); await rm(f.directory, { recursive: true, force: true }); }
});

test("analysis schema rejects wrong sources, invalid ranges, excessive suggestions and nonfinite metrics", () => {
  const params = { uri: "file:///a.py", language: "python" as const, source: "def f(): pass\n" };
  const suggestion = {
    id: "id", symbolId: "f", symbolName: "f", codeHash: "a".repeat(64),
    range: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } },
    oldText: "", newText: "# Example\n", commentText: "# Example", explanation: "Task intent", evidence: ["Current declaration"],
  };
  const result = { uri: params.uri, sourceHash: createHash("sha256").update(params.source).digest("hex"),
    suggestions: [suggestion], skipped: [], metrics: { inputTokens: 1, outputTokens: 1, modelCalls: 1, elapsedMs: 2.5 } };
  assert.equal(validAnalysisResult(result, params), true);
  assert.equal(validAnalysisResult({ ...result, sourceHash: "b".repeat(64) }, params), false);
  assert.equal(validAnalysisResult({ ...result, suggestions: Array(4).fill(suggestion) }, params), false);
  assert.equal(validAnalysisResult({ ...result, suggestions: [{ ...suggestion, range: { start: { line: -1, character: 0 }, end: { line: 0, character: 0 } } }] }, params), false);
  assert.equal(validAnalysisResult({ ...result, metrics: { ...result.metrics, elapsedMs: Infinity } }, params), false);
  assert.equal(validAnalysisResult({ ...result, suggestions: [{ ...suggestion, evidence: [123] }] }, params), false);
});

function clearProviderSettings(): void {
  for (const key of Object.keys(configured)) delete configured[key];
}

test("custom configuration becomes ready without preparing a GGUF and does not expose its key", async () => {
  configured.provider = "custom";
  configured.endpoint = "https://provider.example/v1/chat/completions";
  configured.model = "chosen-model";
  const f = await contextFixture(true, false);
  await rm(join(f.directory, "config", "model.json"));
  const manager = new RuntimeManager(f.context);
  const before = preparations;
  try {
    await manager.ensureReady();
    assert.equal(manager.getState().ready, true);
    assert.equal(manager.getState().provider, "custom");
    assert.match(manager.getState().label, /connection checked on analysis/);
    assert.equal(manager.getState().modelPath, undefined);
    assert.equal(preparations, before);
    assert.ok(!JSON.stringify(manager.getState()).includes("test-secret"));
  } finally { manager.dispose(); clearProviderSettings(); await rm(f.directory, { recursive: true, force: true }); }
});

test("an invalid custom destination fails without falling back to local setup", async () => {
  configured.provider = "custom";
  configured.endpoint = "http://remote.example/v1/chat/completions";
  configured.model = "chosen-model";
  const f = await contextFixture(true, false);
  const manager = new RuntimeManager(f.context);
  const before = preparations;
  try {
    await assert.rejects(manager.ensureReady(), /HTTPS/);
    assert.equal(manager.getState().ready, false);
    assert.equal(preparations, before);
  } finally { manager.dispose(); clearProviderSettings(); await rm(f.directory, { recursive: true, force: true }); }
});

test("a legacy automatic-use setting is blocked before starting a custom worker", async () => {
  configured.provider = "custom";
  configured.endpoint = "https://provider.example/v1/chat/completions";
  configured.model = "chosen-model";
  configured.automaticCustomProvider = true;
  const f = await contextFixture(true, false);
  const manager = new RuntimeManager(f.context);
  try {
    await manager.ensureReady();
    assert.equal(manager.getState().ready, true);
    assert.equal(manager.canAnnotateAutomatically(), false);
    await assert.rejects(manager.annotateObserved({
      uri: "file:///sample.py", language: "python", source: "def f(): pass\n", previousSource: "def f(): return 1\n",
    }), /approve automatic provider use/);
    assert.equal(await manager.getStatus(), undefined);
  } finally { manager.dispose(); clearProviderSettings(); await rm(f.directory, { recursive: true, force: true }); }
});

test("saved-change analysis needs current destination consent while explicit manual analysis remains available", async () => {
  const endpoint = "https://provider.example/v1/chat/completions";
  configured.provider = "custom";
  configured.endpoint = endpoint;
  configured.model = "chosen-model";
  configured.automaticCustomProvider = true;
  const f = await contextFixture(true, false);
  f.state.set(AUTOMATIC_PROVIDER_APPROVAL, { endpoint, model: configured.model });
  const manager = new RuntimeManager(f.context);
  const params = { uri: "file:///sample.py", language: "python" as const, source: "def f(): pass\n", force: false };
  let starts = 0;
  // Replace only process launch; the runtime still validates every returned result.
  (manager as unknown as { startWorker: () => Promise<unknown> }).startWorker = async () => {
    starts++;
    return { request: async () => ({ uri: params.uri, sourceHash: createHash("sha256").update(params.source).digest("hex"),
      suggestions: [], skipped: [], metrics: { inputTokens: 0, outputTokens: 0, modelCalls: 0, elapsedMs: 0 } }) };
  };
  try {
    await manager.ensureReady();
    assert.equal(manager.canAnnotateAutomatically(), true);
    configured.endpoint = "https://other.example/v1/chat/completions";
    await assert.rejects(manager.analyze(params), /approve automatic use/);
    assert.equal(starts, 0, "A settings change must block even before runtime refresh finishes");
    await manager.refreshProvider();
    await assert.rejects(manager.analyze(params), /approve automatic use/);
    assert.equal(starts, 0, "The new endpoint must not inherit old saved-change consent");
    await manager.analyze({ ...params, force: true });
    assert.equal(starts, 1, "The explicitly invoked manual action remains available");
  } finally { manager.dispose(); clearProviderSettings(); await rm(f.directory, { recursive: true, force: true }); }
});

test("switching back to local selects only its verified cached model", async () => {
  configured.provider = "custom";
  configured.endpoint = "https://provider.example/v1/chat/completions";
  configured.model = "chosen-model";
  const f = await contextFixture(true, true);
  const manager = new RuntimeManager(f.context);
  try {
    await manager.ensureReady();
    assert.equal(manager.getState().provider, "custom");
    configured.provider = "local";
    await manager.refreshProvider();
    await manager.ensureReady();
    assert.equal(manager.getState().provider, "local");
    assert.match(manager.getState().label, /baseline/);
    assert.equal(manager.getState().modelPath, join(f.context.globalStorageUri.fsPath, "models", "model.gguf"));
  } finally { manager.dispose(); clearProviderSettings(); await rm(f.directory, { recursive: true, force: true }); }
});
