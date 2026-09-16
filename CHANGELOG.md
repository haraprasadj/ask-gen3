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
- UI: Gen3 logo, markdown rendering for answers (headings, tables, lists, code,
  citation chips) and a stop control on the streaming request.
- Lambda container image, CI workflow, and a weekly index-build workflow.
- Deployment: `deploy/bootstrap.sh` for the one-time AWS resources and a
  `deploy` workflow that ships each index build via GitHub OIDC.
- Cloud Run deployment (ADR-0008) replacing the Lambda path.
- `server/test_app.py`: self-check for rate limiting, the CSP nonce and the
  renderer's escaping, the last of which runs an injection payload through the
  real renderer under node.
- Content-Security-Policy with a per-response nonce, `X-Content-Type-Options`
  and `Referrer-Policy` on the page.
- `REQUEST_TIMEOUT_SECONDS` (default 120) bounding one answer.
- `NOTICE` covering the Gen3 wordmark and the licences of indexed content.
- Dependabot for GitHub Actions and uv dependencies.

### Fixed

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

### Changed

- The container runs as `nobody` rather than root.
- GitHub Actions are pinned to commit SHAs instead of floating tags.
- `just deploy` takes the GCP project, region and name as overridable
  variables instead of hardcoding one project.
- Abuse-control documentation now describes the controls that exist, rather
  than a planned Cloudflare, answer cache and request-logging setup that was
  never built.
