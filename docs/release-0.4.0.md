# between-the-lines 0.4.0

**Installable experimental build.** The Windows package passed 19 installed-editor checks, including real local synthesis, and 186 unit tests, with two platform-specific skips. These checks verify the listed workflows; they do not establish general comment accuracy. See [verification](verification.md).

This version writes readable comments from a captured coding task and the Python or Java declarations that actually changed. It replaces 0.3.0's quotation-only automatic path.

- Uses the selected local model or explicitly approved custom Chat Completions-compatible endpoint for automatic comments.
- Keeps generated wording separate from exact prompt evidence and provider/model identity.
- Makes a second support judgment with the same model before automatic insertion.
- Checks Python AST or Java syntax/non-comment tokens, then rechecks source and editor versions before an undoable save.
- Preserves original context across sessions, revisions, retirement and explicit supersession.
- Adds a read-only original-prompt/evidence/comment view under **Review recorded context**.
- Waits for model readiness and pauses failed provider requests until explicit retry.
- Defaults to one changed block per file to limit local work or API calls; the application setting can raise this to three.

Install the Windows x64 VSIX through **Extensions: Install from VSIX**. Prepare the model first, then connect Codex and review/trust the hooks through `/hooks`. Users do not install Python or start Ollama.

Natural tasks do not need to name functions. Unsupported shell writes, ambiguous or overlapping edits, stale/dirty source and unsupported model output are skipped. A provider failure does not switch models or insert a copied task.

The selected default is Qwen3.5 2B Q4_K_M, a 1.28 GB weight download pinned in `config/model.json`, without an app-specific fine-tune or LoRA. The installed-editor example generated and saved an explanation in 78.55 seconds on two CPU threads. Separate development checks peaked at about 1.8 GiB process memory. Broader checks found a false approval for unrelated code. A verifier change rejected that same case on recheck, which is not an independent accuracy result. Local quality remains experimental. Structural source checks do not prove semantic correctness or prevent every later agent regression.

Scope: local Windows x64, Python and Java, with automatic observed edits through Codex. Copilot capture remains manual-only and live-client behavior is unverified. No public Marketplace listing or hosted inference deployment is implied by this package.

The prepared release files include the VSIX, source archive, checksums and verification reports. The installed test records an actual generated comment; a public demo clip has not been recorded. The owner will publish the package and announcement. No account upload has been performed.
