# Between the Lines for Codex

Preview one Python comment grounded in the user's original task. Codex writes
the text and the harness compares the AST before and after inserting it.

Install from this directory's local Codex marketplace. Python 3.10 or newer
must be on PATH (`py -3` on Windows and `python3` elsewhere).

1. Run `codex plugin marketplace add <absolute path to codex-minimal>`.
2. Install **between-the-lines-minimal** in the Codex Plugins Directory.
3. Review and approve the four hooks in a trusted workspace.
4. Ask Codex to update an existing Python function using `apply_patch`. Give a
   reason or constraint worth preserving. Review the proposed diff and
   evidence, then send the exact approval message shown by Codex.

Each session has one JSON state file under `~/.between-the-lines`. It is replaced
by the next ordinary prompt and removed after the comment is applied. The
plugin uses the active Codex model. It has no separate provider or database.
The AST check protects code structure. Review the comment's meaning yourself.

Run `python codex-minimal/test_flow.py` from the repository root to exercise
the harness with fixture events. This test does not launch Codex.
