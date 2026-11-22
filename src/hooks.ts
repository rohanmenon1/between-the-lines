import * as vscode from "vscode";
import { access, lstat, mkdir, readFile, readdir, realpath } from "node:fs/promises";
import * as path from "node:path";
import { mergeHookConfiguration } from "./hookConfig";
import { resolveWorkerExecutable } from "./runtime";
import { Intent } from "./types";
import { sourceHash } from "./controller";

export interface CapturedIntent extends Intent { workspaceRoot: string; id: string; }

export async function listCapturedIntents(context: vscode.ExtensionContext, workspaceRoot: string): Promise<CapturedIntent[]> {
  const inbox = path.join(context.globalStorageUri.fsPath, "intents");
  const root = await realpath(workspaceRoot);
  const equalPath = (a: string, b: string): boolean => process.platform === "win32" ? a.toLowerCase() === b.toLowerCase() : a === b;
  let names: string[];
  try { names = await readdir(inbox); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return []; throw error; }
  const result: CapturedIntent[] = [];
  for (const name of names.slice(0, 500)) {
    if (!/^[a-f0-9-]{36}\.json$/.test(name)) continue;
    try {
      const file = path.join(inbox, name);
      const info = await lstat(file);
      if (!info.isFile() || info.isSymbolicLink() || info.size > 100000) continue;
      const item = JSON.parse(await readFile(file, "utf8")) as CapturedIntent;
      if (("kind" in item && (item as CapturedIntent & { kind?: string }).kind !== "prompt") || typeof item.prompt !== "string" || !item.prompt.trim() || item.prompt.length > 16000 ||
          !["codex", "copilot"].includes(item.source) || typeof item.id !== "string" ||
          item.id + ".json" !== name || typeof item.timestamp !== "string" || !Number.isFinite(Date.parse(item.timestamp)) ||
          typeof item.workspaceRoot !== "string" || !path.isAbsolute(item.workspaceRoot) ||
          (item.sessionId !== undefined && (typeof item.sessionId !== "string" || item.sessionId.length > 200))) continue;
      const capturedRoot = await realpath(item.workspaceRoot);
      const relative = path.relative(root, capturedRoot);
      // Coding tools may start inside a project subdirectory; still select explicitly.
      if (!equalPath(root, capturedRoot) && (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative))) continue;
      result.push(item);
    } catch { /* Ignore a malformed or concurrently removed capture. */ }
  }
  return result.sort((a, b) => Date.parse(b.timestamp!) - Date.parse(a.timestamp!)).slice(0, 100);
}

export function connectionKey(workspaceRoot: string): string {
  const root = path.resolve(workspaceRoot);
  return "connected:" + sourceHash(process.platform === "win32" ? root.toLowerCase() : root);
}

export async function installIntegration(context: vscode.ExtensionContext, kind: "codex" | "copilot"): Promise<void> {
  if (process.platform === "win32" && kind === "copilot") {
    throw new Error("Copilot prompt capture is unavailable in this Windows beta. Connect Codex or enter task context manually.");
  }
  if (!vscode.workspace.isTrusted || vscode.env.remoteName) throw new Error("Connect coding tools from a trusted local workspace.");
  const folders = vscode.workspace.workspaceFolders?.filter(folder => folder.uri.scheme === "file") ?? [];
  const folder = folders.length === 1 ? folders[0] : await vscode.window.showQuickPick(folders.map(value => ({ label: value.name, value })), { title: "Choose the workspace to connect" }).then(choice => choice?.value);
  if (!folder) return;
  const executable = resolveWorkerExecutable(context.extensionPath);
  await access(executable);
  const inbox = path.join(context.globalStorageUri.fsPath, "intents");
  const root = await realpath(folder.uri.fsPath);
  // Validate native Windows paths before creating or editing hook files.
  mergeHookConfiguration({}, kind, executable, inbox, root);
  await mkdir(inbox, { recursive: true });
  const relative = kind === "codex" ? ".codex/hooks.json" : ".github/hooks/between-the-lines.json";
  const uri = vscode.Uri.joinPath(folder.uri, relative);
  await vscode.workspace.fs.createDirectory(vscode.Uri.joinPath(folder.uri, path.posix.dirname(relative)));
  let document: vscode.TextDocument;
  try { document = await vscode.workspace.openTextDocument(uri); }
  catch {
    await vscode.workspace.fs.writeFile(uri, Buffer.from("{}\n"));
    document = await vscode.workspace.openTextDocument(uri);
  }
  if (document.isDirty) throw new Error("Save existing hook configuration edits before connecting.");
  const editor = await vscode.window.showTextDocument(document, { preview: false, preserveFocus: true });
  const current = document.getText();
  const updated = JSON.stringify(mergeHookConfiguration(JSON.parse(current.trim() || "{}"), kind, executable, inbox, root), null, 2) + "\n";
  const range = new vscode.Range(document.positionAt(0), document.positionAt(current.length));
  if (!(await editor.edit(builder => builder.replace(range, updated))) || document.getText() !== updated || !(await document.save())) {
    throw new Error("Hook configuration changed during setup. Review and save it before continuing.");
  }
  await context.workspaceState.update(connectionKey(root), {
    source: kind, configuredAt: new Date().toISOString(), extensionVersion: context.extension.packageJSON.version,
  });
  vscode.window.showInformationMessage(kind === "codex"
    ? "between-the-lines hooks installed. In Codex, use /hooks to review and trust them, then submit your task normally."
    : "Copilot prompt capture installed. Automatic edit association is available through the verified Codex integration.");
}
