# 5. The index is a build artifact, rebuilt weekly in CI

Date: 2026-09-12

## Status

Accepted. Delivery mechanism amended by [ADR-0006](0006-lambda-container-deployment.md):
the index ships inside the Lambda container image rather than as a release asset
fetched at boot. The build pipeline and weekly cadence below are unchanged.

## Context

Source repositories change continuously, but a question-answering tool for a
platform's documentation does not need minute-level freshness; it needs to be
correct about what it does say, and to link to the exact commit it read.

Building in CI means the serving container never needs `git`, the 3.5 GB of
checkouts, or the memory to embed 60 k chunks.

## Decision

A scheduled GitHub Actions workflow shallow-clones the repos listed in
`repos.yaml`, builds `index.db`, records each repo's commit SHA in a manifest
table, and publishes the file as a release asset. The server downloads it at
boot to its volume and serves it read-only; a manual workflow dispatch plus
restart is the way to force a refresh.

Deployment of a new index is therefore a restart, and rollback is pointing at
the previous release asset.

## Consequences

Answers can be stale by up to a week, and the UI states the index date and
links citations to the pinned commit rather than to `main`, so a reader can see
exactly what was read.

The whole index is rebuilt each time; at this corpus size incremental updates
by commit SHA would add bookkeeping for minutes of savings. Revisit if build
time becomes painful.

*Update 2026-09-13:* the original "~20 minutes" estimate was optimistic. A
measured two-repo build embedded ~930 chunks in about a minute on a laptop,
which extrapolates to roughly an hour for the projected 60 k. Embedding, not
cloning, is the cost. This does not change the decision — GitHub Actions is
unlimited for public repositories — but the workflow sets a 180-minute timeout
and the first full build should be measured rather than assumed.

A release asset has a 2 GB limit, which the ~350 MB index is well inside. If
the corpus grows past that, move the artifact to object storage (R2 has a free
tier) without changing anything else.

## Alternatives considered

- **Build the index inside the serving container at boot** — no artifact
  plumbing, but every deploy re-does an hour of work on the smallest machine
  in the stack, and a transient GitHub failure yields a half-built index.
- **Live GitHub API search at query time** — always fresh and no index at all,
  but rate-limited, slow per tool call, and unable to do dense retrieval.
- **Incremental updates on repository webhooks** — near-real-time freshness;
  disproportionate machinery for a weekly-enough corpus, and it needs a
  writable index, which ADR-0001 rules out at serving time.
