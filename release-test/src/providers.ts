import type * as VSCode from "vscode";
import { createHash } from "node:crypto";

export const CONFIGURATION = "between-the-lines";
export const AUTOMATIC_PROVIDER_APPROVAL = "provider.automaticApproval.v1";

export type ProviderConfig =
  | { provider: "local" }
  | { provider: "custom"; endpoint: string; model: string; apiKey: string };

/** Custom providers receive a full chat-completions URL, never guessed paths. */
export function validateEndpoint(value: string): string {
  let url: URL;
  try { url = new URL(value.trim()); }
  catch { throw new Error("Enter a complete chat-completions URL."); }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error("The endpoint must not contain credentials, query parameters, or a fragment.");
  }
  if (url.protocol !== "https:" && !(url.protocol === "http:" && isLoopback(url.hostname))) {
    throw new Error("Use HTTPS for a cloud endpoint. HTTP is allowed only for a loopback server.");
  }
  url.pathname = url.pathname.replace(/\/+$/, "") || "/";
  return url.toString();
}

export function isLoopback(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return host === "localhost" || host === "::1" || /^127(?:\.\d{1,3}){3}$/.test(host);
}

export function validateModel(value: string): string {
  const model = value.trim();
  if (!model || model.length > 256 || /[\x00-\x1f\x7f]/.test(model)) {
    throw new Error("Enter a model name with 1–256 characters and no control characters.");
  }
  return model;
}

export function providerSecretKey(endpoint: string): string {
  return `${CONFIGURATION}.provider.${createHash("sha256").update(validateEndpoint(endpoint)).digest("hex")}.apiKey`;
}

export function providerLabel(configuration: ProviderConfig): string {
  if (configuration.provider === "local") return "Bundled local model";
  const target = new URL(configuration.endpoint);
  const kind = isLoopback(target.hostname) ? "Local endpoint" : "Cloud API";
  return `${kind} · ${configuration.model} · ${target.host}`;
}

// Inference destinations are user settings. A repository cannot change them.
function userSetting<T>(settings: VSCode.WorkspaceConfiguration, key: string, fallback: T): T {
  const value = settings.inspect<T>(key);
  return value?.globalValue ?? value?.defaultValue ?? fallback;
}

/** Consent belongs to this installation and this exact destination/model, never synced settings. */
export function hasAutomaticProviderConsent(context: VSCode.ExtensionContext, configuration: ProviderConfig, host: typeof VSCode): boolean {
  if (configuration.provider === "local") return true;
  const settings = host.workspace.getConfiguration(CONFIGURATION);
  if (userSetting<boolean>(settings, "automaticCustomProvider", false) !== true ||
      userSetting<string>(settings, "provider", "local") !== "custom") return false;
  try {
    // Settings may change before the runtime's asynchronous refresh finishes.
    if (validateEndpoint(userSetting<string>(settings, "endpoint", "")) !== configuration.endpoint ||
        validateModel(userSetting<string>(settings, "model", "")) !== configuration.model) return false;
  } catch { return false; }
  const approved = context.globalState.get<{ endpoint?: unknown; model?: unknown }>(AUTOMATIC_PROVIDER_APPROVAL);
  return approved?.endpoint === configuration.endpoint && approved?.model === configuration.model;
}

export async function readProviderConfig(context: VSCode.ExtensionContext, host?: typeof VSCode): Promise<ProviderConfig> {
  const vscode = host ?? await import("vscode");
  const settings = vscode.workspace.getConfiguration(CONFIGURATION);
  const selected = userSetting<string>(settings, "provider", "local");
  if (selected === "local") return { provider: "local" };
  if (selected !== "custom") throw new Error("Choose the bundled local model or a custom provider.");
  const endpoint = validateEndpoint(userSetting<string>(settings, "endpoint", ""));
  const model = validateModel(userSetting<string>(settings, "model", ""));
  const apiKey = await context.secrets.get(providerSecretKey(endpoint)) ?? "";
  return { provider: "custom", endpoint, model, apiKey };
}

