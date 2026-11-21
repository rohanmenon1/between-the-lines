import * as vscode from "vscode";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { strict as assert } from "node:assert";
import { copyFile, link, mkdir, mkdtemp, readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";
import { sourceHash } from "../controller";
import { RuntimeManager, resolveWorkerExecutable } from "../runtime";
import { WorkerClient } from "../workerClient";
import { Suggestions } from "../suggestions";
import { AUTOMATIC_PROVIDER_APPROVAL, validateEndpoint } from "../providers";
import { AnalysisResult, AutomaticAnalysisResult, Metrics, RequirementRecord, Suggestion } from "../types";

interface AutomationApi { inbox: string; scanObservedEdits(): Promise<void>; getAutomaticMetrics(): Metrics | undefined; testGlobalState?: vscode.Memento }
const RANK_PROMPT = "Keep equal-score items in their original order when ranking.";
const RANK_COMMENT = "Use stable score ordering so ties keep their input positions.";
const UNIQUE_COMMENT = "Keep the first occurrence of each identifier while removing duplicates.";

interface TestReport {
  status: "running" | "passed" | "failed";
  tests: string[];
  metrics?: Metrics;
  modelStatus?: unknown;
  localGeneration?: { prompt: string; commentText: string; evidence: RequirementRecord["evidence"]; generation: RequirementRecord["generation"] };
  error?: string;
  elapsedMs?: number;
}

/** Run with VS Code's --extensionTestsPath, using an isolated user-data directory. */
export async function run(): Promise<void> {
  const started = Date.now();
  const resultPath = requiredEnvironment("BTL_TEST_RESULT");
  const extensionPath = requiredEnvironment("BTL_TEST_EXTENSION_PATH");
  const modelPath = requiredEnvironment("BTL_TEST_MODEL");
  const temporary = await mkdtemp(join(tmpdir(), "btl-integration-"));
  const report: TestReport = { status: "running", tests: [] };
  const resources: vscode.Disposable[] = [];
  let runtime: RuntimeManager | undefined;
  try {
    const manifest = JSON.parse(await readFile(join(extensionPath, "package.json"), "utf8")) as { publisher: string; name: string };
    const extension = vscode.extensions.getExtension(`${manifest.publisher}.${manifest.name}`);
    assert.ok(extension, "VS Code did not discover the development extension");
    const automation = await extension.activate() as AutomationApi;
    assert.ok(automation && typeof automation.inbox === "string" && typeof automation.scanObservedEdits === "function" && typeof automation.getAutomaticMetrics === "function",
      "The extension must expose its bounded integration-test entry point");
    assert.ok(automation.testGlobalState, "Fixture consent is available only in an isolated extension test host");
    const commands = await vscode.commands.getCommands(true);
    for (const name of ["between-the-lines.configureProvider", "between-the-lines.setup", "between-the-lines.analyzeFile", "between-the-lines.setIntent", "between-the-lines.selectCapturedIntent", "between-the-lines.connectCodex", "between-the-lines.connectCopilot", "between-the-lines.unloadModel", "between-the-lines.toggleAutomatic", "between-the-lines.scanObservedEdits", "between-the-lines.reviewRequirements"]) {
      assert.ok(commands.includes(name), `${name} was not registered`);
    }
    await vscode.commands.executeCommand("between-the-lines.unloadModel");
    await vscode.commands.executeCommand("between-the-lines.sidebar.focus");
    report.tests.push("Extension activation, commands, and sidebar");

    const automaticFixture = await startModelFixture();
    const automaticSettings = vscode.workspace.getConfiguration("between-the-lines");
    try {
      await automation.testGlobalState.update(AUTOMATIC_PROVIDER_APPROVAL, undefined);
      await automaticSettings.update("endpoint", automaticFixture.endpoint, vscode.ConfigurationTarget.Global);
      await automaticSettings.update("model", "editor-fixture", vscode.ConfigurationTarget.Global);
      await automaticSettings.update("automaticCustomProvider", false, vscode.ConfigurationTarget.Global);
      await automaticSettings.update("provider", "custom", vscode.ConfigurationTarget.Global);
      await vscode.commands.executeCommand("between-the-lines.setup");
      await automaticEditorChecks(automation, extensionPath, report, automaticFixture.tasks);
      await cancellationEditorCheck(automation, extensionPath, automaticFixture, report);
      await providerFailureEditorCheck(automation, extensionPath, automaticFixture, report);
      automaticFixture.assertHealthy();
      assert.ok(automaticFixture.tasks.includes("comment_synthesis"));
      assert.ok(automaticFixture.tasks.includes("comment_verification"));
      report.tests.push("Automatic comments use generation and verification with captured prompt evidence");
    } finally {
      await vscode.commands.executeCommand("between-the-lines.unloadModel");
      await automaticSettings.update("provider", undefined, vscode.ConfigurationTarget.Global);
      await automaticSettings.update("endpoint", undefined, vscode.ConfigurationTarget.Global);
      await automaticSettings.update("model", undefined, vscode.ConfigurationTarget.Global);
      await automaticSettings.update("automaticCustomProvider", undefined, vscode.ConfigurationTarget.Global);
      await automation.testGlobalState.update(AUTOMATIC_PROVIDER_APPROVAL, undefined);
      await automaticFixture.close();
    }

    await realLocalEditorCheck(automation, extensionPath, modelPath, report);

    const file = join(temporary, "comments.py");
    const source = "def unique_ids(ids):\n    return list(dict.fromkeys(ids))\n";
    await writeFile(file, source, "utf8");
    const document = await vscode.workspace.openTextDocument(vscode.Uri.file(file));
    const context = fakeContext(extensionPath, join(temporary, "storage"));
    const suggestions = new Suggestions(context, "btl-integration-preview");
    resources.push(suggestions, vscode.workspace.registerTextDocumentContentProvider("btl-integration-preview", suggestions));
    const uri = document.uri.toString();
    const proposal = insertion("insert-comment", "# Preserve the first occurrence of each ID.\n");
    suggestions.replace({ uri, version: document.version, source }, analysis(uri, source, proposal));

    await suggestions.preview(uri, proposal.id);
    const key = `${sourceHash(source)}-${proposal.id}`;
    const originalPreview = vscode.Uri.from({ scheme: "btl-integration-preview", path: `/original/${encodeURIComponent(document.fileName)}`, query: key });
    const suggestedPreview = vscode.Uri.from({ scheme: "btl-integration-preview", path: `/suggested/${encodeURIComponent(document.fileName)}`, query: key });
    assert.equal((await vscode.workspace.openTextDocument(originalPreview)).getText(), source);
    assert.equal((await vscode.workspace.openTextDocument(suggestedPreview)).getText(), proposal.newText + source);
    assert.equal(document.getText(), source, "Preview modified the real source");
    report.tests.push("Real diff preview preserves source");

    await suggestions.accept(uri, proposal.id);
    const accepted = proposal.newText + source;
    assert.equal(document.getText(), accepted);
    assert.equal(suggestions.all().length, 0, "Accept should discard proposals based on the old document version");
    assert.deepEqual(suggestions.owned(uri), [{ symbolId: proposal.symbolId, commentText: proposal.commentText, codeHash: proposal.codeHash }]);
    report.tests.push("Real TextEditor.edit acceptance and ownership tracking");

    const stale = insertion("stale-comment", "# This must never be inserted.\n");
    suggestions.replace({ uri, version: document.version, source: accepted }, analysis(uri, accepted, stale));
    const editor = await vscode.window.showTextDocument(document, { preview: false });
    assert.ok(await editor.edit(edit => edit.insert(document.positionAt(document.getText().length), "\n")));
    assert.ok(await editor.edit(edit => edit.replace(new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), accepted)));
    assert.equal(document.getText(), accepted, "The test must restore exactly the same text");
    await assert.rejects(suggestions.accept(uri, stale.id), /file changed after this suggestion/);
    assert.equal(document.getText(), accepted);
    report.tests.push("Stale version rejected even after text is restored");
    await document.save();

    runtime = new RuntimeManager(context);

    const quote = "unique_ids must keep the first occurrence of each identifier.";
    const customFixture = await startModelFixture();
    const settings = vscode.workspace.getConfiguration("between-the-lines");
    try {
      await settings.update("endpoint", customFixture.endpoint, vscode.ConfigurationTarget.Global);
      await settings.update("model", "editor-fixture", vscode.ConfigurationTarget.Global);
      await settings.update("provider", "custom", vscode.ConfigurationTarget.Global);
      await runtime.refreshProvider();
      await runtime.ensureReady();
      assert.equal(runtime.getState().provider, "custom");
      assert.equal(runtime.getState().modelPath, undefined);
      const customResult = await runtime.analyze({ uri, source, language: "python", intent: { source: "manual", prompt: quote }, contextFiles: [], force: true, maxSuggestions: 1 });
      assert.equal(customResult.suggestions.length, 1);
      assert.equal(customResult.suggestions[0].commentText, "# " + UNIQUE_COMMENT);
      assert.notEqual(customResult.suggestions[0].commentText, "# User context: " + quote);
      customFixture.assertHealthy();
      assert.deepEqual(customFixture.tasks, ["comment_synthesis"]);
      assert.equal((await runtime.getStatus())?.modelLoaded, false);
      assert.equal(await readFile(file, "utf8"), accepted);
      report.tests.push("Custom provider through frozen worker, without local weights or source writes");
    } finally {
      await settings.update("provider", undefined, vscode.ConfigurationTarget.Global);
      await settings.update("endpoint", undefined, vscode.ConfigurationTarget.Global);
      await settings.update("model", undefined, vscode.ConfigurationTarget.Global);
      await customFixture.close();
    }
    report.status = "passed";
  } catch (error) {
    report.status = "failed";
    report.error = error instanceof Error ? error.stack ?? error.message : String(error);
    throw error;
  } finally {
    runtime?.dispose();
    for (const resource of resources.reverse()) resource.dispose();
    report.elapsedMs = Date.now() - started;
    await mkdir(dirname(resultPath), { recursive: true });
    await writeFile(resultPath, JSON.stringify(report, null, 2), "utf8");
  }
}

