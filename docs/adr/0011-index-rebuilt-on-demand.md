# 11. The index is rebuilt on demand, not weekly

Date: 2026-10-08

## Status

Accepted

Amends the cadence in [ADR-0005](0005-index-as-a-build-artifact.md). That
ADR's decision, that the index is a build artifact made in CI, still holds.
Only the weekly schedule has changed.

## Context

ADR-0005 rebuilt the index on a weekly cron. Two things made the schedule cost
more than it gave.

The corpus changes slowly, and a stale index is not an outage. Every answer
links to the commit it read and the page shows the index date, so an old
index is visible and its citations still resolve.

GitHub disables a scheduled workflow after 60 days without repository
activity. On a hobby project with quiet months, the cron would switch itself
off, and nobody would notice until long after the index went stale.

## Decision

`index.yml` runs only from `workflow_dispatch`. A successful run still
triggers `deploy.yml`, so rebuilding and shipping a fresh index is one manual
action.

## Consequences

The index is as fresh as the last time someone ran the workflow, and the page
footer shows that date. Nothing rebuilds the index by itself. That is a choice,
not something that can silently break.

A deploy for a code change bakes in the latest successful index rather than
building a new one, as before.

## Alternatives considered

- **Keep the weekly cron.** Fresh without effort while the repository is busy,
  but GitHub disables it after 60 quiet days, and nothing warns when it does.
- **Cron plus a keep-alive commit.** Works around the auto-disable, but adds
  commits to the history only to keep a timer running.
- **Rebuild on uc-cdis pushes.** Needs a webhook into an organisation this
  project does not belong to.
