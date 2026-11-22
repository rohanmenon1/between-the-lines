import { createHash } from "node:crypto";
import { lstat, readFile, readdir, realpath } from "node:fs/promises";
import * as path from "node:path";

/** The original prompt and exact before/after bytes belong to one observed tool call. */
export interface ObservedFile {
  path: string;
  beforeSource: string;
  afterSource: string;
  beforeHash: string;
  afterHash: string;
  beforeRawHash?: string;
  afterRawHash?: string;
}

export interface ObservedEdit {
  kind: "edit";
  id: string;
  eventId: string;
  source: "codex" | "copilot";
  workspaceRoot: string;
  sessionId: string;
  turnId: string;
  prompt: string;
  timestamp: string;
  files: ObservedFile[];
}

export interface ObservedReadResult {
  edits: ObservedEdit[];
  skipped: { id: string; reason: string; status?: "noVerifiedEdit"; timestamp?: string }[];
}

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const HASH = /^[a-f0-9]{64}$/;
const MAX_EVENT_BYTES = 2 * 1024 * 1024;
const MAX_SOURCE_BYTES = 256 * 1024;
const MAX_FILES = 5;
const MAX_READ_EVENTS = 200;

const key = (value: string): string => process.platform === "win32" ? value.toLowerCase() : value;
const hash = (value: string | Buffer): string => createHash("sha256").update(value).digest("hex");
const text = (value: unknown, maximum: number): value is string => typeof value === "string" && value.length <= maximum;
const sourceText = (value: unknown): value is string => text(value, MAX_SOURCE_BYTES) &&
  Buffer.byteLength(value, "utf8") <= MAX_SOURCE_BYTES && Buffer.from(value, "utf8").toString("utf8") === value;

// The parser/editor source excludes a UTF-8 BOM; disk freshness includes it.
// Older notifications had only decoded hashes and remain valid for BOM-free files.
const validRawHash = (value: unknown, source: string, decodedHash: string): boolean => value === undefined ||
  (text(value, 64) && HASH.test(value) && (value === decodedHash || value === hash(`\uFEFF${source}`)));

