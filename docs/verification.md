# Verification

## 0.4.2 Windows beta

The 0.4.2 Windows beta is described in [release notes](release-0.4.2.md). Its companion release manifest records current test totals, logs and SHA-256 hashes. Checks cover TypeScript, the Python worker, evaluation/packaging tools and shared plugin source. Windows symlink checks can be skipped when this host cannot create symlinks.

`artifacts/native-candidate-bf5f44cd/verification.json` records a separate native-launcher candidate that still carried 0.4.1 metadata. Its actual VSIX executable passed status/shutdown and eight native Windows hook invocations across UTF-8 and UTF-8 BOM fixtures. Each hook completed in 298–410 ms in that run. Capture identity, waiting for Stop, unchanged source, replay skipping and stale-disk rejection passed. This does not validate the failed earlier 0.4.1 artifact or identify the final 0.4.2 bytes. It uses controlled events, with no live Codex session, desktop interaction or model calls. Unicode, spaces, apostrophes and literal `&` were covered in paths. `%`, `!`, double quotes and control characters in Windows hook paths are rejected in this beta.

Windows Codex hooks now call the quoted, versioned bundled executable directly without PowerShell. Legacy or mismatched hooks show a reconnect message and require an explicit connection update. Windows Copilot capture is disabled before hook-file writes because its PowerShell capture remains unverified; enter task context manually for other coding tools.

Final deliverables belong under `artifacts/release-0.4.2/`: `between-the-lines-0.4.2-win32-x64.vsix`, `between-the-lines-0.4.2-source.zip`, `verification.json`, `release-0.4.2-manifest.json`, `SHA256SUMS.txt` and `README.md`. The report exercises the final executable through the native Windows command runner; it must match the VSIX hash. Publisher ownership remains unverified. No fresh full IDE test or new model inference was run for 0.4.2; the 19 installed-editor checks and all model measurements below belong to 0.4.0. No reduction in later agent regressions has been established.

## 0.4.1 archived candidate

The earlier 0.4.1 PowerShell-hook package stalled and is not deployable. Preserve its artifacts and diagnostics separately; the passing native-launcher development candidate does not retroactively validate it. See the [archived candidate notes](release-0.4.1.md).

## 0.4.0 status

**Installable experimental build, verified October 27, 2026.** This version generates readable comments from captured tasks and changed code. It replaces 0.3.0's quotation-only automatic path. The current package has passed the workflow checks below. Comment quality remains experimental; these results do not establish broad semantic accuracy.

Current evidence must identify the package, model checksum, settings and test date. Controlled API fixtures establish orchestration and failure handling; they are not measurements of a model's understanding.

| Area | Current evidence |
| --- | --- |
| Original history and hook capture | Lifecycle, identity, source preservation and failure cases pass in the current suites. |
| Complete Python and TypeScript suites | Python: 137 passed, one Windows symlink skip. TypeScript: 49 passed, one Windows symlink skip. |
| Frozen worker and matching VSIX | Worker build and protocol smoke passed; packaging verifies the worker source manifest. Final runtime bytes are compared with the tested install. |
| Installed 0.4.0 editor workflow | All 19 checks passed in VS Code 1.143.0, including actual local synthesis and saving. |
| Local model quality and resources | Installed positive case, development cases and failures recorded below. No held-out accuracy result. |
| Custom API | Loopback fixture passed through the frozen worker, including consent, cancellation and retry. No paid third-party service was called. |
| Live Copilot | Unverified; automatic Copilot edit tracking is not supported. |

The focused history/capture checks cover immutable originals; exact session/turn/tool association; contiguous completed edits; unsupported, overlapping and stale writes; durable publication recovery; original-evidence reinjection; explicit supersession and retirement; generated revisions; and provider failure followed by an unchanged-code pass without dropping prior requirements.

## Installed package result

