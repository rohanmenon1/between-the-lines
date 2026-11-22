import { ChildProcessWithoutNullStreams, spawn } from "node:child_process";

/** A valid worker rejection, distinct from a transport or process failure. */
export class WorkerResponseError extends Error {
  constructor(readonly code: string, message: string) { super(message); this.name = "WorkerResponseError"; }
}

interface Pending {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
  removeAbort?: () => void;
}

export interface WorkerClientOptions {
  timeoutMs?: number;
  maxLineBytes?: number;
  maxPending?: number;
  onDiagnostic?: (message: string) => void;
}

/** One disposable process and a strict, bounded newline-delimited JSON protocol. */
export class WorkerClient {
  private readonly child: ChildProcessWithoutNullStreams;
  private readonly pending = new Map<string, Pending>();
  private readonly timeoutMs: number;
  private readonly maxLineBytes: number;
  private readonly maxPending: number;
  private input = Buffer.alloc(0);
  private sequence = 0;
  private stopped = false;

  constructor(executable: string, args: string[] = [], options: WorkerClientOptions = {}) {
    this.timeoutMs = options.timeoutMs ?? 120_000;
    this.maxLineBytes = options.maxLineBytes ?? 2 * 1024 * 1024;
    this.maxPending = options.maxPending ?? 8;
    this.child = spawn(executable, args, { shell: false, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
    this.child.stdout.on("data", (chunk: Buffer) => this.receive(chunk));
    this.child.stderr.on("data", (chunk: Buffer) => options.onDiagnostic?.(chunk.toString("utf8").slice(0, 2048)));
    this.child.stdin.on("error", () => this.stop(new Error("Could not write to the local worker.")));
    this.child.once("error", error => this.stop(new Error(`Could not start the local worker: ${error.message}`)));
    this.child.once("exit", (code, signal) => this.stop(new Error(`Local worker exited (${signal ?? code ?? "unknown"}).`)));
  }

  get isRunning(): boolean { return !this.stopped; }

  request<T>(method: string, params: unknown = {}, signal?: AbortSignal): Promise<T> {
    if (this.stopped) return Promise.reject(new Error("The local worker is not running."));
    if (signal?.aborted) return Promise.reject(new Error("Local analysis cancelled."));
    if (this.pending.size >= this.maxPending) return Promise.reject(new Error("The local worker request queue is full."));
    const id = String(++this.sequence);
    const line = JSON.stringify({ id, method, params }) + "\n";
    if (Buffer.byteLength(line) > this.maxLineBytes) return Promise.reject(new Error("The source exceeds the local worker request limit."));
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => this.stop(new Error("Local analysis timed out. The worker was stopped.")), this.timeoutMs);
      const pending: Pending = { resolve: value => resolve(value as T), reject, timer };
      if (signal) {
        const abort = () => this.stop(new Error("Local analysis cancelled. The worker was stopped."));
        signal.addEventListener("abort", abort, { once: true });
        pending.removeAbort = () => signal.removeEventListener("abort", abort);
      }
      this.pending.set(id, pending);
      this.child.stdin.write(line, error => { if (error) this.stop(new Error("Could not write to the local worker.")); });
    });
  }

  private receive(chunk: Buffer): void {
    if (this.stopped) return;
    this.input = Buffer.concat([this.input, chunk]);
    let newline: number;
    while ((newline = this.input.indexOf(10)) !== -1) {
      if (newline > this.maxLineBytes) return this.stop(new Error("The local worker response exceeded its size limit."));
      const line = this.input.subarray(0, newline).toString("utf8");
      this.input = this.input.subarray(newline + 1);
      let message: { id?: unknown; result?: unknown; error?: unknown };
      try { message = JSON.parse(line); }
      catch { return this.stop(new Error("The local worker returned invalid JSON.")); }
      if (!message || typeof message !== "object" || typeof message.id !== "string" || !this.pending.has(message.id) ||
          (Object.hasOwn(message, "result") === Object.hasOwn(message, "error"))) {
        return this.stop(new Error("The local worker returned an invalid response."));
      }
      const pending = this.pending.get(message.id)!;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      pending.removeAbort?.();
      if (message.error !== undefined) {
        const error = message.error as { code?: unknown; message?: unknown } | null;
        if (!error || typeof error.code !== "string" || typeof error.message !== "string") {
          pending.reject(new Error("The local worker returned an invalid error."));
          return this.stop(new Error("The local worker returned an invalid error."));
        }
        pending.reject(new WorkerResponseError(error.code.slice(0, 100), error.message.slice(0, 500)));
      } else pending.resolve(message.result);
    }
    if (this.input.length > this.maxLineBytes) this.stop(new Error("The local worker response exceeded its size limit."));
  }

  private stop(error: Error): void {
    if (this.stopped) return;
    this.stopped = true;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.removeAbort?.();
      pending.reject(error);
    }
    this.pending.clear();
    this.input = Buffer.alloc(0);
    this.child.stdin.destroy();
    this.child.kill();
  }

  dispose(): void { this.stop(new Error("The local worker was stopped.")); }
}
