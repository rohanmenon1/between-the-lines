import assert from "node:assert/strict";
import { test } from "node:test";
import type * as VSCode from "vscode";
import { configureProvider, hasAutomaticProviderConsent, providerLabel, providerSecretKey, readProviderConfig, validateEndpoint } from "../providers";

function fixture(options: { globals?: Record<string, unknown>; picks?: (string | undefined)[]; inputs?: (string | undefined)[]; approval?: string } = {}) {
  const globals = new Map<string, unknown>(Object.entries(options.globals ?? {}));
  const state = new Map<string, unknown>();
  const secrets = new Map<string, string>();
  const writes: string[] = [];
  const picks = [...(options.picks ?? [])];
  const inputs = [...(options.inputs ?? [])];
  const notices: { message: string; detail: string }[] = [];
  const host = {
    ConfigurationTarget: { Global: 1 },
    workspace: { getConfiguration: () => ({
      inspect: (key: string) => ({ key, globalValue: globals.get(key), workspaceValue: key === "provider" ? "custom" : "https://repository.example/chat/completions" }),
      update: async (key: string, value: unknown) => { writes.push(`setting:${key}`); globals.set(key, value); },
    }) },
    window: {
      showQuickPick: async (items: { label: string; action?: string; provider?: string }[]) => {
        const next = picks.shift();
        return items.find(item => next !== undefined && (item.action === next || item.provider === next));
      },
      showInputBox: async () => inputs.shift(),
      showInformationMessage: async (message: string, prompt: { detail: string }) => { notices.push({ message, detail: prompt.detail }); return options.approval; },
    },
  } as unknown as typeof VSCode;
  const context = { globalState: {
    get: (key: string) => state.get(key),
    update: async (key: string, value: unknown) => { writes.push("state:approval"); state.set(key, value); },
  }, secrets: {
    get: async (key: string) => secrets.get(key),
    store: async (key: string, value: string) => { writes.push("secret:store"); secrets.set(key, value); },
    delete: async (key: string) => { writes.push("secret:delete"); secrets.delete(key); },
  } } as unknown as VSCode.ExtensionContext;
  return { globals, state, secrets, writes, host, context, notices };
}

test("custom endpoints reject credential leakage and non-local HTTP", () => {
  for (const value of ["https://user:secret@example.com/v1/chat/completions", "https://example.com/v1?key=secret", "https://example.com/#secret", "http://example.com/v1", "file:///private", "not-a-url"]) {
    assert.throws(() => validateEndpoint(value));
  }
  assert.equal(validateEndpoint(" HTTPS://API.EXAMPLE:443/v1/chat/completions/ "), "https://api.example/v1/chat/completions");
  assert.equal(validateEndpoint("http://127.0.0.1:8080/v1/chat/completions"), "http://127.0.0.1:8080/v1/chat/completions");
  assert.equal(validateEndpoint("http://[::1]:8080/v1/chat/completions"), "http://[::1]:8080/v1/chat/completions");
});

test("provider credentials are scoped to the canonical endpoint", () => {
  assert.equal(providerSecretKey("https://API.EXAMPLE:443/v1/chat/completions/"), providerSecretKey("https://api.example/v1/chat/completions"));
  assert.notEqual(providerSecretKey("https://api.example/a/chat/completions"), providerSecretKey("https://api.example/b/chat/completions"));
  assert.notEqual(providerSecretKey("https://api.example/v1/chat/completions"), providerSecretKey("https://other.example/v1/chat/completions"));
});

test("workspace settings cannot select or redirect inference", async () => {
  const f = fixture();
  assert.deepEqual(await readProviderConfig(f.context, f.host), { provider: "local" });
  f.globals.set("provider", "custom");
  f.globals.set("endpoint", "https://approved.example/v1/chat/completions");
  f.globals.set("model", "chosen-model");
  f.secrets.set(providerSecretKey(f.globals.get("endpoint") as string), "saved-key");
  const result = await readProviderConfig(f.context, f.host);
  assert.equal(result.provider, "custom");
  if (result.provider !== "custom") throw new Error("Expected a configured custom provider");
  assert.equal(result.endpoint, "https://approved.example/v1/chat/completions");
  assert.equal(result.apiKey, "saved-key");
  assert.equal(providerLabel(result), "Cloud API · chosen-model · approved.example");
});