/** One actual generation + verification pair through the installed model and editor. */
async function realLocalEditorCheck(api: AutomationApi, extensionPath: string, modelPath: string, report: TestReport): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.find(item => item.uri.scheme === "file");
  assert.ok(folder, "The local-model test requires an isolated local workspace");
  const root = folder.uri.fsPath;
  const executable = resolveWorkerExecutable(extensionPath);
  const file = join(root, `local-model-${randomUUID()}.py`);
  const before = "def rank(items):\n    return items\n";
  const after = 'def rank(items):\n    return sorted(items, key=lambda item: item["score"], reverse=True)\n';
  const prompt = "Please fix the leaderboard output in this file. Ranking should put higher scores first, but when scores tie the people should keep the order they arrived in. Our screen currently jumps between refreshes, so that part matters. Use a stable sort and leave the caller's list alone.";
  assert.ok(!/\brank\b/i.test(prompt), "The task must not name its declaration");
  const base = { session_id: "local-session-" + randomUUID(), turn_id: "local-turn-" + randomUUID(), cwd: root };
  const event = { ...base, tool_use_id: "local-tool-" + randomUUID(), tool_name: "Write",
    tool_input: { file_path: file, content: after } };
  const previousMetrics = api.getAutomaticMetrics();
  await writeFile(file, before, "utf8");
  await hook(executable, api.inbox, root, "UserPromptSubmit", { ...base, prompt });
  await hook(executable, api.inbox, root, "PreToolUse", event);
  await writeFile(file, after, "utf8");
  await hook(executable, api.inbox, root, "PostToolUse", event);
  await hook(executable, api.inbox, root, "Stop", base);
  await api.scanObservedEdits();
  assert.equal(await readFile(file, "utf8"), after, "A completed edit must wait while the model is unprepared");
  assert.deepEqual(api.getAutomaticMetrics(), previousMetrics, "Waiting for setup must not invoke the model or acknowledge the edit");
  report.tests.push("Completed edits wait for model setup without downloading or losing their context");

  const model = JSON.parse(await readFile(join(extensionPath, "config", "model.json"), "utf8")) as { filename: string };
  const cached = join(dirname(api.inbox), "models", model.filename);
  await mkdir(dirname(cached), { recursive: true });
  try { await link(modelPath, cached); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EXDEV") throw error;
    await copyFile(modelPath, cached);
  }
  await vscode.commands.executeCommand("between-the-lines.setup");
  await api.scanObservedEdits();
  const metrics = api.getAutomaticMetrics();
  assert.ok(metrics, "Automatic inference must publish measured totals");
  assert.equal(metrics.modelCalls, 2, "One local synthesis and one support check must actually run");
  assert.ok(metrics.inputTokens > 0 && metrics.outputTokens > 0);
  report.metrics = metrics;

  const history = new WorkerClient(executable);
  try {
    const result = await history.request<{ records: RequirementRecord[] }>("knownRequirements", {
      inbox: api.inbox, paths: [file], workspaceRoot: root,
    });
    assert.equal(result.records.length, 1, "Actual model output must produce one persisted comment, not merely abstain");
    const record = result.records[0];
    assert.equal(record.commentKind, "synthesized");
    assert.equal(record.prompt, prompt);
    assert.equal(record.sessionId, base.session_id);
    assert.equal(record.turnId, base.turn_id);
    assert.equal(record.generation?.provider, "local");
    assert.equal(record.generation?.model, model.filename);
    assert.deepEqual(record.evidence, [{ quote: prompt, start: 0, end: prompt.length, promptHash: sourceHash(prompt) }]);
    assert.equal(record.scopeStatus, "exact", "The declaration's parsed fingerprint must remain unchanged by its comment");
    assert.match(record.commentText, /^# [^\r\n]+$/);
    const prose = record.commentText.slice(2);
    assert.notEqual(prose.replace(/\W/g, "").toLowerCase(), prompt.replace(/\W/g, "").toLowerCase(), "The model must write an explanation rather than copy the prompt");
    assert.ok(!prose.startsWith("User context:"));
    assert.match(prose, /\b(?:tie|ties|equal|stable|original|input order)\b/i, "The explanation must retain the ordering constraint");
    const annotated = await readFile(file, "utf8");
    assert.equal(annotated, record.commentText + "\n" + after, "Only the generated comment may change the observed source");
    const document = await vscode.workspace.openTextDocument(vscode.Uri.file(file));
    assert.equal(document.getText(), annotated);
    assert.equal(document.isDirty, false);
    report.localGeneration = { prompt, commentText: record.commentText, evidence: record.evidence, generation: record.generation };
    report.modelStatus = record.generation;
    report.tests.push("Actual frozen local model synthesizes and verifies a saved explanation with original evidence and unchanged code");
  } finally {
    history.dispose();
    await vscode.commands.executeCommand("between-the-lines.unloadModel");
  }
}

