# 10. A cost ceiling per question, not a step count

Date: 2026-09-21

## Status

Accepted

Amends the ceiling described in [ADR-0004](0004-no-agent-framework.md), which
recorded six tool calls and ~25 k prompt tokens. That ADR's decision — no agent
framework — stands; only the ceiling it described has changed.

## Context

The loop stopped at six tool calls, with a soft nudge when one prompt exceeded
25 k tokens. Both are proxies for the thing anyone actually cares about, which
is what a question costs, and both measure it badly.

Every call in a question re-sends the whole conversation, so the bill is the
sum of the prompts, not the size of the largest one. Measured on real traffic
through OpenRouter, a question runs about 14:1 input to output, and a late tool
call re-sends 6–9 k tokens to emit an 18-token function call. A step count
cannot see any of that: six cheap rounds and six expensive ones differ by more
than an order of magnitude and hit the same ceiling.

The prompt-token check was also reading the wrong number. `usage.prompt_tokens`
was assigned rather than accumulated, so it held the last call's prompt and
understated the real spend by 3–5× on a multi-step answer.

## Decision

One ceiling: `MAX_QUESTION_TOKENS = 180_000`, counted across every call in a
question, prompt and completion together. At flash-lite rates ($0.25/M in,
$1.50/M out) and the output sizes observed, that lands a question just under
USD 0.05.

- `usage.prompt_tokens` accumulates, so the brake reads the billed total and
  the page reports something true.
- When the budget will not fit another round, the next call is offered no
  tools, so it has to answer. When a single round overruns the budget outright,
  the loop stops and reports the count rather than charging on.
- There is no step ceiling. A cheap question gets as many rounds as it needs;
  an expensive one stops sooner than six.
- Tool results are capped in tokens (`MAX_TOOL_RESULT_TOKENS`) in one place for
  every tool, because an uncapped result is re-sent on every later call and so
  is charged repeatedly. This is the knob that trades result size for rounds.

Token counts from the provider are real. The estimator (`CHARS_PER_TOKEN`) is
used only where no count exists yet: sizing history, and billing a round when a
provider reports no usage at all — without which nothing counts up and the loop
never terminates.

## Consequences

A hard question now runs ~12 rounds where it ran 6, so the worst case is
roughly twice the latency. `REQUEST_TIMEOUT` (120 s) is what bounds that now,
and it, not the token budget, is likely to be what fires first.

The budget is in tokens, but the number was chosen from prices that will
change. It is a constant in `server/agent.py` with the arithmetic in a comment,
not a computed figure — re-derive it when the default model changes, the way
[ADR-0007](0007-default-model-gemini-flash-lite.md) re-derived the model.

`estimate_tokens` is roughly 2× conservative against real counts on this
corpus, so the reserve that triggers the final answer fires earlier than it
needs to. That is the safe direction — it answers rather than overruns — but it
means `CHARS_PER_TOKEN` is a calibration knob with a known error, not a fact.
