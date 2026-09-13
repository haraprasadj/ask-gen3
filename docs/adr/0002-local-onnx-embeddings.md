# 2. Embeddings from a local ONNX model, not Ollama or an API

Date: 2026-09-12

## Status

Accepted

## Context

Embeddings are needed in two places: once per chunk at index build time
(~60 k calls, offline, in CI) and once per `search` tool call at serving time
(~2–4 per question, on the request path, latency-visible).

Index and query vectors must come from the same model and the same pooling, or
retrieval silently degrades. That makes "which runtime" a single decision for
both halves, not two independent ones.

Ollama is the obvious candidate since it is already in the stack for local
model work, but at serving time it means a second process holding a model
resident on a 1 GB machine.

## Decision

`fastembed` (0.8.0) running `bge-small-en-v1.5` — 384 dimensions, ~130 MB ONNX
weights, CPU — in-process on both sides. Ollama stays in the stack for local
generation during development, not for embeddings
([ADR-0003](0003-openrouter-for-generation.md)).

The model id and dimension are written into `index.db` metadata at build time
and asserted at server start, so a model change cannot be deployed against a
stale index.

## Consequences

Embedding costs nothing and requires no network in either half. The serving
container is a single process; query embedding is ~10 ms on a shared vCPU.
Index builds run on free GitHub Actions minutes.

384 dimensions retrieve slightly worse than a 768-dim model such as
`nomic-embed-text`, and keep the vector table at ~90 MB instead of ~180 MB.
Changing model means a full rebuild, which is an hour of free CI, not a migration.

## Alternatives considered

- **Ollama `nomic-embed-text` on both sides** — better quality at 768 dims and
  one fewer concept in the stack, but requires an Ollama sidecar in production
  or a hosted endpoint, which is the cost this design is avoiding.
- **OpenAI/Voyage/Cohere embedding APIs** — best quality, but a per-request
  dependency and key on the serving path, and a bill that scales with traffic
  rather than with corpus size.
- **Ollama for the offline build, ONNX at query time** — cheapest to run but
  mixes models across the two halves; the resulting retrieval bug would be
  quiet and hard to attribute.
