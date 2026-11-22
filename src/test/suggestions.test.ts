import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { test } from "node:test";
import type { ExtensionContext } from "vscode";
import { sourceHash } from "../controller";
import type { AutomaticAnalysisResult, Position } from "../types";

// Exercise the real edit/save coordinator against an in-memory editor adapter;
// no VS Code process or desktop window is opened.
const loader = require("node:module") as { _load: (name: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
let adapter: { document: unknown; editor: unknown };
class Range {
  start: Position;
  end: Position;
  constructor(startLine: number, startCharacter: number, endLine: number, endCharacter: number) {
    this.start = { line: startLine, character: startCharacter };
    this.end = { line: endLine, character: endCharacter };
  }
}
loader._load = function(name, ...args) {
  if (name === "vscode") return {
    Range,
    EventEmitter: class { event = () => ({ dispose() {} }); fire() {} dispose() {} },
    Uri: { parse: (value: string) => ({ fsPath: fileURLToPath(value), toString: () => value }) },
    workspace: { openTextDocument: async () => adapter.document },
    window: { showTextDocument: async () => adapter.editor },
  };
  return originalLoad.call(this, name, ...args);
};
const { Suggestions } = require("../suggestions") as typeof import("../suggestions");
loader._load = originalLoad;

test("automatic edit and save preserve the file's original UTF-8 BOM", async () => {
  for (const bom of ["", "\uFEFF"]) {
    const directory = await mkdtemp(join(tmpdir(), "between-the-lines-bom-save-"));
    const file = join(directory, "rank.py");
    const uri = pathToFileURL(file).toString();
    const source = "def rank(items):\n    return sorted(items)\n";
    const comment = "# Sort entries while preserving their tie order.\n";
    const annotatedSource = comment + source;
    const state = new Map<string, unknown>();
    const undoOptions: unknown[] = [];
    const document = {
      uri: { fsPath: file, toString: () => uri }, version: 1, isDirty: false, text: source,
      get lineCount() { return this.text.split("\n").length; },
      lineAt(line: number) { return { text: this.text.split("\n")[line] }; },
      offsetAt(position: Position) { return this.text.split("\n").slice(0, position.line).reduce((sum, line) => sum + line.length + 1, 0) + position.character; },
      getText(range?: Range) { return range ? this.text.slice(this.offsetAt(range.start), this.offsetAt(range.end)) : this.text; },
      async save() { await writeFile(file, bom + this.text); this.isDirty = false; return true; },
    };
    const editor = {
      async edit(callback: (builder: { replace(range: Range, text: string): void }) => void, options: unknown) {
        undoOptions.push(options);
        callback({ replace(range, text) {
          const start = document.offsetAt(range.start), end = document.offsetAt(range.end);
          document.text = document.text.slice(0, start) + text + document.text.slice(end);
        } });
        document.version++; document.isDirty = true; return true;
      },
    };
    adapter = { document, editor };
    const context = { workspaceState: {
      get: (key: string, fallback: unknown) => state.get(key) ?? fallback,
      update: async (key: string, value: unknown) => { state.set(key, value); },
    } } as unknown as ExtensionContext;
    const suggestions = new Suggestions(context);
    const result: AutomaticAnalysisResult = {
      uri, sourceHash: sourceHash(source), annotatedSource, annotatedSourceHash: sourceHash(annotatedSource), requirements: [], skipped: [],
      metrics: { inputTokens: 0, outputTokens: 0, modelCalls: 0, elapsedMs: 0 },
      suggestions: [{ id: "comment", symbolId: "rank", symbolName: "rank", codeHash: "a".repeat(64),
        range: { start: { line: 0, character: 0 }, end: { line: 0, character: 0 } },
        oldText: "", newText: comment, commentText: comment.trim(), explanation: "Task context", evidence: [] }],
    };
    try {
      await writeFile(file, bom + source);
      await suggestions.applyAutomatic({ uri, version: 1, source }, result);
      assert.equal(await readFile(file, "utf8"), bom + annotatedSource);
      assert.equal(document.text, annotatedSource);
      assert.deepEqual(undoOptions, [{ undoStopBefore: true, undoStopAfter: true }]);
    } finally { suggestions.dispose(); await rm(directory, { recursive: true, force: true }); }
  }
});
