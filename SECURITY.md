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

## What leaves this system, and what is kept

Your question is sent to OpenRouter, which routes it to the model named by
`OPENROUTER_MODEL` — a third party, under their terms, not ours. The page says so in the
footer. Don't put anything confidential in a question.

What this application keeps:

| | |
|---|---|
| Questions | not stored. They exist for the life of one request |
| Answers | not stored. The page holds the only copy |
| Rate-limit state | client IP and request timestamps, in memory, for one hour, in one instance. Lost on every restart |
| Outbound fetches | a GET to an allowlisted public host when the model calls `fetch_url`, carrying no question text beyond the URL it chose and no cookie or credential |
| Logs | Cloud Run request logs (IP, path, status, timing) and the exception *type* and source line of anything that broke, at Google's default retention. Question text is never logged |

There is no database of usage, no analytics, and no cookie.

## Supply chain

| Control | Where |
|---|---|
| Dependency CVEs | `pip-audit` over `uv.lock`, on every PR (`ci.yml`) |
| Secret scanning | `gitleaks` over full history, on every PR (`ci.yml`) |
| Static analysis | CodeQL, on every PR and weekly (`codeql.yml`) |
| Container CVEs | Trivy on the built image, gating the deploy on HIGH/CRITICAL (`deploy.yml`) |
| Base images | pinned by digest, not tag (`Dockerfile`) |
| Actions | pinned by commit SHA, not tag |
| Updates | Dependabot, monthly, for uv, Actions and Docker |
| Provenance | SLSA attestation binding the image digest to the workflow run, pushed to the registry |
| SBOM | SPDX, generated from the image and attached to the run |

Verify a published image before trusting it:

```
gh attestation verify oci://REGION-docker.pkg.dev/PROJECT/ask-gen3/app:TAG \
  --repo haraprasadj/ask-gen3
```

`gitleaks` rather than `detect-secrets`: no baseline file to regenerate and
re-review on every false positive, history scanning without a separate pass, and
it runs here as the upstream container pinned by digest, so no marketplace
action joins the supply chain it is meant to be checking.

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

Indexed repository content, and any page returned by `fetch_url`, is untrusted
input that reaches the model as tool output. Anyone who can land a file in a
public uc-cdis repository — or a page on an allowlisted host — can put
instructions in it, and the model may follow them. The system prompt tells the
model to report such content rather than obey it, which is a mitigation and not
a control.

What bounds the damage is that the tools still have no write path and reach no
credential: four of them read a read-only SQLite file, and the fifth makes an
outbound GET the model does not control the destination of. `fetch_url` is the
one tool that acts outside the process, so it is constrained in `server/web.py`
and tested in `server/test_web.py`:

| Control | Why |
|---|---|
| host allowlist, https only | the model picks the URL; an open fetcher reachable by prompt is server-side request forgery |
| on the GitHub hosts, the first path segment must be `uc-cdis` | the host is not the author: anyone can create a repository, and indexed repo content already reaches the model as untrusted input |
| redirects re-checked at every hop, never followed by the client | a redirect from an allowed host to `169.254.169.254` is otherwise the whole attack |
| resolved addresses must be public | an allowlisted name pointed at a private range is a mistake or an attack, and both end the same way |
| 300 kB, 10 s, text content types only | a fetch is not a way to spend the instance |
| no request body, no headers from the question, no cookies | nothing of the user's travels outbound |

The metadata server is the target that matters on Cloud Run, because it issues
the service account token; the allowlist and the address check are what keep it
unreachable. The realistic worst case remains a confidently wrong or
hostile-sounding answer, plus whatever an attacker can do with a link in the
page — which is why the escaping and the CSP above are treated as security
boundaries rather than cosmetics.
