# Security Policy

Report vulnerabilities privately to 5363831+haraprasadj@users.noreply.github.com. Please do not
open a public issue. Expect an acknowledgement within 7 days.

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