function inside(root: string, file: string): boolean {
  const relative = path.relative(root, file);
  return relative !== "" && relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

/** Read completed edit events only. A captured prompt by itself is never an edit association. */
export async function readObservedEdits(
  inbox: string, workspaceRoots: readonly string[], processedIds: ReadonlySet<string> = new Set(),
): Promise<ObservedEdit[]> {
  return (await readObservedEditsDetailed(inbox, workspaceRoots, processedIds)).edits;
}

/** Stale events are reported so the caller can acknowledge them instead of replaying after an undo. */
export async function readObservedEditsDetailed(
  inbox: string, workspaceRoots: readonly string[], processedIds: ReadonlySet<string> = new Set(),
): Promise<ObservedReadResult> {
  const result: ObservedReadResult = { edits: [], skipped: [] };
  const roots = new Map<string, string>();
  for (const candidate of workspaceRoots) {
    try { const root = await realpath(candidate); roots.set(key(root), root); }
    catch { /* Closed or unavailable workspaces cannot receive annotations. */ }
  }
  if (!roots.size) return result;
  let names: string[];
  try { names = await readdir(inbox); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return result;
    throw error;
  }
  const candidates = names.filter(name => name.endsWith(".json") && UUID.test(name.slice(0, -5)) &&
    !processedIds.has(name.slice(0, -5))).sort().slice(0, MAX_READ_EVENTS);
  for (const name of candidates) {
    const id = name.slice(0, -5);
    try {
      const eventPath = path.join(inbox, name);
      const info = await lstat(eventPath);
      if (!info.isFile() || info.isSymbolicLink() || info.size > MAX_EVENT_BYTES) throw new Error("Invalid capture file.");
      const bytes = await readFile(eventPath);
      if (bytes.length > MAX_EVENT_BYTES) throw new Error("Capture size limit exceeded.");
      const event: unknown = JSON.parse(bytes.toString("utf8"));
      if (!event || typeof event !== "object") throw new Error("Invalid capture record.");
      const item = event as Record<string, unknown>;
      if (item.kind !== "edit") {
        const stop = item.kind === "stop" && item.status === "noVerifiedEdit" &&
          text(item.workspaceRoot, 4096) && path.isAbsolute(item.workspaceRoot) &&
          roots.has(key(await realpath(item.workspaceRoot))) && text(item.timestamp, 100) && Number.isFinite(Date.parse(item.timestamp));
        result.skipped.push(stop
          ? { id, reason: "Turn finished without a supported verified edit.", status: "noVerifiedEdit", timestamp: item.timestamp as string }
          : { id, reason: "Capture has no completed edit association." });
        continue;
      }
      if (item.id !== id || !text(item.eventId, 36) || !UUID.test(item.eventId) ||
          (item.source !== "codex" && item.source !== "copilot") ||
          !text(item.workspaceRoot, 4096) || !path.isAbsolute(item.workspaceRoot) ||
          !text(item.sessionId, 200) || !item.sessionId || !text(item.turnId, 200) || !item.turnId ||
          !text(item.prompt, 16000) || !item.prompt.trim() ||
          !text(item.timestamp, 100) || !Number.isFinite(Date.parse(item.timestamp)) ||
          !Array.isArray(item.files) || !item.files.length || item.files.length > MAX_FILES) {
        throw new Error("Capture is missing its prompt, turn, session or edit evidence.");
      }
      const capturedRoot = await realpath(item.workspaceRoot);
      const root = roots.get(key(capturedRoot));
      if (!root) throw new Error("Capture belongs to another workspace.");
      const files: ObservedFile[] = [];
      const seen = new Set<string>();
      let staleFiles = 0;
      for (const value of item.files) {
        if (!value || typeof value !== "object") throw new Error("Invalid changed-file record.");
        const file = value as Record<string, unknown>;
        if (!text(file.path, 4096) || !path.isAbsolute(file.path) || !/\.(py|java)$/i.test(file.path) ||
            !sourceText(file.beforeSource) || !sourceText(file.afterSource) ||
            !text(file.beforeHash, 64) || !HASH.test(file.beforeHash) ||
            !text(file.afterHash, 64) || !HASH.test(file.afterHash) ||
            hash(file.beforeSource) !== file.beforeHash || hash(file.afterSource) !== file.afterHash ||
            !validRawHash(file.beforeRawHash, file.beforeSource, file.beforeHash) ||
            !validRawHash(file.afterRawHash, file.afterSource, file.afterHash) ||
            !inside(root, path.resolve(file.path))) {
          throw new Error("Changed-file evidence is invalid or outside the workspace.");
        }
        if (file.beforeHash === file.afterHash) { staleFiles++; continue; }
        let canonical: string;
        try {
          const stat = await lstat(file.path);
          if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_SOURCE_BYTES) throw new Error();
          canonical = await realpath(file.path);
          if (!inside(root, canonical)) throw new Error("Changed file resolves outside the workspace.");
          const current = await readFile(canonical);
          if (current.length > MAX_SOURCE_BYTES || hash(current) !== (file.afterRawHash ?? file.afterHash)) { staleFiles++; continue; }
          if (key(await realpath(file.path)) !== key(canonical)) throw new Error("Changed file moved during capture validation.");
        } catch (error) {
          if (error instanceof Error && error.message.startsWith("Changed file")) throw error;
          staleFiles++;
          continue;
        }
        if (seen.has(key(canonical))) throw new Error("Capture contains the same changed file more than once.");
        seen.add(key(canonical));
        files.push({ path: canonical, beforeSource: file.beforeSource, afterSource: file.afterSource,
          beforeHash: file.beforeHash, afterHash: file.afterHash,
          beforeRawHash: (file.beforeRawHash ?? file.beforeHash) as string,
          afterRawHash: (file.afterRawHash ?? file.afterHash) as string });
      }
      if (!files.length) throw new Error(staleFiles ? "Captured files changed again or are no longer available." : "Capture contains no source edits.");
      result.edits.push({ kind: "edit", id, eventId: item.eventId, source: item.source, workspaceRoot: root,
        sessionId: item.sessionId, turnId: item.turnId, prompt: item.prompt, timestamp: item.timestamp, files });
    } catch (error) {
      const reason = error instanceof SyntaxError ? "Capture is not valid JSON." :
        error instanceof Error ? error.message : "Capture could not be validated.";
      result.skipped.push({ id, reason: reason.slice(0, 200) });
    }
  }
  result.edits.sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp) || a.id.localeCompare(b.id));
  return result;
}
