# Contributing

1. Fork and branch off `main`.
2. `just setup` to install and create `.env`; `uv run ...` to run anything.
3. `just check` before you push — that is lint plus every self-check, and it is
   what CI runs.
4. If users would notice the change, `just fragment` and fill in the file it
   creates in `changelog.d/`. Don't edit CHANGELOG.md: fragments are collected
   into it at release, so pull requests never conflict there.
5. Open a PR describing what changed and why.

`just audit` runs the security checks CI runs: `pip-audit` over the locked
dependencies and `gitleaks` over the history. It needs docker. CI runs both on
every PR, so this is only for catching them earlier.

A decision worth remembering belongs in `docs/adr/` as a numbered ADR, not in a
PR comment.

Found a vulnerability? Do not open an issue — [SECURITY.md](SECURITY.md) has the
private reporting route.

Contributions are accepted under the Apache License 2.0.
