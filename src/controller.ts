import { createHash } from "node:crypto";

export interface SourceSnapshot { uri: string; version: number; source: string }

export function sourceHash(source: string): string {
  return createHash("sha256").update(source, "utf8").digest("hex");
}

export function isCurrent(snapshot: SourceSnapshot, current: SourceSnapshot, expectedHash: string): boolean {
  return snapshot.uri === current.uri && snapshot.version === current.version &&
    sourceHash(current.source) === expectedHash;
}

/** One inference at a time. New saves replace queued work for the same file. */
export class AnalysisQueue<T> {
  private pending = new Map<string, { value: T; due: number; generation: number }>();
  private generations = new Map<string, number>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private running = false;
  private closed = false;

  constructor(
    private readonly execute: (value: T, isLatest: () => boolean) => Promise<void>,
    private readonly onError: (error: unknown) => void,
  ) {}

  enqueue(uri: string, value: T, delayMs: number): void {
    if (this.closed) return;
    const generation = (this.generations.get(uri) ?? 0) + 1;
    this.generations.set(uri, generation);
    this.pending.set(uri, { value, due: Date.now() + Math.max(0, delayMs), generation });
    this.arm();
  }

  invalidate(uri: string): void {
    this.generations.set(uri, (this.generations.get(uri) ?? 0) + 1);
    this.pending.delete(uri);
    this.arm();
  }

  dispose(): void {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    this.pending.clear();
    this.generations.clear();
  }

  private arm(): void {
    if (this.timer) clearTimeout(this.timer);
    if (this.closed || this.running || !this.pending.size) return;
    const due = Math.min(...Array.from(this.pending.values(), item => item.due));
    this.timer = setTimeout(() => { void this.drain(); }, Math.max(0, due - Date.now()));
  }

  private async drain(): Promise<void> {
    if (this.closed || this.running) return;
    const entry = Array.from(this.pending).find(([, item]) => item.due <= Date.now());
    if (!entry) { this.arm(); return; }
    const [uri, item] = entry;
    this.pending.delete(uri);
    this.running = true;
    try {
      await this.execute(item.value, () => !this.closed && this.generations.get(uri) === item.generation);
    } catch (error) {
      if (!this.closed) this.onError(error);
    } finally {
      this.running = false;
      this.arm();
    }
  }
}
