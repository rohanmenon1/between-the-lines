import * as vscode from "vscode";
import { createHash } from "node:crypto";
import { access, readFile } from "node:fs/promises";
import { join } from "node:path";
import { downloadModel, ModelManifest, validateManifest, verifyModel } from "./download";
import { WorkerClient } from "./workerClient";
import { ProviderConfig, hasAutomaticProviderConsent, providerLabel, providerSecretKey, readProviderConfig } from "./providers";
import { AnalysisResult, AnalyzeParams, AutomaticAnalysisResult, ObservedAnalyzeParams, RequirementRecord, WorkerConfiguration, WorkerStatus } from "./types";

export interface RuntimeState {
  ready: boolean;
  label: string;
  progress?: number;
  error?: string;
  modelPath?: string;
  localModelName?: string;
  localModelSizeBytes?: number;
  provider?: "local" | "custom";
  providerLabel?: string;
}

export function resolveWorkerExecutable(extensionPath: string): string {
  return join(extensionPath, "worker", "bin", "btl-worker", process.platform === "win32" ? "btl-worker.exe" : "btl-worker");
}

/** Validate the process boundary before any worker-owned fields reach the UI. */
export function validAnalysisResult(value: unknown, params: AnalyzeParams): value is AnalysisResult {
  const result = value as Partial<AnalysisResult> | null;
  const text = (item: unknown, limit: number): item is string => typeof item === "string" && item.length <= limit;
  const integer = (item: unknown): item is number => Number.isSafeInteger(item) && (item as number) >= 0;
  const point = (item: unknown): boolean => {
    const position = item as { line?: unknown; character?: unknown } | null;
    return !!position && integer(position.line) && integer(position.character);
  };
  if (!result || result.uri !== params.uri || result.sourceHash !== createHash("sha256").update(params.source).digest("hex") ||
      !Array.isArray(result.suggestions) || result.suggestions.length > Math.min(params.maxSuggestions ?? 3, 10) ||
      !Array.isArray(result.skipped) || result.skipped.length > 1000 || !result.metrics) return false;
  if (![result.metrics.inputTokens, result.metrics.outputTokens, result.metrics.modelCalls].every(integer) ||
      !Number.isFinite(result.metrics.elapsedMs) || result.metrics.elapsedMs < 0) return false;
  if (!result.skipped.every(item => item && text(item.symbolId, 1024) && text(item.reason, 1000))) return false;
  return result.suggestions.every(item => item && text(item.id, 256) && text(item.symbolId, 1024) &&
    text(item.symbolName, 1024) && text(item.commentText, 260) && text(item.oldText, 4096) && text(item.newText, 4096) &&
    text(item.explanation, 1000) && typeof item.codeHash === "string" && /^[a-f0-9]{64}$/.test(item.codeHash) &&
    item.range && point(item.range.start) && point(item.range.end) && Array.isArray(item.evidence) &&
    item.evidence.length <= 12 && item.evidence.every(evidence => text(evidence, 2048)));
}

/** Owns provider configuration and the disposable parser/inference worker. */
export class RuntimeManager implements vscode.Disposable {
  private state: RuntimeState = { ready: false, label: "Local model not prepared", provider: "local" };
  private readonly emitter = new vscode.EventEmitter<RuntimeState>();
  readonly onDidChangeState = this.emitter.event;
  private readyCheck: Promise<void>;
  private preparation?: Promise<void>;
  private worker?: WorkerClient;
  private workerStart?: Promise<WorkerClient>;
  private downloadAbort?: AbortController;
  private disposed = false;
  private model?: ModelManifest;
  private provider: ProviderConfig = { provider: "local" };
  private revision = 0;
  private configurationError?: string;

  constructor(private readonly context: vscode.ExtensionContext) {
    this.readyCheck = this.checkExisting();
  }

  getState(): RuntimeState { return { ...this.state, localModelName: this.model?.name, localModelSizeBytes: this.model?.sizeBytes }; }