/** Exercise real hook processes, disk changes, editor edits and durable history together. */
async function automaticEditorChecks(api: AutomationApi, extensionPath: string, report: TestReport, modelTasks: string[]): Promise<void> {
  const folder = vscode.workspace.workspaceFolders?.find(item => item.uri.scheme === "file");
  assert.ok(folder, "Automatic tests require an isolated local workspace folder");
  const root = folder.uri.fsPath;
  const executable = resolveWorkerExecutable(extensionPath);
  const settings = vscode.workspace.getConfiguration("between-the-lines", folder.uri);
  await settings.update("automaticAnnotations", true, vscode.ConfigurationTarget.Global);
  await settings.update("automatic", false, vscode.ConfigurationTarget.Global);
  const file = join(root, `observed-${randomUUID()}.py`);
  const before = "def rank(items):\n    return items\n";
  const after = "def rank(items):\n    return sorted(items, key=lambda item: item[1])\n";
  const quote = RANK_PROMPT;
  const session = "editor-session-" + randomUUID();
  const turn = "editor-turn-" + randomUUID();
  const tool = "editor-tool-" + randomUUID();
  const patch = `*** Begin Patch\n*** Update File: ${file}\n@@\n def rank(items):\n-    return items\n+    return sorted(items, key=lambda item: item[1])\n*** End Patch`;
  const base = { session_id: session, turn_id: turn, cwd: root };
  const edit = { ...base, tool_use_id: tool, tool_name: "apply_patch", tool_input: { command: patch } };
  await writeFile(file, before, "utf8");
  await hook(executable, api.inbox, root, "UserPromptSubmit", { ...base, prompt: quote });
  await hook(executable, api.inbox, root, "PreToolUse", edit);
  await writeFile(file, after, "utf8");
  await hook(executable, api.inbox, root, "PostToolUse", edit);
  await api.scanObservedEdits();
  assert.equal(await readFile(file, "utf8"), after, "PostToolUse must not insert while the agent turn remains open");
  await hook(executable, api.inbox, root, "Stop", base);
  await api.scanObservedEdits();
  assert.equal(await readFile(file, "utf8"), after, "A custom provider without automatic-use consent must leave source untouched");
  assert.equal(modelTasks.length, 0, "Old custom-provider setup must not authorize automatic prompt transmission");
  await settings.update("automaticCustomProvider", true, vscode.ConfigurationTarget.Global);
  await api.scanObservedEdits();
  assert.equal(await readFile(file, "utf8"), after, "A synced automatic-use boolean cannot grant destination consent");
  assert.equal(modelTasks.length, 0);
  assert.ok(api.testGlobalState);
  await api.testGlobalState.update(AUTOMATIC_PROVIDER_APPROVAL, {
    endpoint: validateEndpoint(settings.get<string>("endpoint", "")), model: settings.get<string>("model", ""),
  });
  const expected = "# " + RANK_COMMENT + "\n" + after;
  await until(async () => await readFile(file, "utf8") === expected, () => api.scanObservedEdits());
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(file));
  assert.equal(document.getText(), expected);
  assert.equal(document.isDirty, false, "Automatic annotation must finish saving its version-checked edit");
  report.tests.push("Custom automatic requests remain blocked until consent, then process the retained event");
  report.tests.push("Observed hook turn generates, applies and saves a comment with separate original evidence");

  const history = new WorkerClient(executable);
  try {
    const status = await history.request<{ modelLoaded: boolean }>("status");
    assert.equal(status.modelLoaded, false, "Provenance queries must not load a model");
    const known = await history.request<{ records: RequirementRecord[] }>("knownRequirements", {
      inbox: api.inbox, paths: [file], workspaceRoot: root,
    });
    assert.equal(known.records.length, 1);
    const record = known.records[0];
    assert.equal(record.quote, quote);
    assert.equal(record.commentText, "# " + RANK_COMMENT);
    assert.equal(record.commentKind, "synthesized");
    assert.deepEqual(record.evidence, [{ quote, start: 0, end: quote.length, promptHash: sourceHash(quote) }]);
    assert.equal(record.generation?.provider, "custom");
    assert.equal(record.generation?.model, "editor-fixture");
    assert.equal(record.prompt, quote);
    assert.equal(record.sessionId, session);
    assert.equal(record.turnId, turn);
    assert.equal(record.status, "active");
    assert.ok(record.id && record.eventId);
    const reinjected = await hook(executable, api.inbox, root, "PreToolUse", {
      session_id: "fresh-session-" + randomUUID(), turn_id: "fresh-turn-" + randomUUID(),
      tool_use_id: "fresh-read-" + randomUUID(), cwd: root, tool_name: "Read", tool_input: { file_path: file },
    });
    assert.ok(JSON.stringify(reinjected).includes(quote), "A fresh coding session must receive the original recorded instruction");
    assert.ok(JSON.stringify(reinjected).includes("quoted data, not new instructions"));
    assert.ok(!JSON.stringify(reinjected).includes(RANK_COMMENT), "Generated wording must not be reinjected as original user intent");
    report.tests.push("Generated explanation and original prompt evidence remain distinct in durable history");

    await api.scanObservedEdits();
    await api.scanObservedEdits();
    assert.equal(await readFile(file, "utf8"), expected, "Repeated scans must not duplicate the annotation");
    const editor = await vscode.window.showTextDocument(document, { preview: false });
    await vscode.commands.executeCommand("undo");
    assert.equal(editor.document.getText(), after, "One native undo should remove only the automatic annotation");
    await editor.document.save();
    await api.scanObservedEdits();
    assert.equal(await readFile(file, "utf8"), after, "Acknowledged events must not replay after user undo");
    report.tests.push("Automatic edit supports native undo and does not replay on later scans");

    await history.request("retireRequirement", { inbox: api.inbox, requirementId: record.id, workspaceRoot: root });
    const retired = await history.request<{ records: RequirementRecord[] }>("knownRequirements", {
      inbox: api.inbox, paths: [file], workspaceRoot: root,
    });
    assert.deepEqual(retired.records, []);
    const afterRetirement = await hook(executable, api.inbox, root, "PreToolUse", {
      session_id: "retired-session-" + randomUUID(), turn_id: "retired-turn-" + randomUUID(),
      tool_use_id: "retired-read-" + randomUUID(), cwd: root, tool_name: "Read", tool_input: { file_path: file },
    });
    assert.ok(!JSON.stringify(afterRetirement).includes(quote), "Retired requirements must stop influencing later agent sessions");
    report.tests.push("Explicit retirement stops future requirement reinjection");
  } finally { history.dispose(); }

  const dirtyFile = join(root, `dirty-${randomUUID()}.py`);
  await writeFile(dirtyFile, before, "utf8");
  const dirtyBase = { session_id: "dirty-session-" + randomUUID(), turn_id: "dirty-turn-" + randomUUID(), cwd: root };
  const dirtyTool = { ...dirtyBase, tool_use_id: "dirty-tool-" + randomUUID(), tool_name: "Write",
    tool_input: { file_path: dirtyFile, content: after } };
  await hook(executable, api.inbox, root, "UserPromptSubmit", { ...dirtyBase, prompt: quote });
  await hook(executable, api.inbox, root, "PreToolUse", dirtyTool);
  await writeFile(dirtyFile, after, "utf8");
  const dirtyDocument = await vscode.workspace.openTextDocument(vscode.Uri.file(dirtyFile));
  const dirtyEditor = await vscode.window.showTextDocument(dirtyDocument, { preview: false });
  const manual = "# unsaved human change\n";
  assert.ok(await dirtyEditor.edit(editBuilder => editBuilder.insert(new vscode.Position(0, 0), manual)));
  assert.equal(dirtyDocument.isDirty, true);
  await hook(executable, api.inbox, root, "PostToolUse", dirtyTool);
  await hook(executable, api.inbox, root, "Stop", dirtyBase);
  await api.scanObservedEdits();
  assert.equal(dirtyDocument.getText(), manual + after, "Automatic processing must not overwrite unsaved human changes");
  assert.equal(await readFile(dirtyFile, "utf8"), after, "A dirty editor must not trigger an automatic save");
  await vscode.window.showTextDocument(dirtyDocument, { preview: false });
  await vscode.commands.executeCommand("workbench.action.files.revert");
  await api.scanObservedEdits();
  assert.equal(await readFile(dirtyFile, "utf8"), after, "An event skipped for dirty source must not replay after reverting");
  report.tests.push("Dirty editor buffers are skipped without saving or replaying agent events");

  // Stage a real completed event until after a separate writer changes its file.
  // This avoids racing the extension's timer while testing its stale-disk guard.
  const staging = join(root, `.integration-inbox-${randomUUID()}`);
  const staleFile = join(root, `stale-${randomUUID()}.py`);
  const staleBase = { session_id: "stale-session-" + randomUUID(), turn_id: "stale-turn-" + randomUUID(), cwd: root };
  const staleTool = { ...staleBase, tool_use_id: "stale-tool-" + randomUUID(), tool_name: "Write",
    tool_input: { file_path: staleFile, content: after } };
  await writeFile(staleFile, before, "utf8");
  await hook(executable, staging, root, "UserPromptSubmit", { ...staleBase, prompt: quote });
  await hook(executable, staging, root, "PreToolUse", staleTool);
  await writeFile(staleFile, after, "utf8");
  await hook(executable, staging, root, "PostToolUse", staleTool);
  await hook(executable, staging, root, "Stop", staleBase);
  const completed: string[] = [];
  for (const name of await readdir(staging)) {
    if (name.endsWith(".json") && JSON.parse(await readFile(join(staging, name), "utf8")).kind === "edit") completed.push(name);
  }
  assert.equal(completed.length, 1, "The hook must emit one completed edit transaction");
  const staleSource = after + "# changed outside the observed transaction\n";
  await writeFile(staleFile, staleSource, "utf8");
  await copyFile(join(staging, completed[0]), join(api.inbox, completed[0]));
  await api.scanObservedEdits();
  assert.equal(await readFile(staleFile, "utf8"), staleSource, "A stale disk snapshot must never be annotated");
  await writeFile(staleFile, after, "utf8");
  await api.scanObservedEdits();
  assert.equal(await readFile(staleFile, "utf8"), after, "A rejected stale event must not replay when old contents return");
  report.tests.push("Stale disk events are rejected and stay acknowledged when the old source returns");

  await malformedEditorCheck(api, executable, root, report);
  await autoSaveEditorCheck(extensionPath, root, report);
}

