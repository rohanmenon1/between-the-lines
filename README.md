# between-the-lines

The [build-small branch](https://github.com/rohanmenon1/between-the-lines/tree/build-small)
preserves the original Gradio hackathon submission. Try it on
[Hugging Face Spaces](https://huggingface.co/spaces/coolbeanz79/between-the-lines).

This branch contains the VS Code extension. The standalone Codex plugin is
packaged separately and has its own version and install instructions.

A VS Code extension that writes short comments from a coding agent's task and the Python or Java code it changed.

After a connected Codex turn finishes, the selected model reads the captured prompt and changed declarations. It writes a readable explanation, then makes a separate support judgment against the task and code. Accepted comments are saved as an undoable edit. The original prompt and evidence stay in local history, separate from the generated wording.

**0.4.2 is a Windows x64 beta.** It updates provider approval, original-context handling and the native Windows Codex launcher. Release verification and package hashes accompany the download; see [beta verification](#beta-verification). The unsuccessful 0.4.1 candidate must not be deployed. Generated comments still need review: the model has falsely approved an unrelated comment during development.

## Get started

1. Install the reviewed `between-the-lines-0.4.2-win32-x64.vsix` with **Extensions: Install from VSIX**.
2. Open a trusted local project and the **between-the-lines** sidebar.
3. Click **Download local model**, or **Use another model** to configure your own local server or cloud API.
4. Wait until the provider is ready, then click **Connect Codex**. Review the generated hooks and trust their commands in Codex's `/hooks` screen.
5. Give Codex a task normally. Once its turn ends, eligible comments are generated and saved. Natural task wording does not have to name a function.

Upgrading from 0.4.0 or an earlier candidate: click **Connect Codex** again and approve the updated hooks. Legacy or mismatched hook configurations display a reconnect message; they are not silently rewritten. If you use a custom provider, run **Configure Model Provider** again to approve this endpoint and model locally.

The package includes Python, parsers and the CPU inference runtime. Users do not install Python, a compiler or Ollama. Model weights download separately; the exact model, size and checksum are pinned in the packaged `config/model.json`.

**Automatically comment on connected-agent edits** pauses or resumes insertion. Captured edits wait while the selected model is unprepared; no download starts silently. After a provider failure, fix its configuration and run **between-the-lines: Check Completed Agent Edits** to retry.

## Local or custom inference

The selected default is Qwen3.5 2B Q4_K_M, a 1.28 GB weight download. It has no fine-tune or LoRA adapter specific to this extension. The extension's capture, history and local annotation inference stay on this computer. Your coding agent's own inference follows that tool's settings. Local comment quality remains experimental; there is no general accuracy claim.

Custom providers use a non-streaming Chat Completions-compatible endpoint with JSON-object responses. Setup discloses that automatic use sends captured prompts and changed declarations to that endpoint; manual analysis can also send selected context files. A cloud provider receives this data outside your computer and may charge for requests. Automatic approval is stored locally for the approved endpoint and model; changing either requires setup again. API keys use endpoint-scoped VS Code secret storage. HTTPS is required except for loopback servers.

Provider failures never switch to another model or copy the prompt into a comment as a fallback. Automatic requests pause after a failure instead of repeatedly calling the endpoint.

The default settings use two CPU threads, a 2,048-token context and a 256-token output limit. By default, one changed block per file is considered, using generation and review calls. `between-the-lines.automaticCommentsPerFile` can raise that limit to three, with more CPU time or API requests. Weights unload after 60 idle seconds; **Unload** stops the worker.

The 0.4.0 installed-editor example on an Intel Core Ultra 7 155H took 78.55 seconds for two model calls. Separate development checks peaked up to about 1.8 GiB process memory, falling to about 22 MiB after unload. This excludes VS Code and is not a memory ceiling or energy benchmark. Settings, raw outputs and failures are documented in `docs/verification.md` in the source archive.

## Inspect the source of a comment

Open a Python or Java file, click **Review recorded context**, choose a record, then **Inspect original prompt and generated comment**. A read-only view shows:

- The complete original prompt and exact evidence spans.
- The generated comment and its provider/model.
- Event, session and turn IDs, source hashes, file and declaration scope.

Generated wording is never promoted into the user's original instruction. Later hooks reinject bounded original evidence, with record IDs and scope status. Full original prompts remain in the ledger. Omitted records are reported; an excerpt does not supersede them.

Improving a comment does not silently erase older requirements. To replace one, include `Supersede <requirement-id>` on its own line in a new task, or retire it through **Review recorded context**. Retirement stops that record's reinjection and removes its exact unchanged owned comment when possible. Native Undo removes an inserted batch; the completed event is not replayed.

## What the checks protect

Prompt capture alone does not authorize a comment. Structured tool input, matching workspace/session/turn/tool IDs and a contiguous verified before/after chain must establish the edit. Comments are considered after the turn ends, rather than between the coding agent's patches.

The worker decomposes source into declarations. The model receives the changed block, its previous version when available, the captured task and active original context. It produces an explanation and separate prompt evidence. Evidence must match the original text. The same model then makes a second support judgment. There is no quotation-only fallback.

A model can still misunderstand scope, omit a condition or approve its own mistake. These checks do not prove comment truth, implementation correctness or that a later agent follows the context.

Before applying a batch, Python must retain its whole AST, including docstrings. Java must retain parsed syntax and every non-comment token; it is not compiled or type-checked. Disk hashes, exact old text, dirty buffers and editor versions are checked again. Human documentation and edited generated comments are preserved.

Supported hooks observe up to five files per completed turn and 128 KiB per file. Arbitrary shell writes, missing identity, ambiguous patches, overlapping writers and stale source are skipped. The package targets local Windows x64 workspaces. Remote SSH, containers, browser VS Code, macOS and Linux packages are not verified.

## Manual review and other tools

**Analyze current file** uses selected task context, recorded original requirements for that file and the same model to draft comments for review. **Analyze my saved changes** repeats that review workflow after saves. Copilot capture is disabled in this Windows beta because its PowerShell path remains unverified. The sidebar offers Codex connection; for other coding tools, enter task context manually and review suggestions.

Codex hook setup preserves other handlers in `.codex/hooks.json`. Windows hooks invoke the quoted, versioned bundled executable directly, without PowerShell. Hook files contain machine-specific paths; review them before committing and reconnect after an extension update. This beta rejects hook paths containing `%`, `!`, double quotes or control characters to avoid command expansion. Spaces, Unicode, apostrophes and literal `&` are supported.

The local ledger lives in `intents/history.sqlite3` under VS Code global storage. Prompt and requirement records survive the 100-file notification cap. Temporary tool snapshots are folded into a durable completed event; its outbox payload is released after publication. Disconnect hooks and close VS Code before deleting that storage to clear history.

## Privacy and license

Captured tasks, original requirements, source snapshots and generated comments are stored locally in the extension's global storage. Local annotation inference does not send them to a hosted model. Downloading the optional local weights contacts Hugging Face and its download hosts, which receive the normal network request metadata. The extension does not implement analytics telemetry.

An explicitly selected custom endpoint receives the captured prompt and changed code; manual analysis may also send selected context files. Its operator's data handling and pricing apply. The coding agent you connect has its own data handling. Endpoint-scoped API keys are stored in VS Code secret storage. To clear captured history, disconnect the hooks, close VS Code and remove the extension's global storage; uninstalling alone does not promise to erase it. Use **Configure Model Provider** to clear a stored API key.

The extension is distributed under the MIT license in the packaged `LICENSE`, including its warranty disclaimer. Native dependencies and model weights retain their own licenses; see the packaged `THIRD_PARTY_NOTICES.md`, worker license files and model manifest. A custom provider's service terms are separate.

## Beta verification

Automated checks cover native hook capture, completed-edit identity, source preservation, replay prevention, provider consent and packaging. The release manifest records suite totals and SHA-256 hashes. The packaged check exercises all four Codex lifecycle hooks for both UTF-8 and UTF-8 BOM files, through the native Windows command runner. It does not launch PowerShell, an editor or a model.

No fresh full IDE test, live Codex session or new model inference was performed for 0.4.2. The 19 installed-editor checks and local generation measurements above belong to 0.4.0. Companion release files are in `artifacts/release-0.4.2/`: `verification.json`, `release-0.4.2-manifest.json` and `SHA256SUMS.txt` identify the exact package. Source notes are in `docs/release-0.4.2.md` and `docs/verification.md`. Publisher ownership remains unverified.

## Code map

| Component | Why it exists |
| --- | --- |
| `src/extension.ts`, `src/view.ts`, `media/` | Commands, sidebar and workspace lifecycle. |
| `src/automaticController.ts`, `src/automation.ts` | Completed-edit validation, model readiness, retries and automatic coordination. |
| `src/suggestions.ts` | Previews, source/version checks, undoable edits and ownership. |
| `src/contextDetails.ts` | Read-only inspection of originals, evidence and generated wording. |
| `src/hooks.ts`, `src/hookConfig.ts` | Hook configuration, native Windows launch commands and reconnect checks. |
| `src/runtime.ts`, `src/workerClient.ts`, `src/types.ts` | Worker lifecycle and the process protocol. |
| `src/controller.ts` | Debounced manual analysis. |
| `src/providers.ts`, `src/download.ts`, `config/model.json` | Provider consent, secrets and verified weight downloads. |
| `worker/btl_agent/capture.py`, `history.py` | Verified tool edits, immutable originals and later reinjection. |
| `worker/btl_agent/synthesis.py`, `automatic.py`, `agent.py`, `grounding.py` | Model input, readable comments, support review and exact evidence checks. |
| `worker/btl_agent/parsing.py`, `patches.py` | Declaration discovery and source-preserving comment batches. |
| `worker/btl_agent/backend.py`, `providers.py` | Local inference and custom transport. |
| `worker/btl_agent/__main__.py`, `types.py` | Protocol dispatch and bounded data contracts. |
| `scripts/`, `worker/worker.spec` | Verification, measurement, freezing and packaging. |
| `scripts/evaluate_synthesis.py`, `eval/synthesis-cases.json` | Reproducible model outputs and challenge cases for human review. |
| `plugin_runtime/`, `codex-plugin/` | Codex host-model tools, immutable capture, hook continuation, previews and checked writes. |
| `scripts/package_codex_plugin.py`, `scripts/verify_codex_plugin.py` | Build the model-free native plugin and verify the actual packaged workflow. |

The source archive contains `docs/architecture.md`, `docs/verification.md`, `docs/demo.md` and `docs/evaluation.md`. This listing uses same-page links because a public source repository URL has not been configured.

## Develop on Windows

Use Node.js 22 and Python 3.13 x64:

~~~bat
npm ci
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: llama-cpp-python==0.3.36 --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r worker/requirements.txt pyinstaller==6.16.0
npm test
set PYTHONPATH=%CD%\worker
.\.venv\Scripts\python.exe -m unittest discover -s worker/tests -v
.\.venv\Scripts\python.exe scripts/build_worker.py
.\.venv\Scripts\python.exe scripts/package_extension.py
~~~

Packaging checks that the frozen worker matches its sources and refuses to replace an existing VSIX. Use `--output` with a new path for another candidate. Press **F5** after building for an Extension Development Host. Test the installed VSIX with `scripts/test_editor.ps1` and an existing verified model; it uses an isolated profile. Read the source archive's `docs/verification.md` before citing results.

A verified VSIX can be shared directly. A Marketplace listing requires a registered publisher; ownership of `between-the-lines-dev` has not been verified. The current package identifier is not evidence of a public listing. The owner upload checklist is `docs/marketplace-release.md` in the source archive.
