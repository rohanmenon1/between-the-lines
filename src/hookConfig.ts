/** Provider-specific schemas kept separate from VS Code so merging is testable. */
export function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'"'"'`)}'`;
}

export function powershellQuote(value: string): string {
  return `'${value.replace(/'/g, "''")}'`;
}

/** Quote one argument using Windows' native backslash/quote rules. */
export function windowsArgument(value: string): string {
  return '"' + value.replace(/(\\*)"/g, (_match, slashes: string) => slashes + slashes + '\\"')
    .replace(/(\\+)$/g, "$1$1") + '"';
}

function windowsHook(executable: string, args: string[]): string {
  const values = [executable, ...args];
  // Codex runs commandWindows through cmd.exe /C. Quoting protects spaces and
  // metacharacters, but cannot stop percent or delayed exclamation expansion.
  // This Windows beta rejects those uncommon paths rather than rewriting them.
  if (values.some(value => /[%!"\u0000-\u001f\u007f]/.test(value))) {
    throw new Error("Windows hook paths cannot contain %, !, double quotes, or control characters in this beta.");
  }
  return values.map(windowsArgument).join(" ");
}

export interface HookConnection { source: "codex" | "copilot"; extensionVersion?: string; }

export function connectionStatus(connection: HookConnection | undefined, extensionVersion: string): string | undefined {
  if (!connection) return undefined;
  if (connection.extensionVersion !== extensionVersion) {
    return `Reconnect ${connection.source === "codex" ? "Codex" : "Copilot"} after extension update.`;
  }
  return connection.source === "codex"
    ? "Codex hooks configured · approve them in Codex /hooks."
    : "Copilot prompt capture configured · automatic edits require Codex.";
}

type JsonObject = Record<string, unknown>;
function object(value: unknown): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Hook configuration must contain a JSON object.");
  return value as JsonObject;
}

export function mergeHookConfiguration(existing: unknown, kind: "codex" | "copilot", executable: string, inbox: string,
  workspaceRoot?: string): JsonObject {
  const config = { ...object(existing) };
  const hooks = config.hooks === undefined ? {} : { ...object(config.hooks) };
  // Match only our two capture flags. Preserve all other handlers and events.
  const ours = (value: unknown): boolean => {
    if (!value || typeof value !== "object") return false;
    const item = value as JsonObject;
    return [item.command, item.commandWindows, item.bash, item.powershell].some(command => typeof command === "string" && command.includes("--capture-hook") && command.includes("--inbox"));
  };
  const names = kind === "codex" ? ["UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"] : ["userPromptSubmitted"];
  for (const name of names) {
    const entries = hooks[name] ?? [];
    if (!Array.isArray(entries)) throw new Error(`Existing ${name} hooks must be an array.`);
    const preserved = entries.flatMap(value => {
      if (kind === "copilot") return ours(value) ? [] : [value];
      const entry = object(value);
      if (!Array.isArray(entry.hooks)) throw new Error("Existing Codex event entries must contain a hooks array.");
      const handlers = entry.hooks.filter(handler => !ours(handler));
      return handlers.length || entry.hooks.length === 0 ? [{ ...entry, hooks: handlers }] : [];
    });
    const args = ["--capture-hook", "--source", kind, "--inbox", inbox, "--event", kind === "codex" ? name : "UserPromptSubmit"];
    if (workspaceRoot) args.push("--workspace", workspaceRoot);
    const bash = [executable, ...args].map(shellQuote).join(" ");
    hooks[name] = [...preserved, kind === "codex" ? {
      hooks: [{ type: "command", command: bash, commandWindows: windowsHook(executable, args), timeout: 10 }],
    } : { type: "command", bash, powershell: "& " + [executable, ...args].map(powershellQuote).join(" "), timeoutSec: 10 }];
  }
  if (kind === "copilot") {
    if (config.version !== undefined && config.version !== 1) throw new Error("Unsupported Copilot hooks version.");
    config.version = 1;
  }
  return { ...config, hooks };
}

