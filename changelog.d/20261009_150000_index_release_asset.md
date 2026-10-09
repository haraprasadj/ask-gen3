### Added

- `just fetch-index` downloads the prebuilt full index, so running against the
  whole corpus no longer means building it first.

### Changed

- The index workflow publishes `index.db` as the asset of the `index` release
  instead of a workflow artifact, and deploys fetch it from there
  ([ADR-0012](docs/adr/0012-index-published-as-a-release-asset.md)). The
  artifact expired after 30 days, after which every deploy failed. The deploy
  workflow's `index_run_id` input is gone; to deploy an earlier index,
  redeploy an earlier image.
