# Model and data evaluation

The useful question is whether this agent can make a justified decision about a code change under a small resource budget. A fine-tune is worth shipping when it improves that decision on cases kept out of training.

The runnable workflow is in [eval/README.md](../eval/README.md): local three-condition comparisons, blinded human ratings and later-edit behavioral checks. The initial 20 cases are synthetic development material, including 12 reused cases; none is independently reviewed or held out. The three later-edit fixtures distinguish retaining original requirements from completing the new task. No human usefulness score or downstream-agent advantage has been established.

The first actual local pilot completed two cases under all three conditions (six outputs). Its frozen report is `artifacts/evaluation-0.4.0-pilot/report.json`; its review page is `artifacts/evaluation-0.4.0-pilot/review/review.html`. Human ratings remain blank. The evaluation tooling has 35 passing tests covering blinded export, score denominators, provenance, missing outputs and later-edit checks. The browser page has static/syntax checks; interactive browser testing was not completed because the embedded browser blocks local file URLs.

## Data options

| Dataset | What it teaches | Main limitation |
| --- | --- | --- |
| Existing code-to-comment pairs | Comment style and code summarization. | Usually lacks the original intent, edits, evidence, and examples where no comment is needed. |
| Public issues and PRs with before/after code | Connecting a requested change to implementation and documentation. | PR descriptions can be written after implementation. Merged code and existing comments are not reliable labels by themselves. |
| Reviewed intent–implementation cases | Choosing skip, context lookup, comment, or mismatch from evidence. | Requires careful labeling, including uncertainty and plausible negative examples. |

The third option fits this agent best. Public issues and PRs can supply raw cases. Record repository, commit, license, source URL, intent provenance, code before/after, available context, expected decision, and a short evidence-based rationale. Review license terms before publishing a derived dataset.

## Start with evaluation

Prepare 100–200 human-reviewed cases before training. Include aligned changes, violated requirements, outdated generated comments, missing evidence, obvious code, and misleading task instructions. Include Python and Java, nested declarations, overloads, Unicode, and concurrent edits.

Split by repository and time; deduplicate related functions and issue families. Keep retrieval context from before the change so it cannot reveal the final answer. Public code may already appear in model pretraining, so report that limitation. Deliberately introduced violations need human review and a distinct label from observed real bugs.

Keep a separate development set for prompt changes. Freeze the held-out set before evaluating a fine-tune.

## Comparisons

- Untuned versus fine-tuned model using the same quantization and runtime settings.
- Intent available versus code alone.
- Allowed context lookup versus no retrieval.
- Agent decisions versus a simple changed-function comment baseline.

Measure comment correctness and usefulness with a written review rubric. Report mismatch precision/recall, abstention, tool validity, preserved code, and stale edit rejection. Also report model calls, input/output tokens, latency, peak process memory, and CPU time. Measure energy separately if making an energy claim.

AST/token preservation is a deterministic safety check, not a semantic accuracy score. A model that always skips is safe but unhelpful; report coverage alongside accuracy. A model that always comments needs to be penalized for unnecessary or invented explanations.

## Release criteria

Version 0.4.0 offers experimental automatic comments after explicit model setup and hook connection; manual review is also available. The installed workflow checks do not establish a reliable semantic threshold. Before describing automatic comments as broadly reliable, run the held-out evaluation above and publish failures, coverage and resource settings. Only replace `config/model.json` with fine-tuned weights after exporting GGUF, verifying its checksum and license, and comparing it against the pinned baseline.

The previous Mellum adapter targets a different model architecture. It cannot be attached to Qwen weights.
