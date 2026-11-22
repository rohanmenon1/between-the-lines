# between-the-lines 0.3.0

A Windows x64 VS Code extension that keeps explicit coding instructions beside Python and Java changes.

- Captures Codex prompts and verifies their actual edits by session, turn and tool identity.
- Adds attributed quotations after the agent finishes, with one undoable edit per file.
- Saves original requirements locally and supplies them to later Codex sessions.
- Preserves Python AST and Java syntax/tokens; skips ambiguous requirements and stale edits.
- Supports requirement retirement, explicit supersession and optional local or custom API analysis.

Install the attached Windows x64 VSIX with **Extensions: Install from VSIX**, open a trusted local project, then click **Connect Codex** and approve its hooks in Codex `/hooks`. Automatic quotations need no model download.

Verified with VS Code 1.143.0 and Codex CLI 0.164.0: 49 TypeScript tests passed, 115 Python tests passed, 15 installed editor checks passed, and a real Codex-to-editor run preserved and reinjected two requirements. Two platform-specific symlink tests were skipped.

Scope: explicit requirements naming declarations; Python and Java; local Windows workspaces. Arbitrary shell writes, renamed scopes and automatic Copilot edits are not supported. These checks preserve executable structure and source wording; they do not prove a requirement is implemented.
