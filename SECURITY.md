# Security Policy

Report vulnerabilities through GitHub's private vulnerability reporting:
**Security → Report a vulnerability** on
<https://github.com/haraprasadj/ask-gen3/security/advisories/new>. Please do not
open a public issue. Expect an acknowledgement within 7 days.

This is a hobby project with one maintainer and no SLA beyond that.

## Where credentials live

There is exactly one long-lived secret in this system. Everything else is
either ephemeral or federated.

| Credential | Where it lives | Notes |
|---|---|---|
| `OPENROUTER_API_KEY` | local: `.env`, gitignored — production: Google Secret Manager, mounted into the Cloud Run revision at start | the only standing secret |
| GitHub API token (index build) | not stored — the workflow uses the automatic `github.token` | scoped to one run, expires with it |
| GCP deploy credentials | not stored — GitHub Actions uses Workload Identity Federation | no service account key in repository secrets |
| Hugging Face token | not needed — weights are baked into the image and `HF_HUB_OFFLINE=1` is set | |
| Local model access | not needed — Ollama takes no key | |

The OpenRouter key is stored in Secret Manager and mounted as an environment
variable in the Cloud Run revision, rather than set as a plain environment
variable on the service. A plain variable is readable by anyone who can describe
the service; a secret needs `secretmanager.secretAccessor`, granted only to the
runtime service account. The first two Secret Manager versions are free and
access calls are billed per 10,000, so this costs nothing at one secret read per
cold start.

Rotating the key means adding a new secret version and redeploying; the revision
pins `:latest` at start, so a running revision keeps the value it booted with.

## Practices

- Set a hard spend limit on the OpenRouter key. Application-level rate limits
  are per-instance and Cloud Run may run several; the provider-side limit is
  the only ceiling concurrency cannot defeat.
- The browser never holds a model key. It calls this application's `/ask`
  endpoint, which is the only thing that talks to a model provider.
- Error responses carry the exception type, never its message, so an upstream
  error cannot echo a credential back to the page.
- Rotate the key by adding a Secret Manager version and revoking the old one at
  the provider. A running revision keeps the value it booted with, so a rotation
  takes effect on the next deploy or cold start.
- `index.db` contains only public repository content. It is not a secret and
  is safe to publish as a build artifact.
- Rate limits key on the address the Cloud Run frontend observed — the rightmost
  `X-Forwarded-For` entry. Anything to its left is supplied by the caller, so
  reading it would make the limit opt-in. If you put a CDN in front of this,
  that assumption changes and `client_ip()` has to change with it.
- The page is served with a per-response CSP nonce, so injected markup cannot
  execute even if the renderer's escaping is wrong. The renderer escapes quotes
  as well as angle brackets, because model output is interpolated into `href`
  attributes; `server/test_app.py` runs an injection payload through the real
  renderer under node to keep it that way.
- The container runs as `nobody` and one answer is capped at
  `REQUEST_TIMEOUT_SECONDS` of wall-clock, which is billed.
- GitHub Actions are pinned to commit SHAs, not tags, and Dependabot moves them.

## Known limitation: prompt injection

Indexed repository content is untrusted input that reaches the model as tool
output. Anyone who can land a file in a public uc-cdis repository can put
instructions in it, and the model may follow them. The system prompt tells the
model to report such content rather than obey it, which is a mitigation and not
a control.

What bounds the damage is that the tools have no side effects: they read a
read-only SQLite file and nothing else. There is no credential the model can
reach, no write path, and no request it can make on the user's behalf. The
realistic worst case is a confidently wrong or hostile-sounding answer, plus
whatever an attacker can do with a link in the page — which is why the escaping
and the CSP above are treated as security boundaries rather than cosmetics.