async function malformedEditorCheck(api: AutomationApi, executable: string, root: string, report: TestReport): Promise<void> {
  const badFile = join(root, `malformed-${randomUUID()}.py`);
  const goodFile = join(root, `valid-${randomUUID()}.py`);
  const badBefore = "def broken(items):\n    return items\n";
  const badAfter = "def broken(items)\n    return sorted(items)\n";
  const goodBefore = "def rank(items):\n    return items\n";
  const goodAfter = "def rank(items):\n    return sorted(items, key=lambda item: item[1])\n";
  const quote = RANK_PROMPT;
  const base = { session_id: "mixed-session-" + randomUUID(), turn_id: "mixed-turn-" + randomUUID(), cwd: root };
  await writeFile(badFile, badBefore, "utf8");
  await writeFile(goodFile, goodBefore, "utf8");
  await hook(executable, api.inbox, root, "UserPromptSubmit", {
    ...base, prompt: "broken must preserve every item.\n" + quote,
  });
  for (const [path, content] of [[badFile, badAfter], [goodFile, goodAfter]]) {
    const event = { ...base, tool_use_id: "mixed-tool-" + randomUUID(), tool_name: "Write",
      tool_input: { file_path: path, content } };
    await hook(executable, api.inbox, root, "PreToolUse", event);
    await writeFile(path, content, "utf8");
    await hook(executable, api.inbox, root, "PostToolUse", event);
  }
  await hook(executable, api.inbox, root, "Stop", base);
  const expected = "# " + RANK_COMMENT + "\n" + goodAfter;
  await until(async () => await readFile(goodFile, "utf8") === expected, () => api.scanObservedEdits());
  assert.equal(await readFile(badFile, "utf8"), badAfter, "Invalid syntax must remain untouched");
  const history = new WorkerClient(executable);
  try {
    const known = await history.request<{ records: RequirementRecord[] }>("knownRequirements", {
      inbox: api.inbox, paths: [badFile, goodFile], workspaceRoot: root,
    });
    assert.equal(known.records.length, 1);
    const recordedPath = known.records[0].path;
    assert.ok(typeof recordedPath === "string");
    assert.equal(vscode.Uri.file(recordedPath).fsPath, vscode.Uri.file(goodFile).fsPath);
  } finally { history.dispose(); }
  await api.scanObservedEdits();
  await api.scanObservedEdits();
  assert.equal(await readFile(goodFile, "utf8"), expected);
  assert.equal(await readFile(badFile, "utf8"), badAfter);
  report.tests.push("Malformed source is skipped while later valid files in the same turn are annotated");
}

