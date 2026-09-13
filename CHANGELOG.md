# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Apache License 2.0 and open source project scaffolding.
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
