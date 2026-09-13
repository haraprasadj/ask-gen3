# 3. OpenRouter for generation, Ollama for local development

Date: 2026-09-12

## Status

Accepted

## Context

The agent loop needs a model with reliable tool calling and a context window
large enough for retrieved chunks plus conversation — roughly 25 k tokens.
Self-hosting such a model means a GPU instance at USD 100+/month, or CPU
inference at tens of seconds per token-heavy turn.

OpenRouter exposes many tool-capable models behind one OpenAI-compatible API.
Sampled 2026-09-12: `mistralai/mistral-nemo` at USD 0.019/0.03 per million
prompt/completion tokens, `qwen/qwen3.7-flash` at 0.03/0.13,
`deepseek/deepseek-v4-flash` at 0.066/0.132. A typical answer — three turns,
~12 k prompt tokens, 800 completion — costs well under a tenth of a cent.

## Decision

All production generation goes through OpenRouter. The model id is a config
value, not a constant, with a cheap default and a documented upgrade path.
Ollama is the local-development and offline-evaluation backend, reached through
the same OpenAI-compatible client with a different base URL, so there is one
code path.

## Consequences

Generation cost scales with traffic and is the only usage-priced component;
the daily USD ceiling in the abuse controls is what bounds it. Model quality
can be A/B tested by changing one config value and re-running the eval set.

We depend on a third party for availability, and on OpenRouter's routing for
which provider actually serves a request — acceptable for a free public Q&A
tool, not acceptable if this ever handles sensitive data. Nothing user-supplied
beyond the question itself is sent.

## Alternatives considered

- **Anthropic/OpenAI directly** — better tool-calling reliability and clearer
  data handling, at 10–50× the token price; worth revisiting if answer quality
  on the eval set is unacceptable at the cheap tier.
- **Ollama in production** — zero marginal cost and full control, but a
  CPU-only hobby instance cannot run a competent tool-calling model at
  interactive latency, and a GPU instance breaks the budget by an order of
  magnitude.
