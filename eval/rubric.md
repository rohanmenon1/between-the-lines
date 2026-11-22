# Reviewing a comment

Judge the comment against the original request, changed code and active earlier requirements shown in the packet. A supplied request can describe behavior that the code failed to implement. Never credit a comment for claiming that missing behavior exists.

Review candidates independently before comparing them. Do not look at the private variant key, model verdict, latency or expected labels while rating. Blank ratings mean unreviewed, not zero. Two independent reviewers are preferable; retain disagreements rather than silently averaging them away.

## Comment scores

Score all four axes from 0 to 2 for each proposed comment.

| Axis | 0 | 1 | 2 |
| --- | --- | --- | --- |
| Factuality | Includes a false claim, invented reason or unsupported guarantee. | Broadly supported but materially imprecise or ambiguous. | Every factual claim and stated reason is supported by the code and supplied context. |
| Intent preservation | Contradicts a requirement, reverses a negation, or promises a condition the code does not enforce. | Retains the main point but omits a relevant condition, exception or purpose. | Retains the relevant purpose, conditions and exceptions without overstating this block's responsibility. |
| Usefulness | Repeats obvious code, duplicates existing documentation, or misleads the reader. | Adds a relevant explanation, but mainly describes behavior or misses the most useful rationale. | Explains a decision or constraint a future maintainer could otherwise miss. |
| Readability | Incoherent, clipped, or too ambiguous to interpret safely. | Understandable but awkward, verbose or insufficiently specific. | Concise, natural and understandable beside this code block. |

For scoring, a *usable* comment requires factuality = 2, intent preservation = 2, usefulness >= 1 and readability >= 1. Report how many comments were reviewed alongside this rate. Also report usefulness = 2 separately; a minimally usable description is not necessarily a strong annotation.

Do not reward lexical novelty. A good explanation can resemble a well-written request. Do not reward overlap with an expected sentence, valid JSON, exact evidence selection, or a positive model verdict as proof of meaning.

Examples:

- `Multiply the argument by two.` above `return value * 2` is readable and factual, with little added usefulness.
- `Preserve arrival order for tied scores so the leaderboard stays stable between refreshes.` is useful when both that purpose and stable sorting are supported.
- `Reject negative withdrawals.` is false when the implementation accepts negative amounts, even if the user requested rejection.
- `Copy defaults so later requests keep their original configuration.` can be useful; `Deep-copy all nested configuration.` is false when the code only makes a shallow copy.

## No proposed comment

Leave the four comment-score fields blank. Set `abstentionAppropriate` to `yes` or `no` and briefly explain why.

- **Yes:** task-to-code relevance is uncertain, requirements conflict, the requested behavior is absent, useful intent evidence is missing, or existing code/documentation already communicates everything supplied.
- **No:** the code and request support a useful, non-obvious explanation that was missed.

An unavailable output caused by a runtime, parser or provider failure is an error. It is not a valid abstention. The summary counts errors separately and does not let them increase an abstention-success score.

## Reporting

Keep these results separate:

- Human-reviewed comment quality, with numerator and denominator.
- Comment coverage, appropriate abstention, missed useful comments and errors.
- False claims and lost conditions, including examples.
- Code-structure preservation and stale-edit protection.
- Calls, tokens, latency, CPU time and process memory.

Use the same task/code cases for each variant. Keep model and runtime settings fixed for the local comparison. The prompted-comment baseline gets the same supplied task and code; the code-only ablation intentionally loses task context. Report differing call budgets and system instructions. This compares workflows using the same local model, not the original coding agent's model or a complete commercial coding product.

The pilot cases are synthetic development material. Their `expected` and `reviewNotes` fields are author-written review targets, not independent labels. Reused cases are marked in provenance. Related families must stay together in any later train/development/test split. Review new cases independently, then freeze their content and dataset hash before using them as held-out evidence. Do not tune on held-out outputs.

## Does the annotation help later edits?

`retention-cases.json` supplies three development tasks with original requirements, a later request, original-behavior checks and new-behavior checks. Compare identical later tasks with no annotation, an ordinary prompted comment and a between-the-lines comment. Do not give the later coding agent the original request or answer checks when testing what the annotation carries; run a separate full-context comparison if needed.

A later edit succeeds only when it completes the new request and preserves the original checks. Passing only the original checks can mean the agent did nothing. Passing these selected checks is not proof of complete correctness. Use repeated runs and report both task completion and retained requirements.

The fixture sources are simple Python, but future candidate submissions are executable code. Use an appropriate sandbox for untrusted submissions. An isolated interpreter, temporary directory or timeout alone is not a security sandbox.
