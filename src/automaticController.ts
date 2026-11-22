import * as vscode from "vscode";
import { readFile } from "node:fs/promises";
import * as path from "node:path";
import { readObservedEditsDetailed } from "./automation";
import { sourceHash } from "./controller";
import { RuntimeManager } from "./runtime";
import { Suggestions } from "./suggestions";
import { AutomaticAnalysisResult, Metrics, RequirementRecord } from "./types";
import { WorkerResponseError } from "./workerClient";

interface Callbacks {
  isBusy(): boolean;
  status(message: string, running: boolean): void;
  beginEdit(uri: string): void;
  endEdit(uri: string): void;
  error(error: unknown): void;
  log(message: string): void;
  analysis(metrics: Metrics, skipped: number): void;
}
interface PendingHistory { records: RequirementRecord[]; path: string; annotatedHash: string; }
const PROCESSED = "automatic.processedEvents";
const PENDING = "automatic.pendingHistory";

/** Bridges completed tool turns to atomic editor edits; it never guesses a prompt. */
export class AutomaticController implements vscode.Disposable {
  private readonly processed: Set<string>;
  private readonly timer: ReturnType<typeof setInterval>;
  private pendingScan?: Promise<void>;
  private disposed = false;
  private failed = false;
  private active?: { uri: string; abort: AbortController };
  readonly inbox: string;

  constructor(private readonly context: vscode.ExtensionContext, private readonly runtime: RuntimeManager,
    private readonly suggestions: Suggestions, private readonly callbacks: Callbacks) {
    this.inbox = path.join(context.globalStorageUri.fsPath, "intents");
    this.processed = new Set(context.workspaceState.get<string[]>(PROCESSED, []));
    this.timer = setInterval(() => { void this.scan(false).catch(callbacks.error); }, 1000);
    this.timer.unref();
  }

  enabled(): boolean {
    const setting = vscode.workspace.getConfiguration("between-the-lines").inspect<boolean>("automaticAnnotations");
    return setting?.globalValue ?? setting?.defaultValue ?? true;
  }

  async setEnabled(enabled: boolean): Promise<void> {
    await vscode.workspace.getConfiguration("between-the-lines").update("automaticAnnotations", enabled, vscode.ConfigurationTarget.Global);
    if (enabled) this.failed = false;
    if (!enabled) {
      this.active?.abort.abort();
      await this.pendingScan?.catch(() => {});
    }
    this.callbacks.status(enabled ? "Waiting for a completed connected-agent edit." : "Automatic annotations paused.", false);
  }

  /** Also used by the real editor integration test to wait for a completed turn. */
  scan(retry = true): Promise<void> {
    if (retry) this.failed = false;
    if (this.pendingScan) return this.pendingScan;
    const operation = this.poll().finally(() => { if (this.pendingScan === operation) this.pendingScan = undefined; });
    this.pendingScan = operation;
    return operation;
  }

