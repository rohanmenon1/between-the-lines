# between-the-lines 0.4.2

**Windows x64 beta.** This version replaces the unsuccessful 0.4.1 candidate with a native Codex launcher. Companion verification and checksums identify the exact release package. Model-generated comments and later-agent benefits remain experimental. Publisher ownership has not been verified, and no upload has been performed.

Changes since the 0.4.0 workflow:

- Automatic custom-provider approval belongs to a specific endpoint and model. Changing settings cannot reuse approval for another destination.
- Capture distinguishes UTF-8 file bytes from decoded source so a byte-order mark does not make a valid edit look stale.
- Windows Codex hooks invoke the quoted, versioned bundled executable directly, without a PowerShell launcher. Legacy or mismatched configurations display a reconnect message instead of being silently rewritten.
- Windows Copilot capture is disabled before hook-file writes; its PowerShell capture remains unverified. The sidebar offers Codex connection and manual task context for other coding tools.
- Active original requirements are no longer silently cut off after 100 records. Context budgets still apply; oversized synthesis input is rejected rather than silently losing older instructions.
- Manual analysis includes recorded original requirements for the file.
- The normal package command checks the frozen worker's architecture and source hashes. A separate source packager excludes local state and creates a matching source manifest.

The default remains untuned Qwen3.5 2B Q4_K_M: a separate 1.28 GB download, two CPU threads and idle unloading. A compatible custom API can be selected explicitly. End users need no Python or Ollama installation.

## Verification

Release checks cover TypeScript, the Python worker, tooling and shared plugin source. The companion manifest records current suite totals, logs and package hashes. Test fixtures check orchestration and source preservation, not model understanding.

| Suite | Passed | Skipped |
| --- | ---: | ---: |
| TypeScript | 61 | 1 |
| Python worker | 141 | 1 |
| Evaluation and packaging tools | 43 | 0 |
| Shared Codex plugin source | 79 | 1 |

A separate native-launcher candidate still carrying 0.4.1 metadata passed eight controlled native hook invocations across UTF-8 and UTF-8 BOM fixtures, taking 298–410 ms each in that run. The preliminary report is `artifacts/native-candidate-bf5f44cd/verification.json`; it does not validate the failed earlier 0.4.1 artifact or identify the final 0.4.2 bytes. It verifies prompt and edit identity, waiting for Stop, unchanged source, processed-event skipping and stale-disk rejection, with zero model calls and no desktop interaction. Unicode and paths containing spaces, apostrophes and literal `&` were covered. Hook paths containing `%`, `!`, double quotes or control characters are rejected in this beta to avoid command expansion.

The final 0.4.2 package is identified by `artifacts/release-0.4.2/verification.json`, `release-0.4.2-manifest.json` and `SHA256SUMS.txt` in that directory. The 19 installed-editor checks and 78.55-second local generation example in [verification](verification.md) belong to 0.4.0. This release does not claim a new full IDE test, live Codex session, new model evaluation or measured reduction in later agent regressions. No desktop automation or new model inference was used to prepare it.

## First use

When upgrading from 0.4.0 or an earlier candidate, reconnect Codex and approve the updated native hook commands. Legacy or mismatched hooks require explicit reconnection. Custom-provider users also need to run **Configure Model Provider** again; the previous Boolean setting cannot supply the new destination-specific approval.

1. In VS Code, run **Extensions: Install from VSIX** and select the reviewed `between-the-lines-0.4.2-win32-x64.vsix`.
2. Open a disposable local project containing a Python or Java file, then open the **between-the-lines** sidebar.
3. Download the local model, or configure a compatible endpoint you want to use. Automatic API use requires the setup disclosure.
4. Click **Connect Codex**. Review and trust the generated commands in Codex's `/hooks` screen.
5. Ask Codex to make a small change normally. When its turn ends, check the annotation status, the resulting diff and **Review recorded context**. Confirm the original task and generated comment appear separately. Try native Undo.

An easy first task is a leaderboard function: ask for descending scores, stable ordering for ties and no mutation of the caller's list. A comment may be skipped; the tool must not invent an explanation when it cannot validate the output. This is a smoke check, not evidence of general accuracy.

If nothing happens, check model readiness, hook trust and the sidebar error before running **between-the-lines: Check Completed Agent Edits**. The sidebar's configured connection message does not prove a hook has fired. Arbitrary shell edits, ambiguous patches, dirty buffers and changes from another writer are skipped.

## Distribution

Upload files belong in `artifacts/release-0.4.2/`: `between-the-lines-0.4.2-win32-x64.vsix`, `between-the-lines-0.4.2-source.zip`, `verification.json`, `release-0.4.2-manifest.json`, `SHA256SUMS.txt` and `README.md`. The final package must match its own verification and checksums; keep prior artifacts intact. The archived 0.4.1 files are not release inputs.

Ownership of the configured publisher `between-the-lines-dev` has not been verified. The owner must confirm or replace it and rebuild before upload. See [the upload checklist](marketplace-release.md). Local preparation does not upload anything.
