# Contributing

1. Fork and branch off `main`.
2. `uv sync` to set up; `uv run ...` to run anything.
3. `just check` before you push — that is lint plus every self-check, and it is
   what CI runs.
4. Open a PR describing what changed and why.

`just audit` runs the security checks CI runs: `pip-audit` over the locked
dependencies and `gitleaks` over the history. It needs docker. CI runs both on
every PR, so this is only for catching them earlier.

A decision worth remembering belongs in `docs/adr/` as a numbered ADR, not in a
PR comment.

Found a vulnerability? Do not open an issue — [SECURITY.md](SECURITY.md) has the
private reporting route.

Participation is governed by our [Code of Conduct](CODE_OF_CONDUCT.md).
Contributions are accepted under the Apache License 2.0.
