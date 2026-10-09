# ask-gen3

Ask questions about [Gen3](https://gen3.org) and get answers grounded in the
source, with citations that link to the exact lines they came from.

Gen3 knowledge is spread across 268 repositories in the
[uc-cdis](https://github.com/uc-cdis) organisation — READMEs, docs-site source,
OpenAPI specs, Helm values, dictionary schemas and service code. Answering
"how does fence issue a refresh token?" means searching several of those at
once and then reading a specific file. This is an agent loop with four
retrieval tools over a prebuilt index, not a single-shot RAG pipeline.

It runs on a hobby budget: retrieval is a SQLite file with no service behind
it, embeddings are local, and only generation costs money.

## Install

Prerequisites: [uv](https://docs.astral.sh/uv/getting-started/installation/)
and [just](https://github.com/casey/just#installation). uv fetches Python 3.13
itself.

```sh
just setup
just test
```

Retrieval loads the `sqlite-vec` extension, so Python must allow SQLite
extensions. Current uv-managed builds do; older ones fail with
`'sqlite3.Connection' object has no attribute 'enable_load_extension'`. If you
see that, run `uv self update`, then `uv python install --reinstall 3.13`.

## Quickstart

Build a small index from two repositories, then ask it something:

```sh
just index-dev
```

Or download the full prebuilt index, about 60 MB, with `just fetch-index`.

Answers need a model. Locally, point at [Ollama](https://ollama.com) with any
tool-capable model:

```sh
ollama pull qwen3:8b
just run
```

Any tool-capable tag works; set `LOCAL_INFERENCE_MODEL` in `.env` to pick a different one.

Open <http://localhost:8000>. For the hosted configuration, set
`HOSTED_INFERENCE_API_KEY` and use `just run-prod`.

## Usage

```sh
just                      # list recipes
just fetch-index          # download the prebuilt full index
just index                # build the full corpus, about an hour
just index "--only fence" # one repository
just check                # lint and every self-check
just deploy               # build on Cloud Build, deploy a Cloud Run revision
```

Configuration is environment variables, read from `.env`. There are two
inference slots, local and hosted, and each owns its own URL, model and key, so
switching between them cannot half-apply. Either slot takes any
OpenAI-compatible endpoint:

| Variable | Default | Purpose |
|---|---|---|
| `PROVIDER` | `hosted` | `local` or `hosted`; picks which slot below is live. `.env.example` ships `local` for local work. `just run` forces `local`, `just run-prod` forces `hosted` |
| `HOSTED_INFERENCE_API_KEY` | — | required when the slot is `hosted` |
| `HOSTED_INFERENCE_MODEL` | `google/gemini-3.1-flash-lite` | any tool-capable model; benchmarked in [ADR-0007](docs/adr/0007-default-model-gemini-flash-lite.md). `openrouter/free` costs nothing but varies in quality. Set it whenever you change the URL: the default is an OpenRouter model ID |
| `HOSTED_INFERENCE_URL` | `https://openrouter.ai/api/v1` | |
| `LOCAL_INFERENCE_MODEL` | `qwen3:8b` | the model `just run` serves. A tag name says nothing about size — `ollama show <tag>` does |
| `LOCAL_INFERENCE_URL` | `http://localhost:11434/v1` | |
| `LOCAL_INFERENCE_API_KEY` | — | only for a local server that checks one; Ollama does not |
| `INDEX_PATH` | `index.db` | where the index lives |
| `RATE_LIMIT_PER_HOUR` | `20` | per-IP question limit |
| `DAILY_QUESTION_CAP` | `2000` | per-instance daily ceiling |
| `REQUEST_TIMEOUT_SECONDS` | `120` | wall-clock ceiling on one answer |
| `PUBLIC_URL` | this repository | sent to OpenRouter as `HTTP-Referer`, which it uses to attribute traffic, and to no other endpoint; set it to your deployment's address |

`just setup` creates a gitignored `.env` from `.env.example`; every recipe
loads it. Set a hard spend limit on the OpenRouter key as well — it is the only
budget ceiling that concurrency cannot exceed. See [SECURITY.md](SECURITY.md)
for where every credential lives.

Questions are sent to the hosted endpoint — OpenRouter and the model it routes
to, by default — which are third parties. Nothing is stored: no question, no
answer, no analytics, no cookie.
[SECURITY.md](SECURITY.md) has the full list of what leaves and what is kept.

## How it works

Two halves that share only a file. A GitHub Actions job, run by hand, clones the
curated repositories, chunks them, embeds locally, and produces `index.db`;
the server opens it read-only and never talks to a database, vector store or
embedding API.

- [Architecture](docs/architecture.md) — components, corpus selection, cost,
  abuse controls
- [Deploying](docs/how-to/deploy.md) — Cloud Run, Cloud Build and the CI deploy path
- [Decision records](docs/adr/) — why SQLite, why local embeddings, why
  OpenRouter, why no framework, why Cloud Run, why a built-in fetch tool, why the index is a release asset
- [Overview](docs/overview.md) — the design and what it costs, in one page

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). Indexed
content remains under the licences of the repositories it came from.

Not affiliated with, endorsed by, or sponsored by the Gen3 project, the
University of Chicago or the Center for Translational Data Science. "Gen3"
belongs to its owners and is used here only to identify the software this tool
indexes.
