import * as vscode from "vscode";
import { randomBytes } from "node:crypto";
import { Intent, Metrics } from "./types";

export interface SidebarState {
  ready: boolean;
  runtimeLabel: string;
  localModelName?: string;
  localModelSizeBytes?: number;
  provider?: "local" | "custom";
  providerLabel?: string;
  progress?: number;
  file: string;
  intent?: Intent;
  automatic: boolean;
  automaticAnnotations?: boolean;
  automaticCustomProvider?: boolean;
  connected?: string;
  annotationStatus?: string;
  busy: boolean;
  message: string;
  error?: string;
  metrics?: Metrics;
  skipped: number;
  suggestions: { uri: string; id: string; symbolName: string; commentText: string; explanation: string; evidence: string[] }[];
}

export type SidebarAction =
  | { action: "ready" | "setup" | "analyze" | "unload" | "selectIntent" | "connectCodex" | "connectCopilot" | "configureProvider" | "reviewRequirements" }
  | { action: "intent"; prompt: string }
  | { action: "automatic" | "automaticAnnotations"; enabled: boolean }
  | { action: "accept" | "dismiss" | "preview"; uri: string; id: string };

export class Sidebar implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  constructor(private readonly extensionUri: vscode.Uri, private state: SidebarState,
    private readonly handleAction: (action: SidebarAction) => Promise<void>) {}

  update(state: SidebarState): void { this.state = state; void this.view?.webview.postMessage({ type: "state", state }); }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true, localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")] };
    view.webview.html = this.html(view.webview);
    view.webview.onDidReceiveMessage((message: unknown) => {
      if (validAction(message)) void this.handleAction(message);
    });
    view.onDidDispose(() => { if (this.view === view) this.view = undefined; });
  }

  private html(webview: vscode.Webview): string {
    const nonce = randomBytes(24).toString("base64");
    const css = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "sidebar.css"));
    const js = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "sidebar.js"));
    return `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';">
<link rel="stylesheet" href="${css}"><title>between-the-lines</title></head><body>
<header><span class="eyebrow">between-the-lines</span><h1>Keep the why.</h1>
<p>Write readable comments from coding prompts and changed code, with Python AST and Java syntax checks.</p></header>
<section class="card"><div class="section-heading"><h2>1. Choose a model</h2><span id="provider-badge" class="badge">Local</span></div>
<p id="provider" class="provider-name">Bundled local model</p><p id="runtime" role="status">Preparing status…</p>
<p id="local-model-note" class="muted">Model weights download once and stay on this computer. CPU inference unloads after inactivity.</p>
<p id="custom-provider-note" class="muted" hidden>Automatic comments send captured agent prompts and changed code to your chosen endpoint. Manual analysis also sends selected context files. A cloud API processes these inputs outside your computer.</p>
<progress id="progress" max="100" hidden></progress>
<div class="actions"><button id="setup">Download local model</button><button id="configure-provider" class="secondary">Use another model</button><button id="unload" class="secondary">Unload</button></div></section>
<section class="card automation-card"><div class="section-heading"><h2>2. Connect Codex</h2><span class="badge">Automatic</span></div>
<p id="connected" class="provider-name">Prepare your model, then connect.</p>
<p class="muted">After each completed turn, the model reads the captured prompt and actual code changes, then writes a short comment. Source evidence stays available for review; unclear cases are skipped.</p>
<label class="toggle"><input id="automatic-annotations" type="checkbox"> Automatically comment on connected-agent edits</label>
<p id="annotation-status" class="muted" role="status">Choose and prepare a model to start.</p>
<div class="actions"><button id="connect-codex">Connect Codex</button><button id="review-requirements" class="secondary">Review recorded context</button></div>
</section>
<section class="card"><h2>Manual review</h2><p class="muted">Optional: use the same model to draft comments for an open file. Provide task context or choose a captured prompt, then review each suggestion.</p>
<label for="intent">Task context</label><textarea id="intent" rows="4" maxlength="16000" placeholder="Keep equal-score items in their original order when ranking."></textarea>
<p id="intent-source" class="muted"></p><div class="actions"><button id="save-intent">Use this context</button><button id="select-intent" class="secondary">Select captured prompt</button></div>
<p class="muted">This Windows beta captures Codex tasks automatically. For other coding tools, enter task context above and review suggestions manually.</p></section>
<section class="card"><h2>Review comments</h2><p id="file" class="filename">Open a Python or Java file.</p>
<label class="toggle"><input id="automatic" type="checkbox"> Analyze my saved changes</label>
<p class="muted">Requires a configured provider and selected task context.</p><button id="analyze">Analyze current file</button>
<p id="message" role="status"></p><p id="error" class="error" role="alert" hidden></p><p id="metrics" class="muted" hidden></p></section>
<section id="suggestions" aria-label="Comment suggestions"></section>
<footer>Generated comments can be wrong. Review their recorded source context, and undo any automatic edit. AST checks preserve code structure; they do not verify behavior.</footer>
<script nonce="${nonce}" src="${js}"></script></body></html>`;
  }
}

function validAction(value: unknown): value is SidebarAction {
  if (!value || typeof value !== "object") return false;
  const data = value as Record<string, unknown>;
  if (typeof data.action !== "string") return false;
  if (["ready", "setup", "analyze", "unload", "selectIntent", "connectCodex", "connectCopilot", "configureProvider", "reviewRequirements"].includes(data.action)) return true;
  if (data.action === "intent") return typeof data.prompt === "string" && data.prompt.length <= 16000;
  if (data.action === "automatic" || data.action === "automaticAnnotations") return typeof data.enabled === "boolean";
  return ["accept", "dismiss", "preview"].includes(data.action) && typeof data.uri === "string" &&
    data.uri.length <= 8192 && typeof data.id === "string" && data.id.length <= 256;
}
