# Overview

ask-gen3 answers questions about the Gen3 codebase, and every claim in an
answer is cited to the source it came from. This page is the short tour: what
it does, how it is built, and what it costs. Each part ends at the decision
record that argues it in full. [Architecture](architecture.md) goes deeper.

## The problem

Gen3 is a data-commons platform spread across hundreds of public repositories
in the `uc-cdis` organisation. Answering *"how does fence issue a refresh
token?"* means:

- knowing it's `fence`, not `arborist` or `wts`
- finding `oidc/grants/refresh_token_grant.py`
- reading the code, not the README's summary of it

Newcomers can't do the first step. Search engines don't index the second
well.

## What it does

**In:** a natural-language question.
**Out:** a prose answer where every claim carries a citation to real source.

```text
[fence/fence/oidc/grants/refresh_token_grant.py#L34-L71]
```

A citation names a repository, a path and **line numbers that resolve** at the
indexed commit.

## The one promise

> Never answer from memory. Every claim comes from a tool result.

A model that read Gen3 during pretraining will happily invent
`fence/auth/refresh.py`. It sounds right, and it does not exist. **The design
exists to make that failure impossible.**

## Two phases

```text
BUILD (offline, on demand, free)    QUERY (online, $0.002 typical)
─────────────────────────────       ─────────────────────────────
clone 176 repos                     question
  ↓                                   ↓
chunk into 17,463 pieces            embed it (same model)
  ↓                                   ↓
embed each chunk                    search index.db
  ↓                                   ↓
write index.db (61 MB) ───────────► feed chunks to a model
                                      ↓
                                    cited answer
```

The index is a build artifact
([ADR-0005](adr/0005-index-as-a-build-artifact.md)), rebuilt on demand
([ADR-0011](adr/0011-index-rebuilt-on-demand.md)):

| | Build | Query |
|---|---|---|
| Runs | on demand, by hand | per request |
| Takes | one to three hours | seconds |
| Needs | git, network, 176 clones | one file |
| Costs | nothing | ~$0.002 |

The server never clones anything. It opens a file.

## Build: chunking and embeddings

Chunks follow structure, not fixed-size windows:

- **prose**: markdown sections, split on headings
- **code**: functions and classes, through language-aware parsing
- **openapi**: one per endpoint
- **schema**: one per definition

Each chunk records `repo`, `path`, `start_line`, `end_line`, `symbol` and
`heading`.

Embeddings come from `BAAI/bge-small-en-v1.5`, running as ONNX on the local
CPU: 384 dimensions, a 64 MB quantized model, the whole corpus in one pass, at
no cost. [ADR-0002](adr/0002-local-onnx-embeddings.md) rejected hosted
embedding APIs. At corpus scale they are a real bill; here the cost is a
runner busy for an hour.

## The artifact

One SQLite file holds all three parts of retrieval:

| Table | Holds | Serves |
|---|---|---|
| `chunks` | text, repo, path, lines | content and citations |
| `chunks_fts` | FTS5 inverted index | keyword search (BM25) |
| `chunks_vec` | one 384-float vector per chunk | semantic search |

SQLite is the entire retrieval layer
([ADR-0001](adr/0001-sqlite-as-the-retrieval-layer.md)): no Elasticsearch, no
pgvector, no vector database, no running service.

## Query: hybrid retrieval

Every search runs both ways, because they fail in opposite directions:

| | Good at | Bad at |
|---|---|---|
| **BM25** | exact identifiers: `ssjdispatcher`, `presigned_url` | paraphrase, concepts |
| **Vectors** | "how does auth work" | rare tokens never seen in training |

`ssjdispatcher` was not in bge-small's training data, so its vector is close
to noise. BM25 doesn't care: an exact match is its best case.

One tokenizer setting carries most of the keyword half's value:

```text
tokenize="unicode61 tokenchars '_.-/'"
```

| Input | Default FTS5 | Ours |
|---|---|---|
| `refresh_token_grant.py` | `refresh` `token` `grant` `py` | one token |
| `fence/jwt/token.py` | `fence` `jwt` `token` `py` | one token |

With the default, a search for `token.py` matches every chunk containing
"token": thousands, all useless.

