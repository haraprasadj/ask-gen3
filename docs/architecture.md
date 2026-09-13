# Architecture

How ask-gen3 answers questions about the Gen3 platform, and why it is shaped
this way. For individual decisions and their alternatives see [docs/adr](adr/).

## Problem

Gen3 knowledge is spread across 268 repositories in the `uc-cdis` GitHub
organisation (191 non-archived, ~3.5 GB of checkouts as of 2026-09-12). Answers
live in READMEs, the `docs-gen3` site source, OpenAPI specs, Helm values,
dictionary schemas and service code. A question like "how does fence issue a
refresh token?" needs retrieval across several of those at once, plus the
ability to open a specific file and read it — which is why this is an agent
loop and not a single-shot RAG pipeline.

Constraints, in priority order:

1. Publicly reachable on the internet.
2. Runs on hobby money — target under USD 15/month all-in.
3. Answers cite their sources, because a wrong answer about an auth flow is
   worse than no answer.

## Shape

Two halves that share nothing but a file.

```
 OFFLINE (GitHub Actions, weekly)          ONLINE (Lambda container, arm64)
 ┌──────────────────────────────┐          ┌────────────────────────────────┐
 │ clone --depth=1 (repos.yaml) │          │ Function URL, RESPONSE_STREAM  │
 │   ↓ select + chunk           │          │   ↓ FastAPI + SSE              │
 │   ↓ embed (fastembed, ONNX)  │          │ agent loop (tool calling)      │
 │   ↓ write index.db           │          │   ↓ search / grep / open_file  │
 │   ↓ bake into image → ECR    │          │   ↓         ↑                  │
 └──────────┬───────────────────┘          │ index.db in image (read-only) ─┤
            │ docker push + deploy         │   ↓                            │
            └──────────────────────────────┤ OpenRouter (generation only)   │
                                           └────────────────────────────────┘
```

The index is a build artifact, not a service. Nothing in the serving path
talks to a database server, a vector store or an embedding API — see
[ADR-0001](adr/0001-sqlite-as-the-retrieval-layer.md). That matters more on
Lambda than it would on a rented box, because Lambda bills wall-clock time:
every network call in the retrieval path would be paid for twice, once in
latency and once in GB-seconds.

## Corpus selection

`repos.yaml` is hand-curated and tiered, because indexing everything is both
expensive and worse: `cdis-manifest` (488 MB) and `gen3-gitops` (96 MB) are
generated deployment config that would drown real answers in near-duplicate
YAML.

| Tier | Repos | What is indexed |
|---|---|---|
| 1 — docs | `docs-gen3`, `gen3.org`, `BRH-documentation` | all prose |
| 2 — core services | ~25 (`fence`, `indexd`, `sheepdog`, `peregrine`, `arborist`, `guppy`, `metadata-service`, `hatchery`, `gen3sdk-python`, …) | prose, OpenAPI, source, `migrations/` |
| 3 — rest | remaining non-archived | README + `docs/` only |
| excluded | archived; `cdis-manifest`, `gen3-gitops` except schemas; vendored, minified, lockfiles, test fixtures, binaries | — |

Tier is a per-repo field, so promoting a repo is a one-line change and a
rebuild.

## Chunking

Format-aware, because the natural unit differs:

- **Markdown/RST** — split on headings, keep the heading path in the chunk text
  (`fence > Authentication > Refresh tokens`). Merge chunks under ~200 tokens.
- **OpenAPI/JSON Schema** — one chunk per path+method, or per definition.
- **Code** — one chunk per top-level function or class, found by per-language
  regex rather than tree-sitter: it covers the languages in this corpus, adds
  no dependency, and a missed symbol only costs a fall back to 80-line windows
  with 15-line overlap. Revisit if symbol detection becomes the weak link.
- **Everything** — hard cap 1000 tokens, and every chunk keeps
  `(repo, path, start_line, end_line, commit_sha, tier, lang)`.

Chunks are deliberately small and lossy. The agent's `open_file` tool is what
recovers full context when a snippet is not enough, which is cheaper than
indexing large chunks for every query.

## What is in index.db

The schema is [`ingest/schema.sql`](../ingest/schema.sql); `uv run
ingest/test_schema.py` builds one in memory and checks it end to end.

| Object | Holds | Projected |
|---|---|---|
| `meta` | embedding model, dimension, schema version, build time, builder commit | bytes |
| `repos` | one row per repo: tier, commit SHA, branch, licence, indexed-at | ~190 rows |
| `chunks` | text plus `(repo, path, start_line, end_line, kind, lang, heading, symbol, tokens)` | ~60 k rows, ~155 MB |
| `chunks_fts` | FTS5 over text/heading/symbol/path, external content | ~70 MB |
| `chunks_vec` | `sqlite-vec` `float[384]` per chunk | ~92 MB |

≈ 320 MB total, which is why it fits in a container image and in page cache.

Three details carry weight:

