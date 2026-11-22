import * as vscode from "vscode";
import { readFile } from "node:fs/promises";
import { isCurrent, SourceSnapshot, sourceHash } from "./controller";
import { AnalysisResult, AutomaticAnalysisResult, OwnedComment, Suggestion } from "./types";

export interface SuggestionBatch {
  snapshot: SourceSnapshot;
  result: AnalysisResult;
}

/** Proposals are tied to the exact document version that was analyzed. */
export class Suggestions implements vscode.TextDocumentContentProvider, vscode.Disposable {
  private readonly batches = new Map<string, SuggestionBatch>();
  private readonly previews = new Map<string, string>();
  private readonly changed = new vscode.EventEmitter<void>();
  readonly onDidChangeSuggestions = this.changed.event;

  constructor(private readonly context: vscode.ExtensionContext, private readonly previewScheme = "between-the-lines-preview") {}

  all(): SuggestionBatch[] { return Array.from(this.batches.values()); }

  replace(snapshot: SourceSnapshot, result: AnalysisResult): void {
    if (result.uri !== snapshot.uri || result.sourceHash !== sourceHash(snapshot.source)) {
      throw new Error("The worker returned a result for a different source snapshot.");
    }
    this.batches.set(snapshot.uri, { snapshot, result });
    this.changed.fire();
  }

  invalidate(uri: string): void {
    if (this.batches.delete(uri)) this.changed.fire();
  }

  clear(): void { this.batches.clear(); this.changed.fire(); }

  dismiss(uri: string, id: string): void {
    const batch = this.batches.get(uri);
    if (!batch) return;
    batch.result = { ...batch.result, suggestions: batch.result.suggestions.filter(item => item.id !== id) };
    this.changed.fire();
  }

  owned(uri: string): OwnedComment[] {
    return this.context.workspaceState.get<OwnedComment[]>(this.storageKey(uri), []);
  }

  async accept(uri: string, id: string): Promise<void> {
    const { batch, proposal } = this.find(uri, id);
    const document = await vscode.workspace.openTextDocument(vscode.Uri.parse(uri));
    const editor = await vscode.window.showTextDocument(document, { preview: false });
    this.checkCurrent(batch, document);
    const range = checkedRange(document, proposal);
    if (document.getText(range) !== proposal.oldText) throw new Error("The proposed comment no longer matches the file.");

    // TextEditor.edit records the document version and rejects intervening edits.
    const applied = await editor.edit(edit => edit.replace(range, proposal.newText), {
      undoStopBefore: true, undoStopAfter: true,
    });
    this.invalidate(uri);
    if (!applied) throw new Error("The file changed before the comment could be applied. Analyze it again.");

    const owned = this.owned(uri).filter(item => item.symbolId !== proposal.symbolId);
    owned.push({ symbolId: proposal.symbolId, commentText: proposal.commentText, codeHash: proposal.codeHash });
    await this.context.workspaceState.update(this.storageKey(uri), owned);
  }

