# Demo

The installed 0.4.0 package has passed its local-model workflow. Comment quality remains experimental. Use the actual package and selected model; the older quotation-only demo does not demonstrate model-written comments.

## Prepare

Install the matching VSIX in a trusted disposable local project. In the sidebar, download the local model or configure your custom endpoint and approve its disclosure. Wait until it is ready, then connect Codex and review/trust the hooks in `/hooks`.

Keep the source file and sidebar visible. Start with:

~~~python
def rank(items):
    return items
~~~

## Record the full path

1. Give Codex a normal task: **Please fix the leaderboard output in this file. Ranking should put higher scores first, but when scores tie the people should keep the order they arrived in. Our screen currently jumps between refreshes, so that part matters. Use a stable sort and leave the caller's list alone.**
2. Let the turn finish. Show the comment the selected model actually generated from the task and changed block. Do not type a replacement or promise a particular sentence.
3. Open **Review recorded context**, select the record, then **Inspect original prompt and generated comment**. Show the raw original, exact evidence, generated explanation and provider/model separately.
4. In a fresh coding-agent session, demonstrate the original context supplied by the hooks. Generated wording must not become the source of user intent.
5. Use native Undo to remove the inserted batch. A repeated scan must not replay that completed event. Explain that retiring the record, rather than Undo alone, stops its future reinjection.

The task deliberately does not name `rank`. The association comes from captured turn identity and the actual changed declaration, with model interpretation checked separately.

In the installed-editor test, Qwen3.5 2B generated and saved this actual comment:

~~~python
# Use stable sort to preserve original order for tied scores, ensuring caller's list remains unchanged.
~~~

The changed function returned `sorted(items, key=lambda item: item["score"], reverse=True)`. Generation and review took 78.55 seconds on two CPU threads. The controlled installed-editor test is recorded in `artifacts/editor-test-c515212d92a742078a341d432682fcf9/report.json`; a public Codex demo clip has not been recorded. A new run may generate different wording or abstain.

If generation skips or its support review rejects the explanation, show that result. The second review is the same model's judgment; AST preservation proves the inserted batch left parsed code intact, not that its explanation is correct.

## Show one failure honestly

Use a dirty buffer or a stale captured edit and show that it is preserved. Alternatively, disconnect a custom test endpoint: the event should remain pending, requests should pause, and retry should be explicit after the provider is fixed.

Keep controlled API-fixture output separate from actual local-model output. Do not invent a clip, claim a tuned model or describe a development smoke sample as held-out accuracy.

## LinkedIn draft

Replace the bracketed references with real artifacts after recording your demo:

> I built between-the-lines, a VS Code extension that writes comments from a coding agent's task and the code it changed.
>
> The comment is generated; the original prompt and evidence stay separate and inspectable. It can use a local model or a custom API, and checks Python AST or Java syntax/tokens before applying an undoable edit.
>
> This is an experimental Windows build. Local inference uses a 1.28 GB model download; comment quality still needs broader evaluation.
>
> [Actual demo clip] · [Source repository] · [Verified Windows VSIX]

Publish it yourself after reviewing the demo and release results. This document is a draft, not a publication record.
