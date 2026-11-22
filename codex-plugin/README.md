# between-the-lines for Codex

An experimental Windows x64 plugin that records a coding task, associates it with
verified Python or Java edits, and asks the active Codex model to write useful
comments. Original prompts and evidence remain separate from generated wording.

## Install the complete package

1. Extract `between-the-lines-codex-0.1.0-win32-x64.zip` into a folder you will keep.
2. Run `Install.cmd` from that outer folder. It prepares this bundle's absolute
   runtime paths and shared local data directory, then adds the folder as a local marketplace using your Codex CLI.
   If the CLI is unavailable, follow the printed manual command after installing
   a current CLI. Preparation itself does not change global Codex configuration.
3. Open the desktop app's Plugins Directory, select **between-the-lines**, and
   install the plugin. Restart the app if the marketplace has not appeared.
4. Open a trusted local project. Review and trust the four bundled hooks in
   Codex's `/hooks` screen. Installing the plugin alone does not trust them.
5. Give Codex a normal task. After supported edits, one bounded continuation asks
   Codex to inspect the task and code, preview a comment, then apply it or skip.

Use a current Codex desktop release with lifecycle-hook and local MCP support.
This package uses the compatibility manifest at `.codex-plugin/plugin.json`,
whose hook/MCP discovery was verified with Codex CLI 0.164.0. An active root
`plugin.json` prevented those declarations from loading in that client, so it is
not shipped. Source-only portable examples are retained for future client tests.
Legacy MCP launch paths are not expanded by that client. The one-time
`--prepare-install` step resolves the bundled executable path; keep the extracted
folder in place. After moving or replacing it, prepare it again and refresh or
reinstall the plugin so the installed hooks and MCP launch config stay together.
The package bundles Python and parsers. No Python installation, model download,
Ollama server or separate model API key is needed. Generation uses the selected
Codex model, its normal usage allowance and its inference provider. For OpenAI
models, that is cloud inference; this plugin is not an offline inference mode.

Save editor buffers before using it. This desktop integration checks disk
snapshots, not unsaved buffers or native VS Code document versions. Ask Codex to
undo a between-the-lines annotation through `undo_annotation`; native editor Undo
is not provided by this package. To withdraw a stored requirement, inspect its ID
and put `Retire <requirementId>` on its own line in a new task. Pause automatic
annotations by disabling the hooks or plugin.

Preparation records the local ledger directory in this bundle’s `.mcp.json`;
hooks and MCP both read that exact path. It defaults to
`%LOCALAPPDATA%\between-the-lines\codex-plugin`. To use another directory, set
`BETWEEN_THE_LINES_PLUGIN_DATA` before preparation, then prepare and reinstall
the plugin. Changing the host environment alone does not move the ledger. Do not run both automatic annotation integrations on
the same project at once.

## Distribution and scope

This is a manually distributed plugin, not a public directory listing. OpenAI's
current rules exclude hook-containing plugins from the public directory. Public
local-MCP submissions also require partner approval. See [packaging rules](https://developers.openai.com/plugins/build/plugins)
and [desktop-only submission](https://developers.openai.com/plugins/deploy/desktop-only).

Python and Java in local Windows x64 projects are the initial scope. Unsupported
shell writes, ambiguous associations, stale source and uncertain comments are
skipped. AST/token preservation protects executable structure, not comment truth.
The same Codex model can misunderstand both code and its own explanation.

For a preparation-only check, run `Install.cmd --prepare-only`; it skips marketplace
registration. Nothing is published by either installation command.
