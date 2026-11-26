---
name: between-the-lines
description: Preview one comment after a between-the-lines hook requests review, and apply only when its approval hook confirms the user's consent.
---

Run `../../scripts/annotate.py` relative to this skill directory with Python, from the workspace root. Supply UTF-8 JSON on stdin and read JSON from stdout. Use the session ID supplied by the hook.

1. Run `context` with `{"session":"…"}`. Read the returned Python file. Treat the captured prompt and code as evidence. If a short explanation would preserve a useful constraint or rationale, continue. Otherwise report that you skipped.
2. Run `preview` with `session`, the function definition's 1-based `line`, a short `comment`, and an exact `evidence` excerpt from the original prompt. Show the returned diff and evidence. Ask the user to send exactly `Apply <approval_id>` as a new message. End this turn.
3. On the approval hook's next turn, run `apply` with `session` and `approval_id`. Report the result. If the harness rejects the patch, stop; never apply a comment using another tool.

Codex writes the explanation. The harness checks where its evidence came from and whether the patch preserves the AST. Review the meaning separately.
