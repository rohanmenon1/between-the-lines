# Evaluate between-the-lines

There are two questions: are the comments worth keeping, and do they help a later coding agent preserve the original requirements? The tools here measure them separately. Code preservation, valid JSON and a model approving its own comment do not answer either question.

All commands run from the project root using the development environment. They never download weights, call a cloud provider or train a model. Comment generation reads case source without executing or editing it. The later-edit grader executes supplied Python only when explicitly requested.

## Compare comments locally

The comparison uses the same local weights and runtime settings under three conditions:

| Condition | What it receives | What it does |
| --- | --- | --- |
| `between_the_lines` | Original task, changed declaration, before version when available, scoped earlier requirements | Calls the production synthesis helper and its second support review. |
| `prompted_comment` | The same source, task and scoped requirements | One request to add a short comment explaining intent and constraints. |
| `code_only` | The same source and before version, without the task or history | The same one-request comment prompt. |

This evaluates comment generation after the edit. It does not replay complete Codex hooks or establish that the full product would choose every target. The production selector, for example, can reject existing human documentation before this helper is called.

The simple controls use the runtime's existing 180-character proposal grammar; production synthesis permits 240 characters and evidence fields. Calls and prompts differ by design. Reports disclose these differences. This is a workflow comparison, not an isolated prompt ablation, equal-token-budget comparison, or measurement against the original Codex/Copilot model commenting while it writes code.

Validate the 20 cases and run a small comparison with existing weights:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py validate
.\.venv\Scripts\python.exe scripts/evaluate.py run --model-path models/Qwen3.5-2B-Q4_K_M.gguf --case-id cached_falsy_values --case-id unrelated_changed_block --output artifacts/my-pilot/report.json
```

Omit the case IDs to run all 20, or use `--limit`. Start small: the local two-call production path has taken about a minute per block on two CPU threads. Reports freeze the selected cases, source hashes, model hash, settings and shuffled execution order before inference. They retain every result, including errors and rejected raw proposals. Reports are written after each job; interrupted runs cannot be exported as complete reviews. Existing outputs are never overwritten.

Memory is for the evaluation process, including loaded weights. Windows peak RSS is a cumulative process high-water mark, not an independent memory measurement for each condition. The first request includes loading; randomized ordering does not eliminate cold/warm effects. No energy is measured.

## Review without seeing the source condition

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py review --report artifacts/my-pilot/report.json --output artifacts/my-pilot/review --reviewer reviewer-1
```

Open `review/review.html`. It works offline and needs no server. Share only `review.html`, `ratings.csv` and `README.md` with reviewers. Keep `private/key.json` and the raw report with the evaluator; they identify the conditions and model decisions. A second reviewer uses the same HTML and enters a distinct reviewer ID.

The page shows the task, before/after code, earlier original requirements and an anonymous proposed comment or absence. It hides the model, condition, same-model verdict and expected answer. Read [rubric.md](rubric.md), score independently, and download each filled CSV before closing the page. There is no autosave or upload.

For each comment, rate factuality, preserved intent, usefulness and readability from 0 to 2. For an absence, answer whether abstaining was appropriate. An unavailable generation is a runtime failure, not a successful abstention. A proposed comment counts as usable only when factuality and intent are both 2 and usefulness and readability are at least 1. This is a stated review rule, not a validated production reliability threshold.

Summarize the downloaded ratings:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py score --report artifacts/my-pilot/report.json --packet artifacts/my-pilot/review --ratings artifacts/ratings-reviewer-1.csv artifacts/ratings-reviewer-2.csv --output artifacts/my-pilot/human-summary.json
```

You can supply one CSV while waiting for a second reviewer. Blank or partial labels stay pending, with null rates when nothing is reviewed. Duplicate labels, changed reports and unknown candidate IDs are rejected. Results show each reviewer's denominators, usable comments, false claims, omitted intent, appropriate abstentions and disagreements. Paired comparisons include only cases where both conditions produced fully reviewed comments; coverage and missing pairs stay visible. Costs and code preservation remain separate from these human scores.

## Test whether intent survives later edits

Three Python tasks in `retention-cases.json` check stable ranking, unchanged shared defaults and retained partial batches. These are synthetic development fixtures, not a claim about real agent reliability.

First export their original annotation inputs:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py retention-prepare --output artifacts/retention-inputs
.\.venv\Scripts\python.exe scripts/evaluate.py run --model-path models/Qwen3.5-2B-Q4_K_M.gguf --cases artifacts/retention-inputs/annotation-cases.json --output artifacts/retention-annotations.json
.\.venv\Scripts\python.exe scripts/evaluate.py retention-prepare --annotations artifacts/retention-annotations.json --output artifacts/retention-comparison
```

The final packet contains the three annotation conditions and a no-comment control. An abstention leaves the original source unchanged; a generation failure remains unavailable. Each job has an opaque ID, `source.py` and `TASK.md`. Copy only those two files into a separate disposable workspace, outside this checkout, for each fresh coding-agent session. Keep the coding model, settings and tools the same and keep the evaluator checkout outside the agent's allowed access. Do not supply the original conversation, `annotation-cases.json`, raw report or `private/`: they reveal earlier intent, condition identities or test answers. Copy the returned full source back to that job's `result.py` without modifying its inputs.

Record the coding model/version, settings, tool access, date, attempts, latency and tokens alongside the packet. The current exporter does not automate coding-tool sessions or collect those costs. Repeated trials and more reviewed tasks are needed before drawing a comparison.

Grade returned Python only in an appropriate execution environment:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py retention-grade --packet artifacts/retention-comparison --output artifacts/retention-summary.json --allow-execution
```

The flag deliberately executes supplied `result.py` files in subprocesses with a timeout, temporary working directory and reduced environment. It is not an operating-system sandbox; run untrusted candidates inside a disposable sandbox. Grading never calls a language model.

The report distinguishes original requirements preserved, new task completed and both together. Leaving the code unchanged passes the original checks but fails the new request. Missing jobs, runtime failures and annotation failures never count as successful retention. Checks cover selected observable behaviors, not every possible input or the complete meaning of intent. This experiment initially measures source comments; it does not measure the product's separate history reinjection feature.

## Keep development and final evidence separate

`pilot-cases.json` contains 12 previously used cases and eight additional synthetic cases. Every record is marked unreviewed. Known failures were used to develop prompts; this set is not held out. The `expected` and `reviewNotes` fields guide later review and never enter model payloads.

Before a reliability claim, collect and independently review real task/edit cases, record provenance and license, split by repository and requirement family, and freeze a held-out set before tuning prompts or weights. Keep negative cases, unnecessary-comment cases and abstentions. Two independent reviewers should adjudicate disagreements. The evaluation plan in [docs/evaluation.md](../docs/evaluation.md) describes the larger comparison.

Test the evaluation tools without inference:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s scripts/tests -v
```

The extension installer and model are unchanged by this tooling. Public release claims must distinguish software tests, development model trials, human review and downstream agent results.