async function autoSaveEditorCheck(extensionPath: string, root: string, report: TestReport): Promise<void> {
  const file = join(root, `autosave-${randomUUID()}.py`);
  const source = "def unique_ids(ids):\n    return list(dict.fromkeys(ids))\n";
  const proposal = insertion("autosave-comment", "# Keep the first occurrence of each identifier.\n");
  const expected = proposal.newText + source;
  await writeFile(file, source, "utf8");
  const document = await vscode.workspace.openTextDocument(vscode.Uri.file(file));
  await vscode.window.showTextDocument(document, { preview: false });
  const settings = vscode.workspace.getConfiguration("files", document.uri);
  const previousSave = settings.inspect<string>("autoSave")?.workspaceValue;
  const previousDelay = settings.inspect<number>("autoSaveDelay")?.workspaceValue;
  let observedSave = false;
  const saved = vscode.workspace.onDidSaveTextDocument(item => {
    if (item.uri.toString() === document.uri.toString()) observedSave = true;
  });
  const context = fakeContext(extensionPath, join(root, "autosave-storage"), async () => {
    // Wait inside the real ownership commit so Auto Save wins this ordering.
    await until(async () => observedSave, async () => {});
    assert.equal(await readFile(file, "utf8"), expected, "Auto Save must finish before ownership persistence completes");
  });
  const suggestions = new Suggestions(context);
  try {
    await settings.update("autoSaveDelay", 1, vscode.ConfigurationTarget.Workspace);
    await settings.update("autoSave", "afterDelay", vscode.ConfigurationTarget.Workspace);
    const uri = document.uri.toString();
    const result: AutomaticAnalysisResult = {
      ...analysis(uri, source, proposal), annotatedSource: expected,
      annotatedSourceHash: sourceHash(expected), requirements: [],
    };
    await suggestions.applyAutomatic({ uri, version: document.version, source }, result);
    assert.ok(observedSave, "The test must exercise actual VS Code Auto Save");
    assert.equal(document.isDirty, false);
    assert.equal(document.getText(), expected);
    assert.equal(await readFile(file, "utf8"), expected);
    report.tests.push("Automatic annotation tolerates real Auto Save before ownership persistence completes");
  } finally {
    saved.dispose();
    suggestions.dispose();
    await settings.update("autoSave", previousSave, vscode.ConfigurationTarget.Workspace);
    await settings.update("autoSaveDelay", previousDelay, vscode.ConfigurationTarget.Workspace);
  }
}

