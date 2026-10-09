# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Changes not yet released are fragments in `changelog.d/`, one per pull request,
which `just changelog` collects into a new version section here.

<!-- scriv-insert-here -->

<a id='changelog-0.3.0'></a>
## [0.3.0] - 2026-10-09

### Added

- `just fetch-index` downloads the prebuilt full index, so running against the
  whole corpus no longer means building it first.

### Changed

- CodeQL runs on every pull request, on pushes to `main` and weekly again,
  as SECURITY.md says. Its triggers were removed while code scanning was
  unavailable on the private repository.
- The overview is a Markdown page, `docs/overview.md`, that GitHub renders,
  instead of an HTML slide deck you had to open from a clone. Its figures
  now match the current index and deployment.
- The index workflow publishes `index.db` as the asset of the `index` release
  instead of a workflow artifact, and deploys fetch it from there
  ([ADR-0012](docs/adr/0012-index-published-as-a-release-asset.md)). The
  artifact expired after 30 days, after which every deploy failed. The deploy
  workflow's `index_run_id` input is gone; to deploy an earlier index,
  redeploy an earlier image.

### Fixed

- The first deploy from the public repository failed at the provenance
  attestation with "No credentials found for registry". The deploy now logs
  in to Artifact Registry with a stored, short-lived token, which the
  attestation's push can use, instead of a gcloud credential helper, which
  it cannot.

<a id='changelog-0.2.0'></a>
## [0.2.0] - 2026-10-08

### Changed

- **Breaking:** `PROVIDER` picks `local` or `hosted` instead of `ollama` or
  `openrouter`, and each slot reads `<SLOT>_INFERENCE_URL`, `_MODEL` and
  `_API_KEY`. Either slot takes any OpenAI-compatible endpoint; local still
  defaults to Ollama and hosted to OpenRouter. Rename the variables in an
  existing `.env`: `OLLAMA_BASE_URL` → `LOCAL_INFERENCE_URL`, `OLLAMA_MODEL` →
  `LOCAL_INFERENCE_MODEL`, `OPENROUTER_API_KEY` → `HOSTED_INFERENCE_API_KEY`,
  `OPENROUTER_BASE_URL` → `HOSTED_INFERENCE_URL`, `OPENROUTER_MODEL` →
  `HOSTED_INFERENCE_MODEL`.
- Deploys set the hosted URL and model from the `HOSTED_INFERENCE_URL` and
  `HOSTED_INFERENCE_MODEL` repository variables, so production can move to
  another endpoint without a code change. The `OPENROUTER_SECRET_NAME`
  variable is now `HOSTED_INFERENCE_SECRET_NAME`.
- The page footer names the host questions are sent to, taken from the
  configured URL, and OpenRouter's attribution headers go only to OpenRouter.
- Deploys skip the provenance attestation while the repository is private.
  GitHub stores none for a private personal repository, and the failed step
  blocked every deploy. It runs again as soon as the repository is public;
  images deployed before then cannot be verified with `gh attestation verify`.

### Removed

- The Gen3 logo from the page header, with its NOTICE attribution. The page
  carries the project name only.

### Fixed

- The deploy guide's service account setup was missing the roles Cloud Build
  needs to accept a source upload, so the first CI deploy failed with
  "forbidden from accessing the bucket". It now grants
  `serviceusage.serviceUsageConsumer` and `storage.bucketViewer` on the
  project, and `storage.objectAdmin` on the Cloud Build bucket.
- The deploy workflow failed after a successful image build, because
  streaming Cloud Build's logs needs project-wide Viewer. It now submits the
  build asynchronously and polls its status; the logs stay in the console.

### Security

- The container image moves to the current `python:3.13-slim`, picking up
  Debian fixes for perl, OpenSSL, SQLite, PCRE2 and gzip, and no longer ships
  `pip`, whose bundled urllib3, msgpack and setuptools carried known
  vulnerabilities. The app is installed and run with uv and never used it.

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