  private async poll(): Promise<void> {
    if (this.disposed || this.failed || this.callbacks.isBusy() || !vscode.workspace.isTrusted || vscode.env.remoteName) return;
    const roots = vscode.workspace.workspaceFolders?.filter(folder => folder.uri.scheme === "file").map(folder => folder.uri.fsPath) ?? [];
    if (!roots.length) return;
    let running = false;
    let completed = false;
    try {
      const pending = this.context.workspaceState.get<PendingHistory>(PENDING);
      if (pending?.records.length) {
        // A crash before applying must not commit an annotation that never reached disk.
        let applied = false;
        try { applied = sourceHash(await readFile(pending.path, "utf8")) === pending.annotatedHash; } catch { /* File was removed. */ }
        if (applied) await this.runtime.recordRequirements(this.inbox, pending.records);
        await this.context.workspaceState.update(PENDING, undefined);
      }
      if (this.enabled() && !this.runtime.canAnnotateAutomatically()) {
        this.callbacks.status(this.runtime.getState().ready
          ? "Reconfigure the custom provider to approve automatic use of captured tasks and code."
          : "Prepare the local model or configure a provider. Captured edits will wait until it is ready.", false);
        return;
      }
      const incoming = await readObservedEditsDetailed(this.inbox, roots, this.processed);
      for (const item of incoming.skipped) this.processed.add(item.id);
      let applied = 0;
      let skipped = 0;
      const totals: Metrics = { modelCalls: 0, inputTokens: 0, outputTokens: 0, elapsedMs: 0 };
      for (const event of incoming.edits) {
        if (!this.enabled()) { this.processed.add(event.id); continue; }
        if (this.callbacks.isBusy() && !running) break;
        if (this.disposed) break;
        running = true;
        this.callbacks.status("Generating comments from the captured task and changed code...", true);
        for (const file of event.files) {
          if (this.disposed || !this.enabled()) break;
          const uri = vscode.Uri.file(file.path);
          const document = await vscode.workspace.openTextDocument(uri);
          if (document.isDirty || document.getText() !== file.afterSource || sourceHash(await readFile(file.path, "utf8")) !== (file.afterRawHash ?? file.afterHash)) {
            skipped++; continue;
          }
          const captured = { uri: uri.toString(), version: document.version, source: document.getText() };
          const knownRequirements = await this.runtime.knownRequirements(this.inbox, [file.path], event.workspaceRoot);
          let result: AutomaticAnalysisResult;
          const active = { uri: captured.uri, abort: new AbortController() };
          this.active = active;
          try { result = await this.runtime.annotateObserved({
            uri: captured.uri, source: captured.source, previousSource: file.beforeSource,
            language: file.path.toLowerCase().endsWith(".py") ? "python" : "java",
            intent: { prompt: event.prompt, source: event.source, eventId: event.eventId,
              sessionId: event.sessionId, turnId: event.turnId, timestamp: event.timestamp },
            workspaceRoot: event.workspaceRoot, knownRequirements,
            ownedComments: this.suggestions.owned(captured.uri), maxSuggestions: this.commentBudget(),
          }, active.abort.signal); } catch (error) {
            if (active.abort.signal.aborted) { skipped++; continue; }
            if (!(error instanceof WorkerResponseError) || !["invalid_source", "unsafe_source", "unsafe_encoding", "source_limit"].includes(error.code)) throw error;
            skipped++;
            this.callbacks.log(`Skipped ${path.basename(file.path)}: ${error.message}`);
            continue;
          } finally { if (this.active === active) this.active = undefined; }
          skipped += result.skipped.length;
          for (const key of ["modelCalls", "inputTokens", "outputTokens", "elapsedMs"] as const) totals[key] += result.metrics[key];
          for (const item of result.skipped) this.callbacks.log(`Skipped ${path.basename(file.path)} ${item.symbolId}: ${item.reason}`);
          if (!result.suggestions.length) continue;
          if (!this.enabled() || this.disposed || document.isDirty || document.version !== captured.version ||
              sourceHash(await readFile(file.path, "utf8")) !== (file.afterRawHash ?? file.afterHash)) { skipped++; continue; }
          const records = result.requirements.map(record => ({ ...record, path: file.path, workspaceRoot: event.workspaceRoot }));
          // If the editor closes after applying, the next activation can finish the history commit.
          const bom = (file.afterRawHash ?? file.afterHash) === file.afterHash ? "" : "\uFEFF";
          if (records.length) await this.context.workspaceState.update(PENDING, {
            records, path: file.path, annotatedHash: sourceHash(bom + result.annotatedSource),
          } satisfies PendingHistory);
          this.callbacks.beginEdit(captured.uri);
          try {
            await this.suggestions.applyAutomatic(captured, result);
          } catch (error) {
            await this.context.workspaceState.update(PENDING, undefined);
            throw error;
          } finally { this.callbacks.endEdit(captured.uri); }
          if (records.length) {
            await this.runtime.recordRequirements(this.inbox, records);
            await this.context.workspaceState.update(PENDING, undefined);
          }
          applied += result.suggestions.length;
        }
        this.processed.add(event.id);
      }
      if (this.processed.size > 2000) {
        const keep = Array.from(this.processed).slice(-2000);
        this.processed.clear(); for (const id of keep) this.processed.add(id);
      }
      await this.context.workspaceState.update(PROCESSED, Array.from(this.processed));
      completed = true;
      if (running) this.callbacks.analysis(totals, skipped);
      if (!running && !this.callbacks.isBusy() && this.enabled() && incoming.skipped.some(item => item.status === "noVerifiedEdit")) {
        this.callbacks.status("Agent turn finished without a supported verified edit. Shell writes and ambiguous patches are skipped.", false);
      }
      if (running) this.callbacks.status(`${applied} annotation${applied === 1 ? "" : "s"} applied; ${skipped} declaration${skipped === 1 ? "" : "s"} skipped. Original context retained locally.`, false);
    } catch (error) {
      // A failed endpoint must not trigger more billable requests every second.
      this.failed = true;
      throw error;
    } finally {
      if (running && !completed && !this.disposed) this.callbacks.status("Automatic annotation stopped. Check the provider, then run Check Completed Agent Edits to retry.", false);
    }
  }

  private commentBudget(): number {
    const setting = vscode.workspace.getConfiguration("between-the-lines").inspect<number>("automaticCommentsPerFile");
    const value = setting?.globalValue ?? setting?.defaultValue ?? 1;
    return Number.isFinite(value) ? Math.min(3, Math.max(1, Math.floor(value))) : 1;
  }

  retryPending(): void { this.failed = false; }

  invalidate(uri: string): void { if (this.active?.uri === uri) this.active.abort.abort(); }

  dispose(): void { this.disposed = true; this.active?.abort.abort(); clearInterval(this.timer); }
}
