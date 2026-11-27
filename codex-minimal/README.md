# Between the Lines for Codex

Preview one Python comment grounded in the user's original task. Codex writes
the text and the harness compares the AST before and after inserting it.

Install from this directory's local Codex marketplace. Python 3.10 or newer
must be on PATH as `python` for MCP and as `py -3` on Windows or `python3` elsewhere for hooks.

Open Codex in the project’s Git checkout. The plugin advertises the read-only
`lookup_intent(path, symbol)` tool immediately, including in a fresh session.
For example, `lookup_intent("billing/retry.py", "RetryPolicy.retry_charge")`
returns the method's record and up to 32 direct calls. Only current entries
include rationale. Codex can choose this tool before editing. No repository
instructions or prior annotation turn are required.

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

Keep approved intent in `intent.json` at the repository root. For example:

```json
{
  "billing/retry.py": {
    "retry_charge": {
      "intent": "Retry temporary network failures only.",
      "ast_hash": "sha256:<fingerprint from context>"
    }
  }
}
```

After reviewing a function's intent, copy its path, symbol and fingerprint
from the line-specific `context` output into the map and commit it with Git.
Class methods use names such as `Billing.retry_charge`. The lookup supports
direct calls to module-level definitions and explicit absolute
`from ... import ...` bindings in root-layout Python repos. It verifies up to 32
distinct calls per preview. Stale intent is withheld, and changing the lookup
result after preview requires a new review. The plugin reads this map.

Run `python codex-minimal/test_flow.py` from the repository root to exercise
the harness with fixture events. This test does not launch Codex.
