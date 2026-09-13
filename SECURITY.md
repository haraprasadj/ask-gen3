# Security Policy

Report vulnerabilities privately to 5363831+haraprasadj@users.noreply.github.com. Please do not
open a public issue. Expect an acknowledgement within 7 days.

## Where credentials live

There is exactly one long-lived secret in this system. Everything else is
either ephemeral or federated.

| Credential | Where it lives | Notes |
|---|---|---|
| `OPENROUTER_API_KEY` | local: `.env`, gitignored — production: Lambda environment variable, encrypted at rest with KMS | the only standing secret |
| GitHub API token (index build) | not stored — the workflow uses the automatic `github.token` | scoped to one run, expires with it |
| AWS deploy credentials | not stored — GitHub Actions assumes a role via OIDC | no long-lived access keys in repository secrets |
| Hugging Face token | not needed — weights are baked into the image and `HF_HUB_OFFLINE=1` is set | |
| Local model access | not needed — Ollama takes no key | |

Rationale for the OpenRouter key living in a Lambda environment variable rather
than Secrets Manager: one secret, read once at module load. Secrets Manager
costs USD 0.40/month per secret and adds a network round trip to a cold start,
which on Lambda is billed wall-clock time (see
[ADR-0006](docs/adr/0006-lambda-container-deployment.md)). If the number of
secrets grows past a couple, or one needs automatic rotation, move to SSM
Parameter Store SecureString and cache the value across invocations.

## Practices

- Set a hard spend limit on the OpenRouter key. Application-level rate limits
  are per-instance and Lambda may run several; the provider-side limit is the
  only ceiling concurrency cannot defeat.
- The browser never holds a model key. It calls this application's `/ask`
  endpoint, which is the only thing that talks to a model provider.
- Error responses carry the exception type, never its message, so an upstream
  error cannot echo a credential back to the page.
- Rotate the key by editing the Lambda environment variable and revoking the
  old one at the provider. No redeploy is required.
- `index.db` contains only public repository content. It is not a secret and
  is safe to publish as a build artifact.
