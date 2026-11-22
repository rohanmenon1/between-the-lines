# Codex plugin verification

Version 0.1.0 is an experimental Windows x64 release, checked on October 29, 2026.
The shipped ZIP is 9,610,306 bytes. Its SHA256 is
`fb2c25657df597285f5435f377d8c558ab402ceaaed7943096cfcc72c92a81fb`.

## What passed

- 79 software tests passed; one Windows symlink test was skipped. These cover
  original-task capture, ownership, stale edits, interrupted writes, checked undo,
  protocol boundaries, installation and source-package exclusions.
- The extracted package passed 12 native workflow checks, including Windows
  PowerShell 5.1, Unicode, a folder with spaces, conflicting parent storage
  settings, original-context reinjection and AST preservation.
- An isolated Codex CLI 0.164.0 turn loaded the four hooks, skill and eight local
  MCP tools. Its reported active model was `gpt-6.1-sol`. It changed a Python
  function, automatically continued through the annotation workflow, generated a
  comment, previewed it and applied it. The complete original prompt was retained
  separately, and inserting the comment left the implementation's AST unchanged.

The task asked for descending leaderboard scores, stable ties and an unchanged
input list. The actual model output was:

```python
# Keep equal-score entries in arrival order for a stable leaderboard, while ranking higher scores first without changing the caller's list.
def rank(items):
    return sorted(items, key=lambda item: item["score"], reverse=True)
```

The first apply request exceeded the support-reason length limit and was rejected
without writing source. Codex shortened that reason and applied the same preview.
No separate model endpoint, Qwen process or model download was used.

## Scope and limits

This is one synthetic integration example, not a comment-quality or retention
benchmark. Java has parser and preservation tests; the live host-model example
above used Python. No broad usefulness, energy, memory or cost advantage has been
established. The same model writes and reviews the explanation.

The live run reported 183,118 total input tokens, including 164,096 cached input
tokens, and 1,370 output tokens across the whole turn. These include host
instructions, implementation work and annotation calls. They do not measure the
plugin's incremental cost and are not a production cost estimate.

The native client used an isolated verification cache and invocation-only plugin
settings. The desktop Plugins Directory installation clicks were not automated.
Global plugin enable settings were not changed. The package still requires manual
installation and hook trust review; it has not been publicly listed or published.

Raw reports and traces remain under `artifacts/codex-plugin-0.1.0-live/` and
`artifacts/codex-plugin-0.1.0-native/space test/` in the development checkout. The
release verification manifest records their hashes. Local state and raw traces
are excluded from the source archive.

See [installation](../codex-plugin/README.md) and [product direction](product-direction.md).
