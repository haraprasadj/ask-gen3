# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Changes not yet released are fragments in `changelog.d/`, one per pull request,
which `just changelog` collects into a new version section here.

<!-- scriv-insert-here -->

<a id='changelog-0.1.0'></a>
## [0.1.0] - 2026-10-08

### Added

- Question answering over the uc-cdis Gen3 source: a streaming agent loop with
  five tools (hybrid search, grep, open_file, list_repos and an allowlisted
  `fetch_url`), answering with citations pinned to the indexed commit.
- Follow-up questions, carrying as much of the conversation as a 6 k token
  budget holds, and a "New conversation" control.
- A per-question cost ceiling of 180 k billed tokens, about USD 0.05 at the
  default model's rates
  ([ADR-0010](docs/adr/0010-a-cost-ceiling-per-question-not-a-step-count.md)).
- Two providers, chosen with `PROVIDER`: OpenRouter for hosting, Ollama for
  local work.
- A single-page UI with markdown rendering, citation links, a stop control
  and a footer naming who receives the question.
- Ingestion: a curated corpus in `ingest/repos.yaml`, format-aware chunking,
  and an index builder producing `index.db` with BM25 and vector retrieval.
  Repositories without an open licence on GitHub are skipped, and each indexed
  repository's SPDX licence is recorded.
- Abuse controls: a per-IP hourly limit keyed on the address Cloud Run
  observed, a per-instance daily cap, a wall-clock limit per answer and a
  two-instance ceiling on Cloud Run.
- Page hardening: a Content-Security-Policy with a per-response nonce,
  `Strict-Transport-Security`, `X-Content-Type-Options` and `Referrer-Policy`.
- Cloud Run deployment ([ADR-0008](docs/adr/0008-cloud-run-deployment.md)),
  with an index workflow run on demand
  ([ADR-0011](docs/adr/0011-index-rebuilt-on-demand.md)) and a deploy workflow
  that authenticates through Workload Identity Federation, gates on a Trivy
  scan and publishes an SBOM and a provenance attestation.
- Supply chain: actions pinned to commit SHAs, base images pinned to digests,
  Dependabot, and `pip-audit` and `gitleaks` on every pull request.
- Apache License 2.0, NOTICE, contributing guide, security policy,
  architecture overview, ADRs 0001–0011, and issue and pull request templates.