/** The key is entered in a native password prompt and stays out of webview state. */
export async function configureProvider(context: VSCode.ExtensionContext, host?: typeof VSCode): Promise<boolean> {
  const vscode = host ?? await import("vscode");
  const settings = vscode.workspace.getConfiguration(CONFIGURATION);
  const selection = await vscode.window.showQuickPick([
    { label: "Bundled local model", description: "Download once; inference stays on this computer", provider: "local" },
    { label: "Custom provider", description: "Your local server or cloud chat-completions API", provider: "custom" },
  ], { title: "between-the-lines: Inference provider", ignoreFocusOut: true });
  if (!selection) return false;
  if (selection.provider === "local") {
    await settings.update("provider", "local", vscode.ConfigurationTarget.Global);
    return true;
  }

  const endpointValue = await vscode.window.showInputBox({
    title: "between-the-lines: Chat-completions endpoint",
    prompt: "Enter the full endpoint URL, including /chat/completions when your provider requires it.",
    placeHolder: "https://your-provider.example/v1/chat/completions",
    value: userSetting<string>(settings, "endpoint", ""), ignoreFocusOut: true,
    validateInput: value => { try { validateEndpoint(value); return undefined; } catch (error) { return (error as Error).message; } },
  });
  if (endpointValue === undefined) return false;
  const endpoint = validateEndpoint(endpointValue);
  const modelValue = await vscode.window.showInputBox({
    title: "between-the-lines: Model",
    prompt: "Enter the model name accepted by this endpoint.",
    value: userSetting<string>(settings, "model", ""), ignoreFocusOut: true,
    validateInput: value => { try { validateModel(value); return undefined; } catch (error) { return (error as Error).message; } },
  });
  if (modelValue === undefined) return false;
  const model = validateModel(modelValue);
  const secretKey = providerSecretKey(endpoint);
  const existingKey = await context.secrets.get(secretKey);
  const keyChoices = [
    ...(existingKey ? [{ label: "Keep saved API key", action: "keep" }] : []),
    { label: "Set API key", action: "set" },
    { label: "Use without an API key", description: existingKey ? "Remove the saved key for this endpoint" : "For servers that do not require authentication", action: "clear" },
  ];
  const keyChoice = await vscode.window.showQuickPick(keyChoices, {
    title: "between-the-lines: API key", ignoreFocusOut: true,
  });
  if (!keyChoice) return false;
  let apiKey: string | undefined;
  if (keyChoice.action === "set") {
    apiKey = await vscode.window.showInputBox({
      title: "between-the-lines: API key", password: true, ignoreFocusOut: true,
      prompt: "Stored in VS Code secret storage and sent only to the configured endpoint.",
      validateInput: value => !value.trim() || value.length > 4096 || /[\x00-\x1f\x7f]/.test(value)
        ? "Enter a nonempty key without control characters." : undefined,
    });
    if (apiKey === undefined) return false;
    apiKey = apiKey.trim();
  }

  const target = new URL(endpoint);
  const local = isLoopback(target.hostname);
  const action = local ? "Use local endpoint" : "Use cloud API";
  const approval = await vscode.window.showInformationMessage(
    `Use ${model} at ${target.host}?`,
    { modal: true, detail: `With automatic comments enabled, between-the-lines will send each completed connected-agent turn's captured prompt and changed code declarations to ${endpoint}. Manual analysis also sends selected task context and related project files. ${local ? "This endpoint is on this computer." : "This data leaves this computer. API usage may incur charges from your provider."} Choosing this provider authorizes these model requests; pause automatic comments in the sidebar at any time. The bundled local model will not be used as a fallback.` },
    action,
  );
  if (approval !== action) return false;

  if (keyChoice.action === "set") await context.secrets.store(secretKey, apiKey!);
  if (keyChoice.action === "clear") await context.secrets.delete(secretKey);
  await settings.update("endpoint", endpoint, vscode.ConfigurationTarget.Global);
  await settings.update("model", model, vscode.ConfigurationTarget.Global);
  await context.globalState.update(AUTOMATIC_PROVIDER_APPROVAL, { endpoint, model });
  await settings.update("automaticCustomProvider", true, vscode.ConfigurationTarget.Global);
  await settings.update("provider", "custom", vscode.ConfigurationTarget.Global);
  return true;
}