test("cancelling the disclosure preserves settings and credentials", async () => {
  const f = fixture({ picks: ["custom", "set"], inputs: ["https://api.example/v1/chat/completions", "chosen-model", "new-key"] });
  const key = providerSecretKey("https://api.example/v1/chat/completions");
  f.secrets.set(key, "previous-key");
  assert.equal(await configureProvider(f.context, f.host), false);
  assert.deepEqual(f.writes, []);
  assert.equal(f.secrets.get(key), "previous-key");
  assert.match(f.notices[0].detail, /data leaves this computer/);
});

test("cloud configuration writes credentials to secret storage only after consent", async () => {
  const f = fixture({ picks: ["custom", "set"], inputs: ["https://api.example/v1/chat/completions", "chosen-model", "new-key"], approval: "Use cloud API" });
  assert.equal(await configureProvider(f.context, f.host), true);
  assert.equal(f.globals.get("provider"), "custom");
  assert.equal(f.globals.get("automaticCustomProvider"), true);
  assert.equal(f.secrets.get(providerSecretKey("https://api.example/v1/chat/completions")), "new-key");
  assert.equal([...f.globals.values()].includes("new-key"), false);
  assert.equal(JSON.stringify([...f.state.values()]).includes("new-key"), false);
  assert.equal(JSON.stringify(f.notices).includes("new-key"), false);
  assert.match(f.notices[0].detail, /automatic comments enabled/);
  assert.match(f.notices[0].detail, /captured prompt and changed code declarations/);
  assert.match(f.notices[0].detail, /authorizes these model requests/);
  assert.equal(f.writes.at(-1), "setting:provider");
  assert.equal(hasAutomaticProviderConsent(f.context, await readProviderConfig(f.context, f.host), f.host), true);
});

test("synced or legacy automatic-use settings cannot authorize a custom destination", async () => {
  const f = fixture({ globals: {
    provider: "custom", endpoint: "https://api.example/v1/chat/completions", model: "chosen-model", automaticCustomProvider: true,
  } });
  assert.equal(hasAutomaticProviderConsent(f.context, await readProviderConfig(f.context, f.host), f.host), false);
  assert.equal(hasAutomaticProviderConsent(f.context, { provider: "local" }, f.host), true);
});

test("changing endpoint or model requires fresh destination-specific consent", async () => {
  const f = fixture({ picks: ["custom", "clear"], inputs: ["https://api.example/v1/chat/completions", "chosen-model"], approval: "Use cloud API" });
  assert.equal(await configureProvider(f.context, f.host), true);
  const approved = await readProviderConfig(f.context, f.host);
  assert.equal(hasAutomaticProviderConsent(f.context, approved, f.host), true);

  f.globals.set("endpoint", "https://other.example/v1/chat/completions");
  // Neither the stale worker configuration nor the new destination may run automatically.
  assert.equal(hasAutomaticProviderConsent(f.context, approved, f.host), false);
  assert.equal(hasAutomaticProviderConsent(f.context, await readProviderConfig(f.context, f.host), f.host), false);
  f.globals.set("endpoint", "HTTPS://API.EXAMPLE:443/v1/chat/completions/");
  assert.equal(hasAutomaticProviderConsent(f.context, await readProviderConfig(f.context, f.host), f.host), true);
  f.globals.set("model", "another-model");
  assert.equal(hasAutomaticProviderConsent(f.context, await readProviderConfig(f.context, f.host), f.host), false);
  f.globals.set("model", "chosen-model");
  f.globals.set("automaticCustomProvider", false);
  assert.equal(hasAutomaticProviderConsent(f.context, approved, f.host), false);
});

test("keeping and clearing an endpoint key are explicit separate actions", async () => {
  for (const action of ["keep", "clear"]) {
    const f = fixture({ picks: ["custom", action], inputs: ["http://localhost:8080/v1/chat/completions", "local-model"], approval: "Use local endpoint" });
    const key = providerSecretKey("http://localhost:8080/v1/chat/completions");
    f.secrets.set(key, "previous-key");
    assert.equal(await configureProvider(f.context, f.host), true);
    assert.equal(f.secrets.get(key), action === "keep" ? "previous-key" : undefined);
    assert.equal(f.writes.includes("secret:store"), false);
  }
});