The report is `artifacts/editor-test-c515212d92a742078a341d432682fcf9/report.json`. It records installation of `between-the-lines-0.4.0-win32-x64.vsix` into an isolated profile, not an Extension Development Host. The suite took 141.168 seconds. Unit logs are `artifacts/synthesis-node-tests.log` and `artifacts/synthesis-final-worker-tests.log`.

The actual local-model case used this captured request:

> Please fix the leaderboard output in this file. Ranking should put higher scores first, but when scores tie the people should keep the order they arrived in. Our screen currently jumps between refreshes, so that part matters. Use a stable sort and leave the caller's list alone.

The changed declaration returned `sorted(items, key=lambda item: item["score"], reverse=True)`. The installed extension generated and saved:

~~~python
# Use stable sort to preserve original order for tied scores, ensuring caller's list remains unchanged.
~~~

Generation and support review made two real local model calls: 1,295 input tokens, 133 output tokens, 78.55 seconds. The original 278-character prompt, exact evidence and SHA-256 were stored separately from the comment and model identity. The test also checked unchanged parsed code, native Undo, no replay, retirement, dirty and stale buffers, Auto Save, cancellation during inference, retained events during model setup, explicit retry after failure, read-only context inspection and manual previews.

The installed suite feeds controlled hook events and uses a loopback API fixture for transport/failure cases. Its local-model case uses real weights. It does not establish a new live Codex session result. The earlier live Codex 0.164.0 result remains attributed to 0.3.0.

## Selected local candidate

The manifest now selects **Qwen3.5 2B Q4_K_M** for text inference, without an app-specific fine-tune or LoRA. The Unsloth GGUF is pinned to revision `f6d5376be1edb4d416d56da11e5397a961aca8ae`, 1,280,835,840 bytes, SHA-256 `aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223`.

One full natural coding request about stable leaderboard ranking produced this model-written comment:

~~~python
# Use stable sort to preserve insertion order for tied scores, preventing screen refreshes from disrupting the ranking.
~~~

The comment was not a copied prompt. Exact source evidence was retained separately, and the same model accepted its support check. This development run preceded the verifier revision described below; it is not an accuracy estimate or proof that the current package handles other tasks. Raw prompt, source, responses and measurements are in `artifacts/synthesis-qwen35-2b-clarified.json`.

On Windows 11, an Intel Core Ultra 7 155H and about 16.5 GB physical RAM:

| Measurement | Observed value |
| --- | --- |
| Generation plus support review | 2 calls, 61.401 seconds |
| Input / output tokens | 1,222 / 100 |
| Peak process RSS | 1,770,000,384 bytes, about 1.65 GiB |
| Loaded process RSS | About 1.29 GiB |
| RSS after unloading | 23,293,952 bytes, about 22 MiB |

Settings were two CPU threads, 2,048 context tokens and 256 output tokens. The non-thinking ChatML path used temperature 0, top-k 0, top-p 1, min-p 0, repeat penalty 1 and seed 42. No GPU was used. These are source-level development-process measurements, excluding VS Code and OS file cache; they are not a memory ceiling. Energy was not measured.

Automatic work defaults to one changed block per file, with generation and review calls. The application setting `between-the-lines.automaticCommentsPerFile` can raise it to three. Expect more work and latency when increasing that limit.

### Broader cases and a false approval

The four-case run is preserved in `artifacts/synthesis-qwen35-2b-final-checks.json`:

| Case | Observed result |
| --- | --- |
| Bounded retry delay | Model accepted a comment retaining the 30-second cap and no initial wait. |
| Invoice half-cent rounding | Model accepted a comment about rounding ties away from zero and showing two decimals. |
| Withdrawal implementation allowing negative amounts | Rejected because it copied the task. This is not evidence that the implementation bug was detected. |
| CSV task paired with `parse_retry_count` | **Falsely approved** a CSV-column explanation for unrelated integer-parsing code. |

The two positive outputs are available for inspection, not human-reviewed benchmark labels. The unrelated-code failure shows why same-model approval cannot establish comment correctness.

