# 7. `google/gemini-3.1-flash-lite` as the default model

Date: 2026-09-13

## Status

Accepted

Refines [ADR-0003](0003-openrouter-for-generation.md), which chose OpenRouter
and left the model id a config value with "a cheap default". That default was
picked on price alone. This one is picked on measurement.

## Context

ADR-0003 set `mistralai/mistral-nemo` as the default because it was the
cheapest tool-capable model on OpenRouter. Nobody had run the agent loop
against it.

Eight candidates were benchmarked on 2026-09-13 against the two-repo
development index, three questions each, scoring an answer as usable only if it
exceeded 200 characters and carried at least one citation whose `(repo, path)`
resolves to a row in `chunks`. Cost is at 20 k prompt / 1.5 k completion, the
heaviest answer measured — a two-step answer runs ~4 k/400 and costs a quarter
as much.

| Model | Usable | Median latency | Valid citations | Fabricated | $/answer | Answers per $10 |
|---|---|---|---|---|---|---|
| `google/gemini-3.1-flash-lite` | 3/3 | 3.9 s | 20 | 0 | 0.00725 | ~1,400 |
| `google/gemini-2.5-flash-lite` | 3/6 | 2.5 s | 8 | 0 | 0.00260 | ~3,800 |
| `inclusionai/ling-3.0-flash` | 1/3 | 9.3 s | 7 | 0 | 0.00051 | ~19,600 |
| `qwen/qwen3.7-flash` | 1/3 | 17.9 s | 10 | 0 | 0.00079 | ~12,700 |
| `deepseek/deepseek-v4-flash` | 1/3 | 24.7 s | 4 | 0 | 0.00112 | ~8,900 |
| `openai/gpt-5-nano` | 1/3 | 26.9 s | 1 | 0 | 0.00160 | ~6,300 |
| `openai/gpt-oss-120b` | 0/3 | 38.1 s | 0 | 0 | 0.00100 | ~10,000 |
| `mistralai/mistral-nemo` | 0/3 | 13.8 s | 0 | **2** | 0.00043 | ~23,300 |

Two results decided it. The incumbent default answered nothing usable and was
the only model to invent file paths that are not in the index — the exact
failure this app exists to avoid. And the spread in usable answers, 0/3 to 3/3,
is far wider than the 17× spread in price, so price is the wrong axis to
optimise first.

`openrouter/free` remains usable and costs nothing, but it picks a free model
at random per request, so it inherits whichever of the above behaviours it
lands on.

## Decision

`google/gemini-3.1-flash-lite` is the default `OPENROUTER_MODEL`. `openrouter/free` is the
documented zero-cost option for anyone running this without a budget.

## Consequences

Between ~1,400 answers per USD 10 (six tool steps) and ~6,600 (two steps, the
common case — measured end to end at 4.0 s, 3,761 prompt and 384 completion
tokens, 7 citations). The model is now the dominant cost, an order of magnitude
above the Lambda and ECR lines — which is the correct shape, since every other
line was already near zero.

The per-IP hourly limit and daily cap are what keep that bounded, alongside the
hard spend limit on the OpenRouter key.

The benchmark ran against a two-repo index. Prompt size is bounded by the tool
result caps rather than by corpus size, so the full corpus should not move
cost much — but the numbers should be re-measured once the full index exists,
and the eval set in `evals/` is what should decide the next change, not a
three-question spot check.
