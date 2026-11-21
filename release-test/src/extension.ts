import * as vscode from "vscode";
import * as path from "node:path";
import { realpath } from "node:fs/promises";
import { AnalysisQueue, isCurrent, SourceSnapshot, sourceHash } from "./controller";
import { listCapturedIntents, installIntegration, connectionKey } from "./hooks";
import { connectionStatus, HookConnection } from "./hookConfig";
import { AutomaticController } from "./automaticController";
import { ContextDetails } from "./contextDetails";
import { RuntimeManager } from "./runtime";
import { configureProvider } from "./providers";
import { Sidebar, SidebarAction, SidebarState } from "./view";
import { Suggestions } from "./suggestions";
import { AnalyzeParams, Intent, Metrics } from "./types";

interface AnalysisJob {
  snapshot: SourceSnapshot;
  previousSource?: string;
  intent: Intent;
  workspaceRoot: string;
  force: boolean;
}

export function activate(context: vscode.ExtensionContext): { inbox: string; scanObservedEdits(): Promise<void>; getAutomaticMetrics(): Metrics | undefined; testGlobalState?: vscode.Memento } {
  const runtime = new RuntimeManager(context);
  const proposals = new Suggestions(context);
  const contextDetails = new ContextDetails();
  const output = vscode.window.createOutputChannel("between-the-lines");
  const lastSaved = new Map<string, string>();
  const lastAnalyzed = new Map<string, string>();
  const ownEdits = new Set<string>();
  const acceptedVersions = new Map<string, number>();
  const running = new Map<string, vscode.CancellationTokenSource>();
  const selectedIntents = new Map<string, Intent>();
  let busy = false;
  let configuringProvider = false;
  let automaticControllerBusy = false;
  let message = "Prepare a model, then connect Codex to generate comments from the task and its code changes.";
  let annotationStatus = "Waiting for a completed connected-agent edit.";
  let automaticController: AutomaticController | undefined;
  let error: string | undefined;
  let metrics: Metrics | undefined;
  let automaticMetrics: Metrics | undefined;
  let skipped = 0;
  let disposed = false;

  const state = (): SidebarState => {
    const editor = vscode.window.activeTextEditor;
    const folder = editor ? vscode.workspace.getWorkspaceFolder(editor.document.uri) : vscode.workspace.workspaceFolders?.[0];
    const model = runtime.getState();
    const connection = folder ? context.workspaceState.get<HookConnection>(connectionKey(folder.uri.fsPath)) : undefined;
    return {
      ready: model.ready, runtimeLabel: model.label, progress: model.progress,
      provider: model.provider, providerLabel: model.providerLabel,
      localModelName: model.localModelName, localModelSizeBytes: model.localModelSizeBytes,
      automaticCustomProvider: model.provider !== "custom" || runtime.canAnnotateAutomatically(),
      file: editor ? vscode.workspace.asRelativePath(editor.document.uri) : "",
      intent: folder ? selectedIntents.get(folder.uri.fsPath) : undefined,
      automatic: vscode.workspace.getConfiguration("between-the-lines").get<boolean>("automatic", false) &&
        model.ready && !!(folder && selectedIntents.get(folder.uri.fsPath)?.prompt.trim()),
      automaticAnnotations: automaticController?.enabled() ?? true,
      connected: process.platform === "win32" && connection?.source === "copilot"
        ? "Copilot prompt capture is unavailable in this Windows beta. Connect Codex."
        : connectionStatus(connection, context.extension.packageJSON.version),
      annotationStatus, busy, message, error: error ?? model.error, metrics, skipped,
      suggestions: proposals.all().flatMap(batch => batch.result.suggestions.map(item => ({
        uri: batch.snapshot.uri, id: item.id, symbolName: item.symbolName,
        commentText: item.commentText, explanation: item.explanation, evidence: item.evidence,
      }))),
    };
  };
  const publish = (): void => { if (!disposed) sidebar.update(state()); };
  const fail = (reason: unknown): void => {
    error = reason instanceof Error ? reason.message : String(reason);
    output.appendLine(error);
    publish();
  };

  const queue = new AnalysisQueue<AnalysisJob>(async (job, latest) => {
    if (automaticControllerBusy) { message = "Automatic annotations are running. Analyze again when they finish."; publish(); return; }
    const cancellation = new vscode.CancellationTokenSource();
    running.set(job.snapshot.uri, cancellation);
    busy = true; error = undefined; message = "Checking changed declarations..."; publish();
    try {
      const document = vscode.workspace.textDocuments.find(item => item.uri.toString() === job.snapshot.uri);
      if (!document || !latest() || !isCurrent(job.snapshot, snapshot(document), sourceHash(job.snapshot.source))) return;
      const params: AnalyzeParams = {
        uri: job.snapshot.uri, source: job.snapshot.source, previousSource: job.previousSource,
        language: language(document)!, intent: job.intent, workspaceRoot: job.workspaceRoot,
        contextFiles: await contextFiles(document, job.workspaceRoot),
        knownRequirements: await runtime.knownRequirements(automaticController!.inbox, [document.uri.fsPath], job.workspaceRoot),
        ownedComments: proposals.owned(job.snapshot.uri),
        maxSuggestions: Math.min(3, Math.max(1, vscode.workspace.getConfiguration("between-the-lines").get<number>("maxSuggestions", 3))),
        force: job.force,
      };
      if (cancellation.token.isCancellationRequested || !latest()) return;
      const result = await runtime.analyze(params, cancellation.token);
      if (cancellation.token.isCancellationRequested || !latest() ||
        !isCurrent(job.snapshot, snapshot(document), result.sourceHash)) {
        message = "The file changed during analysis. Outdated suggestions were discarded.";
        return;
      }
      proposals.replace(job.snapshot, result);
      lastAnalyzed.set(job.snapshot.uri, job.snapshot.source);
      metrics = result.metrics; skipped = result.skipped.length;
      message = result.suggestions.length ? `${result.suggestions.length} comment suggestion${result.suggestions.length === 1 ? "" : "s"} ready to review.` :
        `No comment changes proposed.${result.skipped[0] ? ` ${result.skipped[0].reason}` : ""}`;
    } catch (reason) {
      if (cancellation.token.isCancellationRequested || !latest()) message = "Analysis stopped because the file or task context changed.";
      else throw reason;
    } finally {
      cancellation.dispose(); running.delete(job.snapshot.uri); busy = false; publish();
    }
  }, fail);

  async function schedule(document: vscode.TextDocument, previousSource: string | undefined, force: boolean): Promise<void> {
    const captured = snapshot(document);
    const folder = await localFolder(document);
    const intent = selectedIntents.get(folder.uri.fsPath);
    if (automaticControllerBusy) throw new Error("Wait for the automatic annotation to finish.");
    if (!runtime.getState().ready) throw new Error("Prepare the local model or configure a custom API first.");
    if (!intent?.prompt.trim()) throw new Error("Attach task context or select a captured prompt first.");
    if (document.version !== captured.version || document.getText() !== captured.source) return;
    error = undefined;
    const delay = force ? 0 : Math.max(300, vscode.workspace.getConfiguration("between-the-lines").get<number>("debounceMs", 1800));
    queue.enqueue(captured.uri, { snapshot: captured, previousSource, intent: { ...intent }, workspaceRoot: folder.uri.fsPath, force }, delay);
    message = force ? "Analysis queued." : "Waiting for saved changes to settle..."; publish();
  }

  function invalidate(uri: string): void {
    queue.invalidate(uri);
    running.get(uri)?.cancel();
    proposals.invalidate(uri);
  }

  function contextChanged(): void {
    for (const document of vscode.workspace.textDocuments) invalidate(document.uri.toString());
    message = "Task context updated. Analyze the file or save a new change.";
    error = undefined; publish();
  }

  async function activeFolder(): Promise<vscode.WorkspaceFolder> {
    const document = vscode.window.activeTextEditor?.document;
    if (document) return localFolder(document, false);
    if (!vscode.workspace.isTrusted || vscode.env.remoteName) throw new Error("Open a trusted local workspace to use between-the-lines.");
    const folder = vscode.workspace.workspaceFolders?.find(item => item.uri.scheme === "file");
    if (!folder) throw new Error("Open a local project folder first.");
    return folder;
  }

  async function setIntent(prompt?: string): Promise<void> {
    const folder = await activeFolder();
    const existing = selectedIntents.get(folder.uri.fsPath)?.prompt;
    const text = prompt ?? await vscode.window.showInputBox({ title: "Task context", prompt: "What is the intent behind these code changes?", value: existing, ignoreFocusOut: true });
    if (text === undefined) return;
    if (text.length > 16000) throw new Error("Keep task context under 16,000 characters.");
    if (text.trim()) selectedIntents.set(folder.uri.fsPath, { source: "manual", prompt: text.trim() });
    else selectedIntents.delete(folder.uri.fsPath);
    contextChanged();
  }

  async function selectIntent(): Promise<void> {
    const folder = await activeFolder();
    const events = await listCapturedIntents(context, folder.uri.fsPath);
    if (!events.length) { message = "No captured prompts for this workspace. Connect a coding tool or enter context manually."; publish(); return; }
    const choice = await vscode.window.showQuickPick(events.map(intent => ({
      label: intent.prompt.replace(/\s+/g, " ").slice(0, 110),
      description: `${intent.source} · session ${intent.sessionId?.slice(0, 12) ?? "unknown"}`,
      detail: intent.timestamp, intent,
    })), { title: "Select task context", placeHolder: "Choose the prompt associated with these edits", matchOnDescription: true });
    if (!choice) return;
    selectedIntents.set(folder.uri.fsPath, {
      prompt: choice.intent.prompt, source: choice.intent.source,
      sessionId: choice.intent.sessionId, timestamp: choice.intent.timestamp, eventId: choice.intent.id,
    });
    contextChanged();
  }

  async function setAutomatic(enabled: boolean): Promise<void> {
    const folder = await activeFolder();
    if (enabled && (!runtime.getState().ready || !selectedIntents.get(folder.uri.fsPath)?.prompt.trim())) {
      throw new Error("Prepare the model and select task context before enabling analysis after saves.");
    }
    await vscode.workspace.getConfiguration("between-the-lines").update("automatic", enabled, vscode.ConfigurationTarget.Workspace);
    publish();
  }

  async function action(data: SidebarAction): Promise<void> {
    try {
      switch (data.action) {
        case "ready": publish(); break;
        case "configureProvider":
          if (busy) throw new Error("Wait for the current operation to finish.");
          configuringProvider = true;
          try {
            if (await configureProvider(context)) {
              for (const document of vscode.workspace.textDocuments) invalidate(document.uri.toString());
              message = "Provider configured. Connected edits use the captured task and changed code; manual analysis accepts task context here.";
              error = undefined;
            }
          } finally { configuringProvider = false; await runtime.refreshProvider(); automaticController?.retryPending(); publish(); }
          break;
        case "setup": await runtime.ensureReady(); automaticController?.retryPending(); message = "Model ready. Connect Codex for automatic comments, or add task context for manual analysis."; publish(); break;
        case "unload":
          if (busy) throw new Error("Wait for the current analysis to finish before unloading.");
          await runtime.unload(); message = "Worker stopped. It will restart for the next analysis."; publish(); break;
        case "analyze": {
          const document = vscode.window.activeTextEditor?.document;
          if (!document) throw new Error("Open a Python or Java file first.");
          await schedule(document, lastSaved.get(document.uri.toString()), true); break;
        }
        case "intent": await setIntent(data.prompt); break;
        case "selectIntent": await selectIntent(); break;
        case "automatic": await setAutomatic(data.enabled); break;
        case "automaticAnnotations": await automaticController?.setEnabled(data.enabled); publish(); break;
        case "reviewRequirements": await reviewRequirements(); break;
        case "connectCodex": await installIntegration(context, "codex"); message = "Prepare your selected model and approve the hooks in Codex /hooks. Comments are generated after the turn finishes."; publish(); break;
        case "connectCopilot": await installIntegration(context, "copilot"); message = "Copilot supports prompt capture here. Select a captured prompt for manual analysis; automatic edit tracking is currently verified for Codex."; publish(); break;
        case "preview": await proposals.preview(data.uri, data.id); break;
        case "dismiss": proposals.dismiss(data.uri, data.id); break;
        case "accept":
          ownEdits.add(data.uri);
          try {
            await proposals.accept(data.uri, data.id);
            const document = vscode.workspace.textDocuments.find(item => item.uri.toString() === data.uri);
            if (document) {
              lastSaved.set(data.uri, document.getText());
              lastAnalyzed.set(data.uri, document.getText());
              acceptedVersions.set(data.uri, document.version);
            }
            message = "Comment applied. Review and save your changes; analyze again for more suggestions.";
          } finally { ownEdits.delete(data.uri); publish(); }
          break;
      }
    } catch (reason) { fail(reason); }
  }

  function beginEdit(uri: string): void { invalidate(uri); ownEdits.add(uri); }
  function endEdit(uri: string): void {
    const document = vscode.workspace.textDocuments.find(item => item.uri.toString() === uri);
    if (document) {
      lastSaved.set(uri, document.getText()); lastAnalyzed.set(uri, document.getText());
      acceptedVersions.set(uri, document.version);
    }
    ownEdits.delete(uri); publish();
  }

  async function reviewRequirements(): Promise<void> {
    if (busy || configuringProvider) throw new Error("Wait for the current operation to finish.");
    const document = vscode.window.activeTextEditor?.document;
    if (!document) throw new Error("Open a Python or Java file first.");
    const folder = await localFolder(document);
    const records = await runtime.knownRequirements(automaticController!.inbox, [document.uri.fsPath], folder.uri.fsPath);
    if (!records.length) { message = "No recorded requirements for this file."; publish(); return; }
    const choice = await vscode.window.showQuickPick(records.map(record => ({
      label: record.symbolName, description: `${record.id} · ${record.scopeStatus ?? "recorded"}`,
      detail: record.commentText, record,
    })), { title: "Recorded requirements", placeHolder: "Choose an original requirement to inspect or retire", matchOnDetail: true });
    if (!choice) return;
    const decision = await vscode.window.showQuickPick([
      { label: "Inspect original prompt and generated comment", detail: "Read the source evidence and model provenance separately.", action: "inspect" },
      { label: "Keep requirement", detail: choice.record.quote, action: "keep" },
      { label: "Retire requirement", detail: "Remove its exact generated comment and stop reinjecting it. Original evidence remains in local history.", action: "retire" },
    ], { title: `${choice.record.symbolName} · ${choice.record.id}`, placeHolder: "Retire only when the original requirement no longer applies" });
    if (decision?.action === "inspect") { await contextDetails.open(choice.record); return; }
    if (decision?.action !== "retire" || !choice.record.id) return;
    if (document.isDirty) throw new Error("Save this file before retiring a requirement.");
    busy = true; publish();
    try {
      const captured = snapshot(document);
      const result = await runtime.retireAnnotation({ uri: captured.uri, source: captured.source, language: language(document)!, requirement: choice.record });
      if (result.suggestions.length) {
        beginEdit(captured.uri);
        try { await proposals.applyAutomatic(captured, result); } finally { endEdit(captured.uri); }
      }
      await runtime.retireRequirement(automaticController!.inbox, choice.record.id, folder.uri.fsPath);
      message = "Requirement retired. Its original prompt remains in local history.";
    } finally { busy = false; publish(); }
  }

  const sidebar = new Sidebar(context.extensionUri, state(), action);
  automaticController = new AutomaticController(context, runtime, proposals, {
    isBusy: () => busy || configuringProvider,
    status: (text, active) => { annotationStatus = text; automaticControllerBusy = active; busy = active; if (active) error = undefined; publish(); },
    beginEdit, endEdit, error: fail, log: text => output.appendLine(text),
    analysis: (value, count) => { automaticMetrics = { ...value }; metrics = value; skipped = count; publish(); },
  });
  for (const document of vscode.workspace.textDocuments) if (language(document)) {
    lastSaved.set(document.uri.toString(), document.getText());
    lastAnalyzed.set(document.uri.toString(), document.getText());
  }
  context.subscriptions.push(runtime, proposals, output, queue, automaticController,
    vscode.window.registerWebviewViewProvider("between-the-lines.sidebar", sidebar),
    vscode.workspace.registerTextDocumentContentProvider("between-the-lines-preview", proposals),
    vscode.workspace.registerTextDocumentContentProvider("between-the-lines-context", contextDetails),
    runtime.onDidChangeState(publish), proposals.onDidChangeSuggestions(publish),
    context.secrets.onDidChange(event => {
      if (configuringProvider || !runtime.isProviderSecret(event.key)) return;
      for (const document of vscode.workspace.textDocuments) invalidate(document.uri.toString());
      void runtime.refreshProvider().then(() => { automaticController?.retryPending(); publish(); }).catch(fail);
    }),
    vscode.window.onDidChangeActiveTextEditor(publish),
    vscode.workspace.onDidChangeConfiguration(event => {
      if (!event.affectsConfiguration("between-the-lines")) return;
      if (!configuringProvider && ["provider", "endpoint", "model", "inferenceThreads", "idleSeconds"].some(key => event.affectsConfiguration(`between-the-lines.${key}`))) {
        for (const document of vscode.workspace.textDocuments) invalidate(document.uri.toString());
        void runtime.refreshProvider().then(() => { automaticController?.retryPending(); publish(); }).catch(fail);
      } else publish();
    }),
    vscode.workspace.onDidOpenTextDocument(document => {
      if (language(document)) {
        lastSaved.set(document.uri.toString(), document.getText());
        lastAnalyzed.set(document.uri.toString(), document.getText());
      }
    }),
    vscode.workspace.onDidChangeTextDocument(event => { if (event.contentChanges.length && !ownEdits.has(event.document.uri.toString())) {
      const uri = event.document.uri.toString(); invalidate(uri); automaticController?.invalidate(uri);
    } }),
    vscode.workspace.onDidCloseTextDocument(document => {
      const uri = document.uri.toString(); invalidate(uri);
      lastSaved.delete(uri); lastAnalyzed.delete(uri); acceptedVersions.delete(uri);
    }),
    vscode.workspace.onDidSaveTextDocument(document => {
      if (!language(document)) return;
      const uri = document.uri.toString();
      const previousSource = lastSaved.get(uri);
      lastSaved.set(uri, document.getText());
      if (ownEdits.has(uri) || acceptedVersions.get(uri) === document.version || previousSource === document.getText()) return;
      const folder = vscode.workspace.getWorkspaceFolder(document.uri);
      if (vscode.workspace.getConfiguration("between-the-lines").get<boolean>("automatic", false) && runtime.getState().ready &&
        folder && selectedIntents.get(folder.uri.fsPath)?.prompt.trim()) {
        void schedule(document, lastAnalyzed.get(uri), false).catch(fail);
      }
    }),
    { dispose: () => { disposed = true; for (const cancellation of running.values()) { cancellation.cancel(); cancellation.dispose(); } } },
  );
  const commands: Record<string, () => Promise<void>> = {
    "between-the-lines.configureProvider": () => action({ action: "configureProvider" }),
    "between-the-lines.setup": () => action({ action: "setup" }),
    "between-the-lines.analyzeFile": () => action({ action: "analyze" }),
    "between-the-lines.setIntent": () => setIntent().catch(fail),
    "between-the-lines.selectCapturedIntent": () => action({ action: "selectIntent" }),
    "between-the-lines.connectCodex": () => action({ action: "connectCodex" }),
    "between-the-lines.connectCopilot": () => action({ action: "connectCopilot" }),
    "between-the-lines.unloadModel": () => action({ action: "unload" }),
    "between-the-lines.toggleAutomatic": () => setAutomatic(!state().automatic).catch(fail),
    "between-the-lines.toggleAutomaticAnnotations": () => automaticController!.setEnabled(!automaticController!.enabled()).catch(fail),
    "between-the-lines.scanObservedEdits": () => automaticController!.scan(),
    "between-the-lines.reviewRequirements": () => action({ action: "reviewRequirements" }),
  };
  for (const [id, callback] of Object.entries(commands)) context.subscriptions.push(vscode.commands.registerCommand(id, callback));
  return { inbox: automaticController.inbox, scanObservedEdits: () => automaticController!.scan(),
    getAutomaticMetrics: () => automaticMetrics ? { ...automaticMetrics } : undefined,
    // Isolated extension-host tests seed fixture consent without driving the setup UI.
    ...(context.extensionMode === vscode.ExtensionMode.Test ? { testGlobalState: context.globalState } : {}) };
}

