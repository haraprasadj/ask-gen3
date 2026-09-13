# 1. SQLite as the entire retrieval layer

Date: 2026-09-12

## Status

Accepted

## Context

The corpus is ~60 k chunks from a curated subset of the 191 active `uc-cdis`
repositories. It changes at most weekly and is read-only at serving time. We
need both lexical and dense retrieval — Gen3 questions are full of exact
identifiers (`presigned_url`, `arborist`, `GEN3_HOSTNAME`) where BM25 beats
embeddings, and full of paraphrase where the reverse holds.

Managed vector databases start around USD 20–70/month, which is more than the
entire rest of the budget. Self-hosting Qdrant or pgvector means a second
always-on container and its memory.

## Decision

One SQLite file, `index.db`, holding chunk text, metadata, an FTS5 table for
BM25 and a `sqlite-vec` (0.1.9) `vec0` table for 384-dim vectors. It is opened
read-only and is the only retrieval dependency. Hybrid results are fused with
reciprocal rank fusion in application code.

## Consequences

Retrieval is an in-process function call: no network hop, no service to run out
of memory, no credentials. The index is a versioned artifact, so a bad build is
rolled back by pointing at the previous file, and any contributor can download
it and debug retrieval locally.

`sqlite-vec` does brute-force scan — fine at 60 k × 384 dims (single-digit ms),
not fine at ten million chunks. The corpus would have to grow ~50× before that
matters; the exit is a vector index extension or Qdrant behind the same
`search()` signature.

Writes during serving are impossible by construction. Query logs therefore go
to a separate database file.

## Alternatives considered

- **pgvector on a managed Postgres** — real ANN indexing and one system for
  logs too, but the cheapest always-on instance costs more than the app and
  adds a network hop to every retrieval.
- **Qdrant/Chroma in the same container** — no per-month fee, but a second
  process competing for 1 GB of RAM, plus its own snapshot lifecycle, to serve
  a corpus small enough to scan.
- **Embeddings in a numpy file, BM25 from `rank_bm25`** — even simpler, but
  loads the whole corpus into process memory and gives up SQL filtering by
  repo, tier and language.