The two result lists are merged with reciprocal rank fusion, by rank rather
than score. BM25 scores and cosine similarities are not comparable quantities,
and normalising them is tuning with no principled answer. Ranks are always
comparable, so RRF needs no weights, no calibration and no retuning when the
corpus grows.

## Query: an agent, not a pipeline

Classic RAG retrieves once, stuffs the context and generates. Here the model
gets five tools and decides what to do:

| Tool | For |
|---|---|
| `search` | concepts, through hybrid retrieval |
| `grep` | exact identifiers |
| `open_file` | reading more around a hit |
| `list_repos` | orienting: what is indexed at all |
| `fetch_url` | a page the index doesn't hold |

Take *"walk me through a controlled-access file download."* Single-shot
retrieval finds chunks about one service. The loop runs `search` on fence,
`grep` for presigned, `open_file` on the handler and `search` on arborist,
then answers across three services. Questions that span services are where
the codebase is hardest to learn, and where one-shot RAG fails.

The loop is hand-written ([ADR-0004](adr/0004-no-agent-framework.md)), with
no LangChain or LlamaIndex. Call the model; if it asks for tools, run them,
append the results and repeat. The whole agent is one readable file, every
token sent to the model is visible in one place, and no dependency rewrites
its API each minor version.

### Reaching outside the index

Release notes and the docs site aren't in the corpus, and the index is a
snapshot besides. `fetch_url` is the one tool that leaves the process, and it
is built in rather than the reference MCP server
([ADR-0009](adr/0009-built-in-fetch-tool-over-mcp.md)). Who fetches the bytes
was never the issue. The issue is that the model picks the URL, and on Cloud
Run the first useful target is `169.254.169.254`. So:

- https only, four allowlisted hosts, and `uc-cdis` as the first path segment
  on GitHub
- redirects are never followed blindly; every hop is checked again
- every resolved address must be public
- results are marked untrusted and cited as URLs, not as `[repo/path#L]`

### Follow-ups without a session store

The page keeps the transcript and posts it back with every question. The
server stores nothing, which is what lets any instance serve any turn.

- There is no turn limit. History fills a 6 k token budget, newest first: a
  long exchange of short questions survives whole, and one verbose answer is
  truncated rather than evicting four others.
- Only text travels. Earlier tool results are dropped, because history is
  re-sent on every call, and replaying them would spend the next question's
  budget.
- History arrives in a body the caller controls, so only `user` and
  `assistant` strings are admitted. A forged `system` turn never reaches the
  model.

### A budget, not a step count

The loop used to stop at six tool calls. Six cheap rounds and six expensive
ones differ by more than an order of magnitude and hit the same ceiling. Every
call re-sends the whole conversation, so the bill is the sum of the prompts,
not the size of the largest one
([ADR-0010](adr/0010-a-cost-ceiling-per-question-not-a-step-count.md)).

| | Before | Now |
|---|---|---|
| Ceiling | 6 tool calls, ~25 k prompt tokens | 180 k tokens across the question |
| Meaning | a proxy for cost | ~USD 0.05, the real number |
| Cheap question | capped at 6 rounds | as many rounds as it needs |

Out of budget, the next call is offered no tools, so it has to answer.

## Grounding, enforced

Four mechanisms, not just a hopeful prompt:

1. **Tool results only.** The model is told never to answer from memory.
2. **Citations come from tool output.** Retrieval emits the markers; the model
   doesn't compose them.
3. **Repository and web content is untrusted.** A file saying "ignore your
   instructions" is reported, not obeyed.
4. **Hard ceilings.** 180 k billed tokens and about USD 0.05 per question,
   2,000-character questions, 120 s of wall clock.

## Choosing the model

[ADR-0007](adr/0007-default-model-gemini-flash-lite.md) benchmarked eight
candidates. The top of the table:

| Model | Usable | Valid cites | Fabricated | $/answer |
|---|---|---|---|---|
| **gemini-3.1-flash-lite** | **3/3** | **20** | **0** | 0.0073 |
| gemini-2.5-flash-lite | 3/6 | 8 | 0 | 0.0026 |
| gpt-5-nano | 1/3 | 1 | 0 | 0.0016 |
| mistral-nemo *(incumbent)* | 0/3 | 0 | **2** | 0.0004 |

