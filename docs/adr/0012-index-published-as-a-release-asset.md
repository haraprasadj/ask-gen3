# 12. The index is published as a release asset

Date: 2026-10-09

## Status

Accepted

Amends how [ADR-0005](0005-index-as-a-build-artifact.md)'s artifact is handed
on. The index still ships inside the image
([ADR-0008](0008-cloud-run-deployment.md)); this decision is about where the
deploy, and anyone running ask-gen3 locally, gets it from.

## Context

The index workflow uploaded `index.db` as a workflow artifact, and the deploy
workflow downloaded it from the latest successful run. Workflow artifacts
expire, after 30 days here, and the index is rebuilt by hand
([ADR-0011](0011-index-rebuilt-on-demand.md)). A month without a rebuild left
the deploy with no index to bake in, so every deploy, including one for a
code change alone, would fail.

Nobody outside the project could use the artifact either. Downloading one
needs a signed-in GitHub client, so running ask-gen3 against the full corpus
meant building the index first, an hour or more.

Committing `index.db` to the repository would solve both, at a cost. It is a
61 MB binary that changes almost entirely on every rebuild, so each rebuild
would add a full copy to the history every clone downloads.

## Decision

The index workflow publishes `index.db` as the asset of one release with the
fixed tag `index`, replacing the asset on every run. The release is never
marked latest, so versioned releases keep that place. The build job stays
read-only; a separate publish job holds `contents: write`.

The deploy workflow downloads the asset from that release. `just fetch-index`
downloads it from its fixed URL, which needs no GitHub client or sign-in.

## Consequences

The index no longer expires, and a deploy works however long ago the last
rebuild was.

Only the current index is published. To go back to an earlier one, redeploy an
earlier image, which still contains the index it was built with.

The `index` tag points at the commit of the first publish and does not move.
The release notes record when the current asset was built and what it holds.

Replacing the asset needs releases to stay mutable. If immutable releases are
ever enabled for the repository, this has to become a release per build.

Release assets are limited to 2 GB each, far above the current 61 MB. If the
index outgrows that, move it to object storage, as ADR-0005 already notes.

## Alternatives considered

- **Commit `index.db` to the repository.** Simple to fetch, but the history
  grows by the full file on every rebuild, GitHub rejects files over 100 MB,
  and Git LFS's free tier allows 1 GB of downloads a month.
- **A dated release per build.** Keeps every index, but there is no fixed
  download URL, old indexes are already kept in old images, and the release
  list fills up with builds.
- **Keep the workflow artifact with the maximum 90-day retention.** Delays the
  expiry rather than removing it, and still can't be downloaded without a
  signed-in client.