  /** One version-checked edit and undo group for an observed, comment-only batch. */
  async applyAutomatic(captured: SourceSnapshot, result: AutomaticAnalysisResult): Promise<void> {
    const document = await vscode.workspace.openTextDocument(vscode.Uri.parse(captured.uri));
    if (document.isDirty) throw new Error("The file has unsaved edits; automatic annotations were skipped.");
    const batch = { snapshot: captured, result };
    this.checkCurrent(batch, document);
    const edits = result.suggestions.map(proposal => {
      const range = checkedRange(document, proposal);
      if (document.getText(range) !== proposal.oldText) throw new Error("An automatic comment no longer matches the file.");
      return { proposal, range, start: document.offsetAt(range.start), end: document.offsetAt(range.end) };
    }).sort((a, b) => b.start - a.start);
    let reconstructed = captured.source;
    let boundary = captured.source.length + 1;
    for (const edit of edits) {
      if (edit.end > boundary || (edit.start === boundary && edit.start === edit.end)) throw new Error("Automatic comment edits overlap.");
      reconstructed = reconstructed.slice(0, edit.start) + edit.proposal.newText + reconstructed.slice(edit.end);
      boundary = edit.start;
    }
    if (reconstructed !== result.annotatedSource || sourceHash(reconstructed) !== result.annotatedSourceHash) {
      throw new Error("The automatic batch contains an unexpected source edit.");
    }
    if (!edits.length) return;
    const editor = await vscode.window.showTextDocument(document, { preview: false, preserveFocus: true });
    this.checkCurrent(batch, document);
    const diskBefore = await readFile(document.uri.fsPath, "utf8");
    const bom = diskBefore.startsWith("\uFEFF") ? "\uFEFF" : "";
    if (document.isDirty || diskBefore !== bom + captured.source) throw new Error("The file changed before automatic annotation.");
    const applied = await editor.edit(builder => {
      for (const edit of edits) builder.replace(edit.range, edit.proposal.newText);
    }, { undoStopBefore: true, undoStopAfter: true });
    this.invalidate(captured.uri);
    if (!applied || document.getText() !== result.annotatedSource) throw new Error("The file changed while applying annotations; review the current buffer.");
    const replaced = new Set(result.suggestions.map(item => item.symbolId));
    const owned = this.owned(captured.uri).filter(item => !replaced.has(item.symbolId));
    for (const item of result.suggestions) if (item.commentText) {
      owned.push({ symbolId: item.symbolId, commentText: item.commentText, codeHash: item.codeHash });
    }
    await this.context.workspaceState.update(this.storageKey(captured.uri), owned);
    // Auto Save may have committed this exact edit while ownership was persisted.
    const disk = await readFile(document.uri.fsPath, "utf8");
    if (document.getText() !== result.annotatedSource || (disk !== bom + captured.source && disk !== bom + result.annotatedSource) ||
        !(await document.save()) || await readFile(document.uri.fsPath, "utf8") !== bom + result.annotatedSource) {
      throw new Error("The annotated file could not be saved unchanged; review the editor buffer.");
    }
  }

  async preview(uri: string, id: string): Promise<void> {
    const { batch, proposal } = this.find(uri, id);
    const document = await vscode.workspace.openTextDocument(vscode.Uri.parse(uri));
    this.checkCurrent(batch, document);
    const range = checkedRange(document, proposal);
    if (document.getText(range) !== proposal.oldText) throw new Error("The proposed comment no longer matches the file.");
    const before = document.getText();
    const start = document.offsetAt(range.start);
    const end = document.offsetAt(range.end);
    const after = before.slice(0, start) + proposal.newText + before.slice(end);
    this.previews.clear();
    const key = `${batch.result.sourceHash}-${proposal.id}`;
    const original = vscode.Uri.from({ scheme: this.previewScheme, path: `/original/${encodeURIComponent(document.fileName)}`, query: key });
    const proposed = vscode.Uri.from({ scheme: this.previewScheme, path: `/suggested/${encodeURIComponent(document.fileName)}`, query: key });
    this.previews.set(original.toString(), before);
    this.previews.set(proposed.toString(), after);
    await vscode.commands.executeCommand("vscode.diff", original, proposed, `between-the-lines: ${proposal.symbolName}`);
  }

  provideTextDocumentContent(uri: vscode.Uri): string { return this.previews.get(uri.toString()) ?? ""; }

  dispose(): void { this.changed.dispose(); this.batches.clear(); this.previews.clear(); }

  private find(uri: string, id: string): { batch: SuggestionBatch; proposal: Suggestion } {
    const batch = this.batches.get(uri);
    const proposal = batch?.result.suggestions.find(item => item.id === id);
    if (!batch || !proposal) throw new Error("This suggestion is no longer available. Analyze the file again.");
    return { batch, proposal };
  }

  private checkCurrent(batch: SuggestionBatch, document: vscode.TextDocument): void {
    if (!isCurrent(batch.snapshot, { uri: document.uri.toString(), version: document.version, source: document.getText() }, batch.result.sourceHash)) {
      this.invalidate(batch.snapshot.uri);
      throw new Error("The file changed after this suggestion was generated. Analyze it again.");
    }
  }

  private storageKey(uri: string): string { return `ownedComments:${sourceHash(uri)}`; }
}

function checkedRange(document: vscode.TextDocument, suggestion: Suggestion): vscode.Range {
  const { start, end } = suggestion.range;
  for (const position of [start, end]) {
    if (!Number.isInteger(position.line) || !Number.isInteger(position.character) || position.line < 0 ||
      position.line >= document.lineCount || position.character < 0 || position.character > document.lineAt(position.line).text.length) {
      throw new Error("The worker returned an invalid comment range.");
    }
  }
  if (end.line < start.line || (end.line === start.line && end.character < start.character)) {
    throw new Error("The worker returned an invalid comment range.");
  }
  return new vscode.Range(start.line, start.character, end.line, end.character);
}