The verifier was then changed to emit its supported decision before its explanation, align the reason limit with validation, and explicitly reject task/code irrelevance. Rechecking the same unrelated case produced `supported: false`; `artifacts/synthesis-qwen35-2b-verifier-check.json` preserves the actual verdict. That run used two calls, 59.572 seconds and 1,911,959,552 bytes peak RSS, about 1.78 GiB; RSS after unload was about 22 MiB.

This is a fix and recheck of a known failure, not independent validation or an accuracy score. The earlier false approval remains part of the evidence. The subsequent installed-package result above verifies one positive local-model workflow.

### Failures that informed this choice

The earlier 0.5B baseline produced no useful accepted outputs on six challenge cases: four abstentions and two bad comments approved by its own support review. Specific malformed patterns found there are now mechanically rejected; that does not make the semantic review reliable. See `artifacts/synthesis-challenge-0.5b.json`.

An installed 0.4.0 attempt with that earlier baseline passed mechanical/fixture checks, then failed its actual local-model case because it copied the task. Those results must not be presented as a successful model-driven release. The current 2B package subsequently passed its installed local-model case above. Development changes between trials prevent treating candidate counts as a clean model-accuracy comparison.

### Reproduce the source-level checks

Use a verified local weight file and a fresh output path:

~~~powershell
.\.venv\Scripts\python.exe scripts/evaluate_synthesis.py --model-path .\models\Qwen3.5-2B-Q4_K_M.gguf --output .\artifacts\synthesis-my-machine.json
~~~

The checked-in cases are in `eval/synthesis-cases.json`. They include natural prompts, negation, conditions and misleading cases. Their labels are review targets, not human-reviewed benchmark truth. The report records the model hash, hardware, settings, raw model responses, source checks and timing. Review usefulness, factual claims, omissions and abstentions yourself.

## Reproduce the editor checks and assess quality

Run `scripts/test_editor.ps1` with the packaged Windows x64 VSIX and an existing verified local model. The runner installs it into an isolated profile and checks automatic generation, separate evidence and provider identity, persisted originals, read-only context inspection, undo, retirement, dirty/stale source, Auto Save and provider failures. Its output belongs in `artifacts/editor-test-*/report.json`.

Exercise the local inference runtime separately from the controlled endpoint fixture. Inspect the actual comments, not just valid JSON or passing AST checks. Report useful accepted comments, false or unnecessary comments and abstentions together. Use natural prompts that do not name declarations and cases containing negation, conditions, conflicting requirements and misleading code.

Generation and support review use the same model. A supported verdict is fallible. A useful short comment may resemble a well-written task; the requirement is model synthesis with separate evidence, not forced lexical novelty. Failed generation must never be replaced by a copied task.

The pinned model has no app-specific fine-tune or LoRA. There is no established fine-tuning benefit, general accuracy guarantee, reduction in later agent regressions, energy saving or Copilot cost advantage. See the [evaluation plan](evaluation.md) for comparisons and labeling.

## Source protection and resources

For accepted batches, Python retains the complete AST, including docstrings. Java retains parsed syntax and all non-comment tokens; it is not compiled or type-checked. Disk hashes, exact ranges and editor versions guard stale writes. The worker does not execute project code.

These checks protect program structure, not comment truth or behavioral correctness. Historical context is attributed source data; it does not force another coding agent to obey it.

Report hardware, model hash, threads, context/output limits and cold versus warm runs beside latency, token totals, peak process memory and CPU time. Measure energy separately before making an energy claim. Model-file size is not peak runtime memory.

## Earlier releases

[0.3.0 notes](release-0.3.0.md) describe the earlier quotation-based release. Its real Codex 0.164.0 and VS Code 1.143.0 checks exercised hooks, native edits, persistence and fresh-session original-context retrieval. They did not test the model-written explanations now required.

Keep those artifacts labeled with their version. Final 0.4.0 evidence must come from the current worker and installed package; reused log names need their timestamp and version checked before citation.
