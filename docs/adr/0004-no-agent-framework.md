# 4. Hand-rolled agent loop, no RAG framework

Date: 2026-09-12

## Status

Accepted. The step and prompt ceiling described below is amended by
[ADR-0010](0010-a-cost-ceiling-per-question-not-a-step-count.md).

## Context

LangChain, LlamaIndex and their agent abstractions cover retrievers, chunkers,
tool loops and prompt templates. This application needs exactly four tools, one
retrieval strategy and one loop with a call ceiling.

Both frameworks pull in large dependency trees, change public APIs often, and
place their own abstractions between the prompt actually sent and the code that
builds it — which is the thing that most needs to be inspectable when an answer
cites the wrong file.

## Decision

The loop is written directly against the OpenAI-compatible chat completions
API: send messages and tool schemas, execute any returned tool calls, append
results, repeat until the model answers or the ceiling (6 calls, ~25 k prompt
tokens) is hit. Roughly 150 lines. Chunking, fusion and prompt assembly are
plain functions.

## Consequences

Every token sent to the model is visible in one file, so prompt regressions are
diffable and the eval harness can replay a request exactly. Dependencies stay
at FastAPI, the OpenAI client, `fastembed` and `sqlite-vec`.

We implement retries, streaming and tool-schema plumbing ourselves, and we
forgo the frameworks' ready-made extras (query rewriting, multi-hop planners,
evaluators). If several of those turn out to be needed, revisit — but the
cost of adopting a framework later is lower than the cost of reverse-engineering
one now.

## Alternatives considered

- **LlamaIndex** — strongest ingestion and retrieval abstractions, and would
  save real work in the chunking layer; rejected because it wants to own the
  index format, which ADR-0001 already fixes to plain SQLite.
- **LangGraph** — appropriate once the loop has branching state or human
  checkpoints; this loop has neither.
- **OpenAI Agents SDK / provider-native agent loops** — ties the design to one
  provider, which is what routing through OpenRouter is for.