  isProviderSecret(key: string): boolean {
    return this.provider.provider === "custom" && key === providerSecretKey(this.provider.endpoint);
  }

  private update(state: RuntimeState): void {
    if (this.disposed) return;
    this.state = { ...state, provider: this.provider.provider, providerLabel: providerLabel(this.provider) };
    this.emitter.fire(this.getState());
  }

  private async manifest(): Promise<ModelManifest> {
    if (!this.model) this.model = validateManifest(JSON.parse(await readFile(join(this.context.extensionPath, "config", "model.json"), "utf8")));
    return this.model;
  }

  private async checkExisting(): Promise<void> {
    const revision = ++this.revision;
    try {
      let provider: ProviderConfig;
      try { provider = await readProviderConfig(this.context, vscode); }
      catch (error) {
        if (revision === this.revision) {
          this.configurationError = (error as Error).message;
          this.update({ ready: false, label: "Provider configuration needs attention", error: this.configurationError });
        }
        return;
      }
      if (this.disposed || revision !== this.revision) return;
      this.configurationError = undefined;
      this.provider = provider;
      this.update({ ready: false, label: provider.provider === "local" ? "Local model not prepared" : "Checking custom API configuration" });
      await access(resolveWorkerExecutable(this.context.extensionPath));
      if (this.disposed || revision !== this.revision) return;
      if (provider.provider === "custom") {
        this.update({ ready: true, label: "Custom API configured · connection checked on analysis" });
        return;
      }
      const manifest = await this.manifest();
      const path = join(this.context.globalStorageUri.fsPath, "models", manifest.filename);
      const verified = await verifyModel(path, manifest);
      if (!this.disposed && revision === this.revision) {
        this.update(verified ? { ready: true, label: "Local model ready · untuned baseline", modelPath: path }
          : { ready: false, label: "Local model not prepared" });
      }
    } catch (error) {
      if (revision === this.revision) this.update({ ready: false, label: "Provider not ready", error: (error as Error).message });
    }
  }

  /** Changing a provider invalidates all old work and never falls back to another provider. */
  async refreshProvider(): Promise<void> {
    ++this.revision;
    this.preparation = undefined;
    this.downloadAbort?.abort();
    await this.unload();
    this.readyCheck = this.checkExisting();
    await this.readyCheck;
  }

  async ensureReady(): Promise<void> {
    if (this.disposed) throw new Error("The between-the-lines runtime has been disposed.");
    await this.readyCheck;
    if (this.configurationError) throw new Error(this.configurationError);
    if (this.state.ready) return;
    if (this.provider.provider === "custom") throw new Error(this.state.error ?? "Configure a custom endpoint and model first.");
    if (!this.preparation) {
      const pending = this.prepare().finally(() => { if (this.preparation === pending) this.preparation = undefined; });
      this.preparation = pending;
    }
    return this.preparation;
  }

