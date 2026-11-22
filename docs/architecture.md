# Architecture

The TypeScript extension owns VS Code, provider choice, hooks and undoable edits. A frozen Python worker owns parsing, model inference, evidence validation and local history. They exchange newline-delimited JSON over process pipes. The extension does not start an inference HTTP server.

## From a coding turn to a comment

1. **Capture the original task.** `UserPromptSubmit` stores the prompt with source, workspace, session and turn identity. Duplicate delivery is idempotent; different text cannot replace that turn's original.
2. **Establish the actual edit.** `PreToolUse` snapshots explicit supported targets and predicts the result of the structured input. `PostToolUse` checks the actual result against it. Missing identities, ambiguous patches and overlapping writers are rejected.
3. **Wait for completion.** `Stop` merges a contiguous first-before to last-after chain and checks current disk contents. A durable outbox protects the completed notification against interrupted publication.
4. **Check readiness and source.** The extension validates the event, workspace, paths, hashes and open buffers. It waits for a prepared model and, for custom automatic inference, explicit consent.
5. **Decompose the change.** Python or Java parsing identifies changed declarations. The model receives the complete captured task, exact current block, previous block when available and separately attributed active original contexts.
6. **Generate and review.** The selected model writes a short explanation with separate source evidence. Mechanical checks validate that evidence against the original prompt. The same model makes a second support judgment using the task, code and active originals.
7. **Protect the program.** The complete batch must preserve Python AST or Java syntax and non-comment tokens. The editor rechecks dirty state, disk bytes and version, then applies and saves one native undo group per file.
8. **Commit provenance.** Generated records are saved against the immutable original prompt after the source write succeeds. A pending marker allows recovery if the editor closes before that history commit finishes.

Automatic work defaults to one candidate per file, with generation and review calls. The application-level `automaticCommentsPerFile` setting can raise this to three candidates and six calls. Hooks observe at most five files per turn, 128 KiB per file and 16,000 prompt characters. They do not interpret arbitrary shell writes or borrow a global latest prompt.

Invalid source can be skipped without blocking a later valid file. Provider/runtime failures remain actionable and pending. Retry is latched rather than sent every polling interval; after fixing the provider, **Check Completed Agent Edits** retries the work. Successfully handled events are acknowledged, so native Undo does not replay the same event.

## Synthesis is separate from evidence

`synthesis.py` constructs model inputs and handles generation and support review. Tasks need not name functions. Code and prompts are data; automatic synthesis has no arbitrary file-reading or execution loop.

The generated comment is prose written for the changed block. Its evidence is exact original prompt text with UTF-16 ranges and a prompt hash. Evidence selection is bounded and source-checked; it does not turn the original prompt into the comment or establish that the model's interpretation is correct.

The second pass uses the same selected model. Both passes can share a mistake. Unsupported, mismatched or malformed output abstains; source checks cannot decide whether a comment is semantically true. An unavailable provider does not trigger another provider or a quotation-copy fallback.

A synthesized record keeps `commentKind: synthesized`, generated `commentText`, separate `evidence` and `generation` provider/model metadata. Existing quotation records remain readable for compatibility; new automatic comments use the model path.

Scoped declaration IDs and fingerprints associate records with source. Renames are not silently rebound. Decomposition targets functions, methods and classes, rather than arbitrary statement blocks.

## Inference and privacy

The selected default loads the pinned Qwen3.5 2B Q4_K_M GGUF in [config/model.json](../config/model.json) through bundled llama.cpp CPU libraries. It has no app-specific fine-tune or LoRA. The 1.28 GB weights download separately, load lazily and unload after 60 idle seconds. Defaults use two threads, a 2,048-token context and 256 output tokens. The backend uses the model's supported non-thinking ChatML path and deterministic decoding; it does not depend on hidden reasoning. Model quality and resource measurements are documented separately in [verification](verification.md).

Custom inference uses a non-streaming Chat Completions-compatible endpoint with JSON-object responses. HTTPS is required except on loopback. Redirects are rejected; requests have size and time limits. API keys use endpoint-scoped VS Code secret storage.

Automatic custom-provider consent explicitly covers captured prompts and changed code. Manual analysis can also send selected related files. Provider settings are application-level choices; a repository cannot silently redirect inference. An older endpoint configured before the automatic-use disclosure must be reconfigured.

Manual analysis shares the synthesis backend but returns suggestions for review. It may request bounded allowlisted context. Automatic generation has stronger observed-edit and support-review requirements because it inserts without a per-comment approval.

## Originals and annotation lifecycle

`intents/history.sqlite3` stores original prompts and requirement records in VS Code global storage. It is ordinary local SQLite, not a tamper-proof audit service. Original text is immutable through the application API; retirement and supersession change status without deleting it.

Recording checks workspace, source identity, original prompt hash, exact evidence, current declaration fingerprint, safe comment syntax and provider/model identity. It does not prove the comment's meaning. Repeated records are idempotent. Improved generated wording can create a new revision while earlier original requirements remain active.

Queries return the complete original prompt, its evidence and generated wording as separate fields. Scope is reported as `exact`, `changed` or `missing`. Later hooks reinject bounded original evidence only, labeled as quoted data with record IDs and scope. Generated prose is never promoted into user instructions. If an original does not fit the excerpt, the omission is reported.

**Review recorded context** opens a read-only view through `src/contextDetails.ts`. It displays the raw original prompt, evidence, generated comment, model and source provenance. The same review flow can retire a record. An exact unchanged owned comment is removed when possible; human edits are preserved.

Supersession requires `Supersede <requirement-id>` on its own line in a new task and new evidence for the same file/declaration. Retired and superseded records are excluded from reinjection, while their originals remain stored.

An observed removal can restore an exact prior annotation only for unchanged code and valid original evidence. A changed body requires fresh synthesis; a stale explanation is not copied back. Earlier manual removal is respected.

Notifications are capped at 100 files. Completed-event payloads remain in the outbox until publication succeeds; redundant tool snapshots are cleared after completion. Original prompts and requirements are not deleted by notification cleanup.

## Process and package boundary

Protocol version 1 uses `{id, method, params}` requests and bounded `result` or `error` responses. Stdout is JSON only; the maximum request line is 2 MiB.

| Method | Responsibility |
| --- | --- |
| `configure` | Select the provider and bounded inference settings. |
| `annotateObserved` | Generate and support-review a batch for an observed transaction. |
| `analyze` | Draft comments from selected context for manual review. |
| `retireAnnotation` | Propose removal of an exact owned annotation without inference. |
| `recordRequirements` | Revalidate and persist original evidence and generated explanations. |
| `knownRequirements`, `retireRequirement` | Query active scoped records or change their lifecycle status. |
| `status`, `unload`, `shutdown` | Inspect or release the worker. |

Results include source hashes, exact old/new text, exclusive ranges and call/token/time metrics. Positions use UTF-16; source hashes use UTF-8 SHA-256. The extension independently reconstructs the batch and rejects overlaps or stale versions.

The Windows x64 VSIX bundles Python, parsers, native CPU inference libraries and licenses. A source-hash manifest rejects a stale frozen worker. End users need no development environment. Windows Codex hooks launch the quoted bundled executable directly; legacy or mismatched configurations require an explicit reconnect. Windows Copilot capture is disabled because its PowerShell capture remains unverified; other tools can use manually entered task context. Other platforms, remote workspaces and universal coding-tool support are outside the verified package scope.