interface HeldGeneration { started: Promise<void>; closed: Promise<void>; release(): void }

async function cancellationEditorCheck(api: AutomationApi, extensionPath: string,
  fixture: Awaited<ReturnType<typeof startModelFixture>>, report: TestReport): Promise<void> {
  const root = vscode.workspace.workspaceFolders![0].uri.fsPath;
  const file = join(root, `cancel-${randomUUID()}.py`);
  const before = "def rank(items):\n    return items\n";
  const after = "def rank(items):\n    return sorted(items, key=lambda item: item[1])\n";
  const hold = fixture.holdNextGeneration();
  const calls = fixture.tasks.length;
  try {
    await observedWrite(api.inbox, root, extensionPath, file, before, after, RANK_PROMPT);
    const processing = api.scanObservedEdits();
    void processing.catch(() => {}); // Assertions below still observe rejection without an unhandled-promise race.
    await bounded(hold.started, "The cancellation fixture did not receive generation");
    const document = await vscode.workspace.openTextDocument(vscode.Uri.file(file));
    const editor = await vscode.window.showTextDocument(document, { preview: false });
    const manual = "# human edit while the model is running\n";
    assert.ok(await editor.edit(builder => builder.insert(new vscode.Position(0, 0), manual)));
    await bounded(hold.closed, "The changed buffer did not cancel the in-flight provider request");
    await bounded(processing, "The cancelled automatic job did not finish");
    await api.scanObservedEdits();
    assert.equal(document.getText(), manual + after);
    assert.equal(await readFile(file, "utf8"), after);
    assert.equal(fixture.tasks.length, calls + 1, "Cancellation must prevent the verification call");
    await vscode.window.showTextDocument(document, { preview: false });
    await vscode.commands.executeCommand("workbench.action.files.revert");
    await api.scanObservedEdits();
    assert.equal(await readFile(file, "utf8"), after, "A cancelled stale event must not replay after reverting");
    report.tests.push("A human edit during inference closes the pending request and prevents insertion or verification");
  } finally { hold.release(); }
}

