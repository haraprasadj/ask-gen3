# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Apache License 2.0, contributing guide and security policy.
- Architecture overview and ADRs 0001–0006.
- Ingestion: curated corpus in `ingest/repos.yaml`, format-aware chunking, and
  an index builder producing `index.db` with hybrid BM25 and vector retrieval.
- Server: streaming agent loop over four retrieval tools, SSE endpoint and a
  single-page UI with per-IP rate limiting and a daily cap.
- Conversations: follow-up questions carry as much prior history as a 6 k token
  budget holds, held by the page and posted to `/ask`, with a "New
  conversation" control to clear them.
- `fetch_url` tool reading allowlisted public pages over https, with redirect
  re-checking and a private-address refusal (ADR-0009).
- UI: Gen3 logo, markdown rendering for answers (headings, tables, lists, code,
  citation chips) and a stop control on the streaming request.
- Container image, CI workflow, and an index-build workflow run by hand.
- Cloud Run deployment (ADR-0008) and a `deploy` workflow that ships each
  index build via GitHub OIDC.
- `server/test_app.py`: self-check for rate limiting, the CSP nonce and the
  renderer's escaping, the last of which runs an injection payload through the
  real renderer under node.
- Content-Security-Policy with a per-response nonce, `X-Content-Type-Options`
  and `Referrer-Policy` on the page.
- `REQUEST_TIMEOUT_SECONDS` (default 120) bounding one answer.
- `NOTICE` covering the Gen3 wordmark and the licences of indexed content.
- Dependabot for GitHub Actions, uv dependencies and Docker base images.
- `audit` CI job and `just audit`: `pip-audit` over the locked dependency set
  and `gitleaks` over the full history, on every pull request.
- CodeQL static analysis on every pull request and weekly.
- Trivy scan of the built image, gating the deploy on fixable HIGH and CRITICAL
  findings.
- SPDX SBOM and a SLSA provenance attestation for each deployed image, pushed
  to the registry and verifiable with `gh attestation verify`.
- `Strict-Transport-Security` on the page; Cloud Run does not send it.
- Footer and README disclosure that questions are sent to OpenRouter, and a
  `SECURITY.md` section stating what is kept and for how long.
- Code of Conduct (Contributor Covenant 2.1), `CODEOWNERS`, and issue and pull
  request templates.

### Changed

- **Breaking:** `/ask` is a POST taking a JSON body, not a GET with query
  parameters; the body carries the prior conversation.
- **Breaking:** `MODEL` and `BASE_URL` are replaced by `PROVIDER` selecting
  `ollama` or `openrouter`, each owning its own `*_BASE_URL`, `*_MODEL` and
  `*_API_KEY` in `.env`. An existing `.env` needs updating; no model or URL is
  hardcoded in the justfile.
- Cost ceiling ([ADR-0010](docs/adr/0010-a-cost-ceiling-per-question-not-a-step-count.md)): a question stops at 180 k billed tokens (~USD 0.05 at
  flash-lite rates) counted across every call, replacing the 6-step and 25 k
  prompt limits. `usage.prompt_tokens` now reports the billed sum rather than
  the last call's prompt.
- UI: the composer sits below the transcript, chat style.
- UI: the question box is a growing multi-line field (Enter sends, Shift+Enter
  for a newline), and a question may be 2,000 characters rather than 600.
- The container runs as `nobody` rather than root.
- GitHub Actions are pinned to commit SHAs instead of floating tags, and base
  images to digests for the same reason.
- **Security:** Cloud Run deploys now set `--max-instances 2` and the rest of
  the service configuration explicitly. The rate limits in `server/app.py` are
  per-instance, so the previous default of 100 instances made the real ceiling
  100× the intended one, with nothing but the OpenRouter spend cap below it.
- Deploys pin the image by digest and carry the secret mount and ingress
  settings on the command, so no part of the running configuration lives only
  in console state.
- `just deploy` takes the GCP project, region and name as overridable
  variables instead of hardcoding one project.
- Abuse-control documentation now describes the controls that exist, rather
  than a planned Cloudflare, answer cache and request-logging setup that was
  never built.

- CI runs `just check` and `just audit` instead of its own copy of their
  commands. The copy had drifted: `server.test_web`, the `fetch_url` SSRF
  checks, and the evals file check never ran in CI.
- `just setup` no longer needs Homebrew: it is `uv sync` on uv's own Python
  3.13, whose current builds load SQLite extensions. It works on Linux too.

### Removed

- The 6-step and 25,000-character prompt limits, and the two hand-synced
  12,000-character tool-result caps, all superseded by the single token ceiling
  ([ADR-0010](docs/adr/0010-a-cost-ceiling-per-question-not-a-step-count.md))
  and by `MAX_TOOL_RESULT_TOKENS` applied in one place for every tool.

### Fixed

- **Security:** `fetch_url` read a response in full before trimming it to
  300 kB, so the cap bounded what reached the model but not memory: one fetch
  of a very large allowlisted file could exhaust the instance. The body is now
  streamed and reading stops at the cap.
- **Security:** an unhandled error in `/ask` logged the exception's message,
  which can carry the question or an upstream response body, while SECURITY.md
  promised only its type. It now logs the type and the line that raised it.
- `just test` crashed on a fresh clone: `evals/check.py` opened `index.db`
  unconditionally. Citations are now checked only for repositories the index
  holds, and the rest are counted as skipped, so it passes with no index, the
  two-repo `just index-dev` build, or the full one.
- Requests to OpenRouter sent `HTTP-Referer: https://github.com/uc-cdis` by
  default, attributing this project's traffic to the Gen3 organisation. The
  default is now this repository, and `PUBLIC_URL` is documented.
- `server/test_app.py` never ran two of its checks, the `/ask` stream framing
  and the malformed-body cases: its hand-kept call list had missed them. It now
  runs every `test_*` function, as the other self-checks do.
- SECURITY.md presented the image CVE gate, SBOM and provenance attestation
  as covering every deployed image. They run only in `deploy.yml`; it now says
  `just deploy` ships without them, as do the deploy guide and the recipe.
- A citation written without its org, `[fence/fence/x.py#L1-L5]`, linked to
  `github.com/fence/fence/...`, a 404. The org is now optional in the marker
  and always `uc-cdis` in the link.

- **Security:** the answer renderer escaped `< > &` but not quotes, while
  interpolating model output into `href` attributes — repository content, which
  the model reads, could break out into an event handler and run script.
- **Security:** rate limiting keyed on `cf-connecting-ip` and the leftmost
  `X-Forwarded-For` entry, both set by the caller, so the per-IP limit and with
  it the spend ceiling could be bypassed with one header. It now uses the
  address the Cloud Run frontend observed.
- Rate-limit bucket eviction dropped every window at 10 k entries, which let a
  flood of throwaway keys reset limits for real callers; only expired windows
  are dropped now.
