# Codex plugin build

The plugin uses Codex's active model for comment generation. Its local runtime
contains capture, parsing, history, preview and checked file edits. It contains no
model weights, llama.cpp runtime or model API credentials.

## Workflow

1. `UserPromptSubmit` records the original task and reinjects relevant originals.
2. `PreToolUse` and `PostToolUse` establish exact before/after associations for
   supported structured edits. Shell commands are not interpreted as patches.
3. `Stop` creates bounded annotation jobs and requests one continuation. A hook
   continuation is not recorded as a new original user request.
4. Codex reads the immutable task and changed declaration through local MCP tools,
   writes an explanation with separate evidence, and previews it.
5. After checking semantic support, Codex asks the runtime to apply the preview.
   The runtime rechecks source, evidence and parsed structure, then records history.

The semantic decision remains model judgment. Python retains its complete AST;
Java retains parsed syntax and non-comment tokens. No compilation or behavioral
correctness proof is implied. The plugin cannot see unsaved editor buffers and
does not supply native VS Code undo. Its checked undo only removes an unchanged
owned annotation; original context stays recorded until explicitly retired by a
captured task containing `Retire <requirementId>` on its own line.

## Install and distribute

Build on Windows x64 with Python 3.13. This route does not need the extension's
local inference dependencies:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r plugin_runtime/requirements-build.txt
$env:PYTHONPATH = "plugin_runtime;worker"
.\.venv\Scripts\python.exe -m unittest discover -s plugin_runtime/tests -v
.\.venv\Scripts\python.exe scripts/package_codex_plugin.py
```

The output ZIP has `Install.cmd`, a `codex-plugin/` directory and a marketplace
catalog at `.agents/plugins/marketplace.json`. Extract the whole ZIP and run the
outer `Install.cmd`. It calls the bundled runtime's `--prepare-install` mode, which
records that package’s absolute executable and shared data directory in
`.mcp.json`, then adds
the folder as a local marketplace. Install from the desktop plugin browser and
review/trust the hooks. See the [installation guide](../codex-plugin/README.md).

The active entrypoint is `.codex-plugin/plugin.json` with explicit `.mcp.json`,
skills and hook paths. This format exposes four hooks, one MCP server and the
skill in Codex CLI 0.164.0 discovery. An active root portable `plugin.json` hid
the runtime declarations in that client. Its legacy MCP loader also leaves root
placeholders unexpanded; local preparation is required before registration.
`Install.cmd --prepare-only` performs this without global registration. Keep the
extracted folder in place and prepare/reinstall after moving or replacing it.
For a custom ledger, set `BETWEEN_THE_LINES_PLUGIN_DATA` before preparation; both
hooks and MCP use the path recorded in `.mcp.json`. Reprepare/reinstall to change it.
The source-only
`portable-manifest.example.json` and `mcp.json` are excluded from the ZIP until
their complete workflow is verified in a compatible client.

Codex has a shared public plugin directory and local/repo marketplaces. The
automatic version here uses hooks, so current public-directory rules require
manual distribution. Local-MCP public submissions are restricted to approved
partners. A Git-backed marketplace can distribute the same package after the owner
publishes it, including its compiled runtime. The source folder alone is not an
installable package. Creating the ZIP does not publish anything. [Packaging](https://developers.openai.com/plugins/build/plugins),
[desktop-only submissions](https://developers.openai.com/plugins/deploy/desktop-only).

## Source map

| Part | Purpose |
| --- | --- |
| `.codex-plugin/plugin.json`, `.mcp.json` | Verified plugin identity and explicit local runtime launch. |
| `Install.cmd`, `install.py` | Resolve the package's local launch paths before registration. |
| `portable-manifest.example.json`, `mcp.json` | Inactive source examples for later portable-format verification. |
| `hooks/`, `scripts/invoke-hook.ps1` | Lifecycle registration and exact stdin forwarding. |
| `skills/annotate/SKILL.md` | Model workflow, evidence requirements and abstention rules. |
| `plugin_runtime/between_the_lines_plugin/` | Local job lifecycle, checked edits and MCP protocol. |
| `worker/btl_agent/` | Reused parsing, original-history and comment validation. |
| `scripts/package_codex_plugin.py` | Frozen Windows runtime, license retention and verified ZIP. |

This route avoids an additional local inference process. It uses normal Codex
model requests and usage; the plugin does not gain access to an internal title
generation service or a free inference API. The local-model VS Code extension
remains a separate option for local inference.

See [verified results and limits](codex-plugin-verification.md) for the shipped
package's software checks and actual host-model example.
