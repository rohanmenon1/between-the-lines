# Product direction

A Codex plugin can be a useful way to reach developers already using coding
agents. It reduces setup and puts the product where the work happens. Its
installation format does not determine whether it can become a business.

The present product is an experimental annotation workflow. We have software
checks and a few development examples. We have not shown fewer regressions in
real projects, repeat use by teams or willingness to pay. Calling it a venture
scale product now would get ahead of the evidence.

## What could make it valuable

The durable asset would be a record of why code was changed, tied to the actual
user request and affected declarations. Comments make that record visible in
source. Inspectable originals, revisions, retirement and later retrieval could
help teams preserve decisions across agent sessions and tools.

That value must survive a stronger coding model. Automatic capture and persistent
provenance are capabilities a better comment-writing prompt does not supply by
itself. Whether developers need a separate product for them remains unproven.

## Risks to test

- **Platform substitution:** Codex or another coding tool can add native context
  capture and annotations. A Codex-only implementation gives that platform control
  over distribution, hooks, model behavior and usage costs.
- **Low-value output:** Extra comments can become noise or carry a wrong
  interpretation forward. Exact quotations and AST checks cannot establish truth.
- **Weak demand:** A technically convincing demo may still save too little work
  for a developer or team to adopt and pay for it.

Keep the intent/history and validation core separate from the Codex adapter. Ship
one integration, observe real use, and add another only when demand justifies it.
Cross-tool portability can reduce dependence, but supporting every editor before
finding users would spread effort thinly.

The strongest next demonstration is a later edit that completes its new request
while preserving an earlier, easy-to-miss requirement. Compare repeated runs with
no annotation, an ordinary comment prompt and between-the-lines, using the same
coding model and executable behavior checks. Report false comments, missed
comments, task failures, latency and costs alongside retained requirements.

For a larger business case, show recurring use on real repositories and a
measurable reduction in requirement loss. Plugin distribution can be the starting
point. The business has to earn its value through those outcomes.
