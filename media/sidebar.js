(() => {
  const vscode = acquireVsCodeApi();
  const byId = id => document.getElementById(id);
  const send = (action, fields = {}) => vscode.postMessage({ action, ...fields });
  let renderedPrompt;
  const controls = {
    setup: "setup", unload: "unload", analyze: "analyze", "select-intent": "selectIntent",
    "connect-codex": "connectCodex", "configure-provider": "configureProvider", "review-requirements": "reviewRequirements",
  };
  for (const [id, action] of Object.entries(controls)) byId(id).addEventListener("click", () => send(action));
  byId("save-intent").addEventListener("click", () => send("intent", { prompt: byId("intent").value }));
  byId("automatic").addEventListener("change", event => send("automatic", { enabled: event.target.checked }));
  byId("automatic-annotations").addEventListener("change", event => send("automaticAnnotations", { enabled: event.target.checked }));

  window.addEventListener("message", event => {
    if (event.data?.type !== "state") return;
    const state = event.data.state;
    const custom = state.provider === "custom";
    const automaticProviderReady = state.ready && (!custom || state.automaticCustomProvider);
    byId("connected").textContent = state.connected || (state.ready ? "Model ready. Connect Codex to begin." : "Prepare your model, then connect.");
    byId("automatic-annotations").checked = state.automaticAnnotations ?? true;
    byId("automatic-annotations").disabled = false; // Pause must remain available during inference.
    byId("annotation-status").textContent = custom && !state.automaticCustomProvider ?
      "Reconfigure this provider to allow automatic requests with captured prompts and changed code." :
      state.annotationStatus || (state.ready ? "Waiting for a completed Codex edit." : "Choose and prepare a model to start.");
    byId("connect-codex").disabled = !automaticProviderReady || state.busy;
    byId("provider").textContent = state.providerLabel || "Bundled local model";
    byId("provider-badge").textContent = custom ? "Custom" : "Local";
    byId("local-model-note").hidden = custom;
    const modelSize = state.localModelSizeBytes;
    const sizeLabel = modelSize ? (modelSize >= 1e9 ? `${(modelSize / 1e9).toFixed(2)} GB` : `${Math.round(modelSize / 1e6)} MB`) : "";
    byId("local-model-note").textContent = `${state.localModelName || "Local CPU model"}${sizeLabel ? ` · ${sizeLabel} download once` : ""}. Weights unload after inactivity.`;
    byId("custom-provider-note").hidden = !custom;
    byId("runtime").textContent = state.runtimeLabel;
    const progress = byId("progress");
    progress.hidden = state.progress === undefined;
    if (state.progress !== undefined) progress.value = state.progress;
    byId("setup").hidden = state.ready || custom;
    byId("setup").disabled = state.progress !== undefined;
    byId("configure-provider").disabled = state.busy || state.progress !== undefined;
    byId("unload").hidden = !state.ready || custom;
    byId("unload").disabled = state.busy;
    byId("file").textContent = state.file || "Open a Python or Java file.";
    const prompt = state.intent?.prompt || "";
    if (renderedPrompt !== prompt) { byId("intent").value = prompt; renderedPrompt = prompt; }
    byId("intent-source").textContent = state.intent ?
      `Context: ${state.intent.source}${state.intent.sessionId ? ` · session ${state.intent.sessionId.slice(0, 12)}` : ""}` : "No context selected";
    byId("automatic").checked = state.automatic;
    byId("automatic").disabled = !state.ready || !state.intent?.prompt.trim();
    byId("analyze").disabled = !state.ready || !state.intent?.prompt.trim() || state.busy;
    byId("analyze").textContent = state.busy ? "Analyzing…" : "Analyze current file";
    byId("message").textContent = state.message;
    byId("error").hidden = !state.error;
    byId("error").textContent = state.error || "";
    const metrics = byId("metrics");
    metrics.hidden = !state.metrics;
    if (state.metrics) metrics.textContent = `${state.metrics.modelCalls} model calls · ${state.metrics.inputTokens} input / ${state.metrics.outputTokens} output tokens · ${(state.metrics.elapsedMs / 1000).toFixed(1)}s · ${state.skipped} skipped`;
    const list = byId("suggestions");
    list.replaceChildren();
    for (const suggestion of state.suggestions) {
      const card = document.createElement("article"); card.className = "card suggestion";
      const name = document.createElement("h2"); name.textContent = suggestion.symbolName;
      const comment = document.createElement("pre"); comment.textContent = suggestion.commentText;
      const reason = document.createElement("p"); reason.textContent = suggestion.explanation;
      card.append(name, comment, reason);
      if (suggestion.evidence.length) {
        const evidence = document.createElement("p"); evidence.className = "muted";
        evidence.textContent = `Evidence: ${suggestion.evidence.join(" · ")}`; card.append(evidence);
      }
      const actions = document.createElement("div"); actions.className = "actions";
      for (const [action, label] of [["accept", "Accept"], ["preview", "View diff"], ["dismiss", "Dismiss"]]) {
        const button = document.createElement("button"); button.textContent = label;
        if (action !== "accept") button.className = "secondary";
        button.addEventListener("click", () => send(action, { uri: suggestion.uri, id: suggestion.id }));
        actions.append(button);
      }
      card.append(actions); list.append(card);
    }
  });
  send("ready");
})();