  private async prepare(): Promise<void> {
    const revision = this.revision;
    try {
      try { await access(resolveWorkerExecutable(this.context.extensionPath)); }
      catch { throw new Error("The bundled worker is missing. Install the VSIX built for your operating system, or build the worker before running from source."); }
      const manifest = await this.manifest();
      const path = join(this.context.globalStorageUri.fsPath, "models", manifest.filename);
      if (revision !== this.revision || this.disposed) throw new Error("The model provider changed during setup.");
      const abort = new AbortController();
      this.downloadAbort = abort;
      await vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: `Preparing local model (${Math.round(manifest.sizeBytes / 1_000_000)} MB · untuned baseline)`,
        cancellable: true,
      }, async (progress, token) => {
        const subscription = token.onCancellationRequested(() => abort.abort());
        let previous = 0;
        try {
          if (revision !== this.revision || this.disposed) throw new Error("The model provider changed during setup.");
          this.update({ ready: false, label: "Preparing local model", progress: 0 });
          await downloadModel(manifest, path, {
            signal: abort.signal,
            onProgress: (received, total) => {
              if (revision !== this.revision) return;
              const percent = Math.floor(received * 100 / total);
              if (percent === previous && received !== total) return;
              progress.report({ increment: percent - previous, message: `${percent}%` });
              previous = percent;
              this.update({ ready: false, label: "Downloading local model", progress: percent });
            },
          });
        } finally { subscription.dispose(); }
      });
      if (revision !== this.revision) throw new Error("The model provider changed during setup.");
      await this.unload();
      this.update({ ready: true, label: "Local model ready · untuned baseline", modelPath: path });
    } catch (error) {
      const message = this.downloadAbort?.signal.aborted ? "Model preparation cancelled." : (error as Error).message;
      if (revision === this.revision) this.update({ ready: false, label: "Local model not prepared", error: message });
      throw new Error(message);
    } finally { if (revision === this.revision) this.downloadAbort = undefined; }
  }

  private async startWorker(): Promise<WorkerClient> {
    if (this.disposed) throw new Error("The between-the-lines runtime has been disposed.");
    if (this.worker?.isRunning) return this.worker;
    if (this.workerStart) return this.workerStart;
    const revision = this.revision;
    const provider = this.provider;
    this.workerStart = (async () => {
      const client = new WorkerClient(resolveWorkerExecutable(this.context.extensionPath));
      this.worker = client;
      const settings = vscode.workspace.getConfiguration("between-the-lines");
      const integer = (key: string, fallback: number, minimum: number, maximum: number): number => {
        const value = settings.get<number>(key, fallback);
        return Number.isFinite(value) ? Math.max(minimum, Math.min(maximum, Math.floor(value))) : fallback;
      };
      const configuration: WorkerConfiguration = {
        ...provider, modelPath: provider.provider === "local" ? this.state.modelPath ?? "" : "",
        threads: integer("inferenceThreads", 2, 1, 8), contextTokens: 2048,
        maxOutputTokens: 256, idleSeconds: integer("idleSeconds", 60, 1, 300),
      };
      try {
        const status = await client.request<WorkerStatus>("configure", configuration);
        if (revision !== this.revision || !status || status.protocolVersion !== 1 ||
            typeof status.modelLoaded !== "boolean" || status.provider !== provider.provider ||
            (provider.provider === "local" && status.modelPath !== configuration.modelPath)) {
          throw new Error("The worker configuration changed or uses an incompatible protocol.");
        }
        return client;
      } catch (error) {
        client.dispose();
        throw error;
      }
    })().finally(() => { if (revision === this.revision) this.workerStart = undefined; });
    return this.workerStart;
  }

  async analyze(params: AnalyzeParams, token?: vscode.CancellationToken): Promise<AnalysisResult> {
    await this.ensureReady();
    if (params.force === false && !this.canAnnotateAutomatically()) {
      throw new Error("Configure the provider to approve automatic use of captured tasks and code before analysis on save.");
    }
    const abort = new AbortController();
    if (token?.isCancellationRequested) abort.abort();
    const subscription = token?.onCancellationRequested(() => abort.abort());
    try {
      const worker = await this.startWorker();
      const result = await worker.request<AnalysisResult>("analyze", params, abort.signal);
      if (!validAnalysisResult(result, params)) {
        worker.dispose();
        throw new Error("The worker returned an invalid analysis result.");
      }
      return result;
    } finally { subscription?.dispose(); }
  }

  /** Observed edits use the chosen model; history and retirement remain model-free. */
  canAnnotateAutomatically(): boolean {
    return this.state.ready && hasAutomaticProviderConsent(this.context, this.provider, vscode);
  }

  async annotateObserved(params: ObservedAnalyzeParams, signal?: AbortSignal): Promise<AutomaticAnalysisResult> {
    await this.readyCheck;
    if (!this.canAnnotateAutomatically() || this.configurationError) throw new Error("Prepare a model and approve automatic provider use before annotation.");
    const worker = await this.startWorker();
    const result = await worker.request<AutomaticAnalysisResult>("annotateObserved", params, signal);
    return this.checkAutomatic(result, params);
  }

  async retireAnnotation(params: AnalyzeParams & { requirement: RequirementRecord }): Promise<AutomaticAnalysisResult> {
    const result = await this.core<AutomaticAnalysisResult>("retireAnnotation", params);
    return this.checkAutomatic(result, params);
  }

  private checkAutomatic(result: AutomaticAnalysisResult, params: AnalyzeParams): AutomaticAnalysisResult {
    if (!validAnalysisResult(result, params) || typeof result.annotatedSource !== "string" ||
        result.annotatedSource.length > params.source.length + 40960 ||
        result.annotatedSourceHash !== createHash("sha256").update(result.annotatedSource).digest("hex") ||
        !Array.isArray(result.requirements) || result.requirements.length > 64 || result.metrics.modelCalls > 2 * (params.maxSuggestions ?? 3)) {
      throw new Error("The worker returned an invalid automatic annotation batch.");
    }
    for (const record of result.requirements) {
      if (typeof record.quote !== "string" || record.quote.length > 2000 || typeof record.symbolId !== "string" ||
          typeof record.symbolName !== "string" || typeof record.commentText !== "string" ||
          record.promptHash !== createHash("sha256").update(params.intent?.prompt ?? "").digest("hex") ||
          !Number.isSafeInteger(record.quoteStart) || !Number.isSafeInteger(record.quoteEnd) ||
          params.intent?.prompt.slice(record.quoteStart!, record.quoteEnd!) !== record.quote) {
        throw new Error("An automatic annotation lost its original prompt evidence.");
      }
      if (record.commentKind !== "synthesized" || !record.generation ||
          !["local", "custom"].includes(record.generation.provider) || typeof record.generation.model !== "string" ||
          record.generation.model.length > 512 || !Array.isArray(record.evidence) || !record.evidence.length || record.evidence.length > 4 ||
          !record.evidence.every(item => typeof item.quote === "string" && item.quote.length <= 2000 &&
            Number.isSafeInteger(item.start) && Number.isSafeInteger(item.end) &&
            params.intent?.prompt.slice(item.start, item.end) === item.quote)) {
        throw new Error("A generated comment is missing its original evidence or model provenance.");
      }
    }
    return result;
  }

  async knownRequirements(inbox: string, paths: string[], workspaceRoot: string): Promise<RequirementRecord[]> {
    const result = await this.core<{ records: RequirementRecord[] }>("knownRequirements", { inbox, paths, workspaceRoot });
    if (!Array.isArray(result.records)) throw new Error("Invalid requirement history response.");
    if (result.records.length > 500) throw new Error("This file has more than 500 active requirements. Retire obsolete requirements before analyzing it.");
    return result.records;
  }

  async recordRequirements(inbox: string, records: RequirementRecord[]): Promise<void> {
    await this.core("recordRequirements", { inbox, records });
  }

  async retireRequirement(inbox: string, requirementId: string, workspaceRoot: string): Promise<void> {
    await this.core("retireRequirement", { inbox, requirementId, workspaceRoot });
  }

  private async core<T>(method: string, params: unknown): Promise<T> {
    await this.readyCheck;
    const worker = await this.startWorker();
    return worker.request<T>(method, params);
  }

  async getStatus(): Promise<WorkerStatus | undefined> {
    if (!this.worker?.isRunning) return undefined;
    return this.worker.request<WorkerStatus>("status");
  }

  async unload(): Promise<void> {
    this.worker?.dispose();
    this.worker = undefined;
    this.workerStart = undefined;
    if (this.state.ready) this.update({ ...this.state, label: this.provider.provider === "local" ? "Local model ready · unloaded" : "Custom API configured · worker stopped" });
  }

  dispose(): void {
    this.disposed = true;
    ++this.revision;
    this.downloadAbort?.abort();
    this.worker?.dispose();
    this.emitter.dispose();
  }
}