async function providerFailureEditorCheck(api: AutomationApi, extensionPath: string,
  fixture: Awaited<ReturnType<typeof startModelFixture>>, report: TestReport): Promise<void> {
  const root = vscode.workspace.workspaceFolders![0].uri.fsPath;
  const file = join(root, `provider-failure-${randomUUID()}.py`);
  const before = "def rank(items):\n    return items\n";
  const after = "def rank(items):\n    return sorted(items, key=lambda item: item[1])\n";
  fixture.setFailing(true);
  try {
    await observedWrite(api.inbox, root, extensionPath, file, before, after, RANK_PROMPT);
    await assert.rejects(api.scanObservedEdits(), /500|provider|request/i);
    const calls = fixture.tasks.length;
    await new Promise(resolve => setTimeout(resolve, 2_200)); // Allow two timer ticks without an explicit retry.
    assert.equal(fixture.tasks.length, calls, "A failed provider must not be retried by background polling");
    assert.equal(await readFile(file, "utf8"), after);
    fixture.setFailing(false);
    await api.scanObservedEdits();
    assert.equal(await readFile(file, "utf8"), "# " + RANK_COMMENT + "\n" + after);
    assert.equal(fixture.tasks.length, calls + 2, "Explicit retry must retain the observed event and run generation + verification");
    report.tests.push("Provider failures stop background retries while an explicit retry preserves the captured edit");
  } finally { fixture.setFailing(false); }
}

async function observedWrite(inbox: string, root: string, extensionPath: string, file: string,
  before: string, after: string, prompt: string): Promise<void> {
  const executable = resolveWorkerExecutable(extensionPath);
  const base = { session_id: "fixture-session-" + randomUUID(), turn_id: "fixture-turn-" + randomUUID(), cwd: root };
  const event = { ...base, tool_use_id: "fixture-tool-" + randomUUID(), tool_name: "Write",
    tool_input: { file_path: file, content: after } };
  await writeFile(file, before, "utf8");
  await hook(executable, inbox, root, "UserPromptSubmit", { ...base, prompt });
  await hook(executable, inbox, root, "PreToolUse", event);
  await writeFile(file, after, "utf8");
  await hook(executable, inbox, root, "PostToolUse", event);
  await hook(executable, inbox, root, "Stop", base);
}

