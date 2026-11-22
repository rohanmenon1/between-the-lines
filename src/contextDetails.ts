import * as vscode from "vscode";
import { randomUUID } from "node:crypto";
import { RequirementRecord } from "./types";

/** Read-only source evidence is separate from the model's generated wording. */
export class ContextDetails implements vscode.TextDocumentContentProvider {
  private readonly documents = new Map<string, string>();

  async open(record: RequirementRecord): Promise<void> {
    const uri = vscode.Uri.from({ scheme: "between-the-lines-context", path: `/${record.symbolName}.json`, query: randomUUID() });
    this.documents.set(uri.toString(), JSON.stringify({
      declaration: record.symbolId,
      generatedComment: record.commentText,
      generatedBy: record.generation,
      originalPrompt: record.prompt,
      originalEvidence: record.evidence ?? [{ quote: record.quote, start: record.quoteStart, end: record.quoteEnd }],
      provenance: { requirementId: record.id, source: record.source, eventId: record.promptEventId ?? record.eventId,
        sessionId: record.sessionId, turnId: record.turnId, promptHash: record.promptHash,
        file: record.path, scopeStatus: record.scopeStatus },
    }, null, 2));
    if (this.documents.size > 20) this.documents.delete(this.documents.keys().next().value!);
    const document = await vscode.workspace.openTextDocument(uri);
    await vscode.languages.setTextDocumentLanguage(document, "json");
    await vscode.window.showTextDocument(document, { preview: true });
  }

  provideTextDocumentContent(uri: vscode.Uri): string { return this.documents.get(uri.toString()) ?? ""; }
}