function snapshot(document: vscode.TextDocument): SourceSnapshot {
  return { uri: document.uri.toString(), version: document.version, source: document.getText() };
}

function language(document: vscode.TextDocument): "python" | "java" | undefined {
  if (document.languageId === "python" || document.languageId === "java") return document.languageId;
  return undefined;
}

async function localFolder(document: vscode.TextDocument, requireLanguage = true): Promise<vscode.WorkspaceFolder> {
  if (!vscode.workspace.isTrusted) throw new Error("Trust this workspace before using between-the-lines.");
  if (vscode.env.remoteName || document.uri.scheme !== "file") throw new Error("This version supports local desktop files. Remote, container, and browser workspaces are not supported yet.");
  if (requireLanguage && !language(document)) throw new Error("Open a Python or Java source file.");
  const folder = vscode.workspace.getWorkspaceFolder(document.uri);
  if (!folder || folder.uri.scheme !== "file") throw new Error("The source file must be inside an open local project folder.");
  const [root, file] = await Promise.all([realpath(folder.uri.fsPath), realpath(document.uri.fsPath)]);
  const relative = path.relative(root, file);
  if (relative.startsWith(`..${path.sep}`) || relative === ".." || path.isAbsolute(relative)) throw new Error("The source file resolves outside the project folder.");
  return folder;
}

async function contextFiles(document: vscode.TextDocument, workspaceRoot: string): Promise<string[]> {
  const exclusions = "**/{node_modules,.git,.venv,venv,dist,build}/**";
  const [siblings, tests] = await Promise.all([
    vscode.workspace.findFiles(new vscode.RelativePattern(path.dirname(document.uri.fsPath), "*.{py,java}"), exclusions, 8),
    vscode.workspace.findFiles(new vscode.RelativePattern(workspaceRoot, "**/{test*,*Test*}.{py,java}"), exclusions, 8),
  ]);
  const result = new Set<string>();
  for (const uri of [...siblings, ...tests]) {
    if (uri.scheme !== "file" || uri.toString() === document.uri.toString()) continue;
    const relative = path.relative(workspaceRoot, uri.fsPath);
    if (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)) result.add(relative.replace(/\\/g, "/"));
  }
  return Array.from(result).slice(0, 12);
}