/** Controlled transport fixture; actual local inference is exercised separately above. */
async function startModelFixture(): Promise<{
  endpoint: string; tasks: string[]; assertHealthy(): void; close(): Promise<void>;
  holdNextGeneration(): HeldGeneration; setFailing(value: boolean): void;
}> {
  const tasks: string[] = [];
  let failure: unknown;
  let failing = false;
  let pendingHold: { started(): void; closed(): void; released: Promise<void> } | undefined;
  const server = createServer((request, response) => {
    const chunks: Buffer[] = [];
    request.on("data", (chunk: Buffer) => chunks.push(chunk));
    request.on("end", async () => {
      try {
        const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
        assert.equal(body.model, "editor-fixture");
        assert.equal(request.url, "/v1/chat/completions");
        const payload = JSON.parse(body.messages[1].content);
        assert.ok(typeof payload.intent?.prompt === "string" && payload.intent.prompt.length > 0);
        assert.ok(typeof payload.currentSource === "string");
        const unique = String(payload.symbol).includes("unique_ids");
        assert.ok(payload.currentSource.includes(unique ? "dict.fromkeys(ids)" : "sorted(items, key=lambda item: item[1])"),
          "The model must receive the actual changed declaration");
        const quote: string = payload.intent.prompt.split(/\r?\n/).at(-1);
        const comment = unique ? UNIQUE_COMMENT : RANK_COMMENT;
        if (unique) assert.equal(payload.intent.prompt, "unique_ids must keep the first occurrence of each identifier.");
        else assert.ok(payload.intent.prompt.endsWith(RANK_PROMPT), "The original captured prompt must reach the provider");
        let result: unknown;
        if (payload.task === "comment_synthesis") {
          const choice = payload.evidenceChoices.find((item: { quote: string }) => item.quote === quote);
          assert.ok(choice, "The provider must receive an exact original evidence choice");
          result = { action: "synthesize_comment", comment, promptEvidence: { passageId: choice.id }, reason: "Supported by task context and implementation." };
        } else {
          assert.equal(payload.task, "comment_verification");
          assert.equal(payload.proposal.comment, comment);
          const evidence = payload.proposal.promptEvidence;
          assert.equal(payload.intent.prompt.slice(evidence.start, evidence.end), evidence.quote);
          assert.equal(evidence.quote, quote);
          result = { supported: true, reason: "The prompt evidence and code support this explanation." };
        }
        tasks.push(payload.task);
        if (failing) {
          response.writeHead(500, { "Content-Type": "application/json" });
          response.end(JSON.stringify({ error: "Intentional provider failure" }));
          return;
        }
        if (payload.task === "comment_synthesis" && pendingHold) {
          const hold = pendingHold;
          pendingHold = undefined;
          response.once("close", hold.closed);
          hold.started();
          await hold.released;
          if (response.destroyed) return;
        }
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ choices: [{ message: { content: JSON.stringify(result) } }], usage: { prompt_tokens: 10, completion_tokens: 15 } }));
      } catch (error) {
        failure = error;
        response.writeHead(500, { "Content-Type": "application/json" });
        response.end(JSON.stringify({ error: "Invalid editor fixture request" }));
      }
    });
  });
  await new Promise<void>((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  return {
    endpoint: `http://127.0.0.1:${address.port}/v1/chat/completions`, tasks,
    setFailing(value) { failing = value; },
    holdNextGeneration() {
      assert.equal(pendingHold, undefined);
      const started = signal(), closed = signal(), released = signal();
      pendingHold = { started: started.resolve, closed: closed.resolve, released: released.promise };
      return { started: started.promise, closed: closed.promise, release: released.resolve };
    },
    assertHealthy() { if (failure) throw failure; },
    close: () => new Promise<void>((resolve, reject) => {
      server.closeAllConnections();
      server.close(error => error ? reject(error) : resolve());
    }),
  };
}

function signal(): { promise: Promise<void>; resolve(): void } {
  let complete!: () => void;
  const promise = new Promise<void>(resolve => { complete = resolve; });
  return { promise, resolve: () => complete() };
}

async function bounded<T>(operation: Promise<T>, message: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([operation, new Promise<T>((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), 10_000);
    })]);
  } finally { if (timer) clearTimeout(timer); }
}

async function hook(executable: string, inbox: string, workspace: string, eventName: string,
  event: Record<string, unknown>): Promise<unknown> {
  const output = await new Promise<string>((resolve, reject) => {
    const child = spawn(executable, ["--capture-hook", "--source", "codex", "--inbox", inbox,
      "--workspace", workspace, "--event", eventName], { shell: false, windowsHide: true, cwd: workspace });
    let stdout = "", stderr = "";
    const timer = setTimeout(() => { child.kill(); reject(new Error(`Hook ${eventName} timed out`)); }, 15_000);
    child.stdout.on("data", (part: Buffer) => { stdout += part.toString("utf8"); });
    child.stderr.on("data", (part: Buffer) => { stderr += part.toString("utf8"); });
    child.once("error", error => { clearTimeout(timer); reject(error); });
    child.once("close", code => {
      clearTimeout(timer);
      if (code !== 0 || stderr.trim()) reject(new Error(`Hook ${eventName} failed: ${stderr.slice(0, 500)}`));
      else resolve(stdout);
    });
    child.stdin.end(JSON.stringify({ ...event, hook_event_name: eventName }));
  });
  return output.trim() ? JSON.parse(output) : {};
}

async function until(check: () => Promise<boolean>, scan: () => Promise<void>): Promise<void> {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    await scan();
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.fail("Automatic editor processing did not finish within ten seconds");
}

function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name} before launching the extension host test.`);
  return value;
}

function fakeContext(extensionPath: string, storage: string, beforeUpdate?: () => Promise<void>): vscode.ExtensionContext {
  const values = new Map<string, unknown>();
  const state = {
    keys: () => Array.from(values.keys()),
    get: <T>(key: string, fallback?: T): T | undefined => values.has(key) ? values.get(key) as T : fallback,
    update: async (key: string, value: unknown): Promise<void> => { await beforeUpdate?.(); values.set(key, value); },
  };
  return { extensionPath, extensionUri: vscode.Uri.file(extensionPath), globalStorageUri: vscode.Uri.file(storage), workspaceState: state, globalState: state, secrets: { get: async () => undefined } } as unknown as vscode.ExtensionContext;
}

function insertion(id: string, newText: string): Suggestion {
  return {
    id, symbolId: "unique_ids", symbolName: "unique_ids", oldText: "", newText,
    range: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } },
    commentText: newText.trim(), codeHash: sourceHash("unique_ids-body"),
    explanation: "The task requires stable ordering.", evidence: ["task intent", "dict.fromkeys preserves insertion order"],
  };
}

function analysis(uri: string, source: string, suggestion: Suggestion): AnalysisResult {
  return {
    uri, sourceHash: sourceHash(source), suggestions: [suggestion], skipped: [],
    metrics: { inputTokens: 0, outputTokens: 0, modelCalls: 0, elapsedMs: 0 },
  };
}

