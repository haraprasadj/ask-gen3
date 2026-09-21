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

Prerequisites: [uv](https://docs.astral.sh/uv/getting-started/installation/),
[just](https://github.com/casey/just#installation), and a Python built with
loadable SQLite extensions — retrieval needs `sqlite-vec`. Several CPython
distributions on macOS, including uv's own managed builds, are compiled without
it and fail with `'sqlite3.Connection' object has no attribute
'enable_load_extension'`. Homebrew's Python works:

```sh
brew install python@3.13
just setup
just test
```

## Quickstart

Build a small index from two repositories, then ask it something:

```sh
just index-dev
```

Answers need a model. Locally, point at [Ollama](https://ollama.com) with any
tool-capable model:

```sh
ollama pull qwen3:8b
just run
```

Any tool-capable tag works; set `OLLAMA_MODEL` in `.env` to pick a different one.

Open <http://localhost:8000>. For the hosted configuration, set
`OPENROUTER_API_KEY` and use `just run-prod`.

## Usage

```sh
just                      # list recipes
just index                # full corpus, about an hour
just index "--only fence" # one repository
just check                # lint and every self-check
just deploy               # build on Cloud Build, deploy a Cloud Run revision
```

Configuration is environment variables, read from `.env`. Each provider owns
its own base URL, model and key, so switching between them cannot half-apply:

| Variable | Default | Purpose |
|---|---|---|
| `PROVIDER` | `openrouter` | `ollama` or `openrouter`; picks which triple below is live. `just run` forces `ollama`, `just run-prod` forces `openrouter` |
| `OPENROUTER_API_KEY` | — | required when the provider is `openrouter` |
| `OPENROUTER_MODEL` | `google/gemini-3.1-flash-lite` | any tool-capable model; benchmarked in [ADR-0007](docs/adr/0007-default-model-gemini-flash-lite.md). `openrouter/free` costs nothing but varies in quality |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | |
| `OLLAMA_MODEL` | `qwen3:8b` | the tag `just run` serves. A tag name says nothing about size — `ollama show <tag>` does |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | |
| `INDEX_PATH` | `index.db` | where the index lives |
| `RATE_LIMIT_PER_HOUR` | `20` | per-IP question limit |
| `DAILY_QUESTION_CAP` | `2000` | per-instance daily ceiling |
| `REQUEST_TIMEOUT_SECONDS` | `120` | wall-clock ceiling on one answer |

`just setup` creates a gitignored `.env` from `.env.example`; every recipe
loads it. Set a hard spend limit on the OpenRouter key as well — it is the only
budget ceiling that concurrency cannot exceed. See [SECURITY.md](SECURITY.md)
for where every credential lives.

Questions are sent to OpenRouter and the model it routes to, which are third
parties. Nothing is stored: no question, no answer, no analytics, no cookie.
[SECURITY.md](SECURITY.md) has the full list of what leaves and what is kept.

## How it works

Two halves that share only a file. A weekly GitHub Actions job clones the
curated repositories, chunks them, embeds locally, and produces `index.db`;
the server opens it read-only and never talks to a database, vector store or
embedding API.

- [Architecture](docs/architecture.md) — components, corpus selection, cost,
  abuse controls
- [Deploying](docs/how-to/deploy.md) — Cloud Run, Cloud Build and the CI deploy path
- [Decision records](docs/adr/) — why SQLite, why local embeddings, why
  OpenRouter, why no framework, why Cloud Run, why a built-in fetch tool

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). Indexed
content remains under the licences of the repositories it came from.

Not affiliated with, endorsed by, or sponsored by the Gen3 project, the
University of Chicago or the Center for Translational Data Science. "Gen3" and
the Gen3 logo belong to their owners and are used here only to identify the
software this tool indexes.