The cheapest model invented file paths, the one failure this app exists to
prevent. The quality spread (0/3 to 3/3) far exceeds the 17× price spread.

## Deployment

Cloud Run was the third attempt
([ADR-0008](adr/0008-cloud-run-deployment.md)):

| Attempt | Outcome |
|---|---|
| AWS Lambda | The function URL returned 403 to everything: correct policy, correct auth type, direct invoke fine, no CloudWatch entry. An account-level block |
| Hugging Face Spaces | `Quota exceeded for cpu-basic: limit=0` |
| Cloud Run | Live |

It fits because it scales to zero, streams server-sent events with no
adapter, and runs the same container as `docker run`. The service runs at
1 vCPU and 2 GiB, with at most two instances. The index ships inside the
image: no volume, no bucket, no download on cold start.

CI holds no keys. The deploy workflow signs in with Workload Identity
Federation: GitHub mints a short-lived OIDC token, and Google Cloud accepts it
only from this repository's `main` branch.

```text
index  (manual)   ──► 1–3 hours ──► index.db artifact
                                        │
                                        ▼
deploy (auto)     ──► Cloud Build ──► CVE scan ──► Cloud Run ──► /health check

ci     (per PR)   ──► lint, 7 test modules, pip-audit, gitleaks ──► minutes
```

The index is rebuilt by hand on purpose. The corpus moves slowly, a stale
index is not an outage, and scheduled workflows disable themselves after 60
idle days.

## What it costs

Per answer, inference is nearly all of it:

| Component | Per answer | Share |
|---|---|---|
| OpenRouter inference | $0.0020 | 87% |
| Cloud Run CPU | $0.00026 | 11% |
| Cloud Run memory | $0.00003 | 1% |
| Requests, egress | ~$0.000001 | ~0% |

Latency costs twice, because the container is billed while it waits on the
API.

Fixed monthly:

| Item | Cost |
|---|---|
| Secret Manager (1 version) | $0.06 |
| Artifact Registry | $0.01 |
| Cloud Build, Logging | $0.00 (free tier) |
| Idle compute | $0.00 (scales to zero) |
| **Total** | **~$0.07/month** |

There is no database, VPC, load balancer or NAT, which is where hobby projects
usually bleed.

By volume:

| Questions/month | Total |
|---|---|
| 100 | $0.30 |
| 1,000 | $2.36 |
| 10,000 | $23 |

The ceilings are USD 0.05 per question, 20 questions per IP per hour, 2,000 per
instance per day and two instances. The real backstop is the spend limit on
the OpenRouter key, which no amount of concurrency can exceed.

## Evals

`evals/questions.jsonl` holds 50 questions with checkable expectations, facts
and citations rather than prose answers, which no scorer can match:

```json
{"id": "fence-006",
 "question": "How does fence generate a presigned URL?",
 "must_include": ["indexd", "presigned", "authz"],
 "should_cite": ["uc-cdis/fence/fence/blueprints/data/indexd.py"]}
```

Four are refusal cases: off-topic, unsafe and secret-fishing questions.
`evals/check.py` verifies that every expected citation resolves against the
index.

## Honest limits

- **A snapshot, not live.** The index reflects the last build.
- **Hard questions take time.** Without a step ceiling, one can run about 12
  rounds; the 120 s timeout bounds it.
- **Retrieval sets the ceiling.** A wrong chunk means a wrong answer,
  confidently cited to a real file.
- **The token estimate is about 2× conservative**, so the loop answers earlier
  than it strictly must. That is the safe direction, but it is a calibration
  knob, not a fact.
- **Rate limits are per instance.** The counters don't coordinate, so the real
  ceilings are about twice the configured ones.
- **English only.** The embedding model is English-trained.

## What it demonstrates

Boring infrastructure, chosen carefully:

- SQLite instead of a vector database
- local ONNX instead of an embeddings API
- a hand-written loop instead of a framework
- an allowlist in the same file as the request, instead of an MCP server
- a cost ceiling instead of a step count
- a measured model choice instead of a guess
- federated identity instead of stored keys

About $0.07 a month fixed, zero at idle, and every claim cited.