- **`tokenize="unicode61 tokenchars '_.-/'"`.** Default FTS5 would split
  `presigned_url` into `presigned` and `url`, destroying exactly the exact-match
  retrieval that BM25 is in the design to provide. The self-check asserts this.
- **External-content FTS5.** The index references `chunks` instead of copying
  its text, saving ~140 MB. Normally that needs synchronisation triggers; here
  the file is immutable after build, so it does not.
- **`repos.commit_sha`.** Every citation resolves to the commit that was read,
  not to a branch that has moved since.

The index holds no query logs, no user data and no embeddings of anything but
public repository content. It is opened read-only.

## Retrieval

Hybrid, fused, no reranker to start:

1. BM25 over FTS5.
2. Cosine over `sqlite-vec` (`bge-small-en-v1.5`, 384-dim).
3. Reciprocal rank fusion, `k=60`, roughly ten lines of Python.
4. Test files demoted by a 0.45 factor. They are worth indexing — they document
   real call signatures — but they repeat the identifiers being asked about far
   more often than the one file implementing them, so undemoted they take over
   the results for any "how does X work" question. Found by measuring, not by
   reasoning: the first real query returned three test files in its top four.
5. Top 8 chunks to the model, one per file so eight slots show eight places.

A cross-encoder reranker is the first thing to add if answer quality is the
bottleneck — but only once the eval set says so.

## Agent loop

Hand-rolled tool calling against OpenRouter's OpenAI-compatible endpoint
([ADR-0004](adr/0004-no-agent-framework.md)). Four tools:

| Tool | Purpose |
|---|---|
| `search(query, repo=None, tier=None)` | hybrid retrieval, returns cited chunks |
| `grep(pattern, repo=None, path_glob=None)` | exact symbol/string lookup via FTS5 |
| `open_file(repo, path, start, end)` | read a span around a hit, reassembled from the chunks covering it — the index stores chunks, not files, and gaps are reported rather than invented |
| `list_repos(filter)` | orient when the question names no repo |

The loop runs to a hard ceiling of 6 tool calls and ~25 k prompt tokens, then
forces a final answer. Every claim in the answer carries a
`repo/path#Lstart-Lend` citation linking to GitHub at the indexed commit; the
system prompt requires the model to say it does not know rather than answer
from parametric memory. Tokens stream to the browser over SSE.

## Cost

| Item | Monthly |
|---|---|
| Lambda compute — ~30 GB-s/answer, 400 k GB-s always free | 0 below ~13 k answers, USD 3.33 at 20 k |
| ECR storage for the ~1.05 GB image | ~USD 0.11 |
| Index build (GitHub Actions, weekly, free tier) | 0 |
| Embeddings (local ONNX, both halves) | 0 |
| Generation — `google/gemini-3.1-flash-lite`, USD 0.0015–0.007/answer by tool-step count ([ADR-0007](adr/0007-default-model-gemini-flash-lite.md)) | USD 10 per 1,400–6,600 answers |
| Generation on `openrouter/free` instead | 0, at the free tier's request limits |

Generation is the only line item that costs anything; everything else is free
or rounding. `openrouter/free` takes it to zero at the cost of unpredictable
answer quality. Hosting was compared against S3 Vectors,
CodeBuild and always-on containers in
[ADR-0006](adr/0006-lambda-container-deployment.md).

Index size projection: ~60 k chunks → 90 MB of vectors + ~250 MB text and FTS
≈ 350 MB, comfortably inside RAM-backed page cache on a 1 GB machine.

## Abuse control

The app spends real money per request and is open to the world, so this is not
where the design gets lazy:

- Per-IP token bucket (20 questions/hour) and a global daily USD ceiling read
  from config; past the ceiling the app serves cached answers and a notice.
- Hard caps on question length, tool calls and total tokens per question.
- Cloudflare in front for TLS, caching and bot filtering; Turnstile added only
  if scripted abuse actually shows up. The Function URL is not advertised
  directly, so a traffic spike cannot bypass the cache into billed compute.
- Question, answer and cost written to CloudWatch Logs for eval mining, with a
  retention policy. No accounts, no cookies, no PII. The index is read-only, so
  logs cannot go to it.
- `OPENROUTER_API_KEY` is a KMS-encrypted Lambda environment variable read once
  at module load, and a gitignored `.env` locally. It is the only long-lived
  secret: index builds use the ephemeral `github.token`, and deploys assume a
  role via OIDC rather than storing access keys. The browser talks only to this
  app, so no model key reaches the rendered page. Full table in
  [SECURITY.md](../SECURITY.md).

## Evaluation

50 hand-written questions with known-good source files, in `evals/`. Two
metrics: recall@8 for retrieval (cheap, deterministic, runs on every PR) and an
LLM-judged groundedness score for answers (runs on demand, costs cents).
Retrieval recall is the metric that actually moves — chase it first.

## Deliberate omissions

No user accounts, no conversation persistence, no multi-tenancy, no
reranker, no GraphQL/agent-to-agent API, no incremental index updates — a
weekly full rebuild costs nothing but unlimited public-repo CI minutes. Add each when there is a reason, not
before.
