# Deploy to production

Puts ask-gen3 on AWS Lambda behind a public URL. Roughly 20 minutes of setup
once, then deploys are a button. The reasoning behind this shape is
[ADR-0006](../adr/0006-lambda-container-deployment.md).

## Prerequisites

- An AWS account, and `aws` authenticated: `aws login`
- Docker, for the first image push
- `.env` with a working `OPENROUTER_API_KEY`
- A built `index.db` — `just index` for the full corpus, which takes about an
  hour

## 1. Build and push the first image

Bootstrap needs an image to point the function at.

```sh
just index          # ~1 hour; `just index-dev` for a 2-repo smoke test
just docker
aws ecr create-repository --repository-name ask-gen3 >/dev/null
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR=$ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/ask-gen3
aws ecr get-login-password | docker login --username AWS --password-stdin "$ECR"
docker tag ask-gen3 "$ECR:bootstrap" && docker push "$ECR:bootstrap"
```

Build the image on the machine that will run it, or with `docker build
--platform linux/arm64` — the function is arm64.

## 2. Bootstrap the AWS resources

```sh
./deploy/bootstrap.sh
```

Idempotent, so re-run it freely. It creates the ECR repository with a
keep-the-last-3 lifecycle rule, the Lambda execution role, the function itself
(arm64, 2 GB, 120 s timeout), a public Function URL in `RESPONSE_STREAM` mode,
and the GitHub OIDC role that lets CI deploy without any stored AWS key. It
reads `OPENROUTER_API_KEY` from `.env` and sets it as an encrypted Lambda
environment variable without printing it.

Override the defaults with environment variables: `REGION`, `NAME`, `MEMORY`,
and `REPO` — set `REPO` to your own `owner/repo`, or the OIDC trust policy will
name the wrong repository.

It prints your URL. Check it:

```sh
curl https://<id>.lambda-url.us-east-1.on.aws/healthz
```

## 3. Wire up CI deploys

```sh
gh variable set AWS_ROLE_ARN --body arn:aws:iam::<account>:role/ask-gen3-deploy
gh variable set AWS_REGION --body us-east-1
```

`.github/workflows/deploy.yml` then runs automatically whenever the weekly
index build succeeds, and on demand from the Actions tab. It downloads
`index.db` from the index run, builds the image on a native arm64 runner, pushes
it, updates the function, and fails the run if `/healthz` does not come back
with a non-empty index.

To roll back, redeploy an earlier image:

```sh
aws lambda update-function-code --function-name ask-gen3 --image-uri "$ECR:<sha>"
```

## 4. Put a CDN in front (optional)

The Function URL works as-is, on an ugly hostname, with no caching. For a
custom domain, know this first: **a plain Cloudflare CNAME to a Function URL
returns 403.** Lambda validates the `Host` header against its own hostname, and
Cloudflare forwards yours. The fixes, cheapest first:

- A Cloudflare Transform Rule (or a Worker) that rewrites the `Host` header to
  the `*.lambda-url.*.on.aws` origin. Free.
- CloudFront with an origin access control and your ACM certificate. Also
  handles streaming, costs pennies at this traffic, and is more moving parts.

Whichever you choose, keep `cf-connecting-ip` or `x-forwarded-for` reaching the
app — `client_ip()` reads them in that order, and per-IP rate limiting silently
degrades to one global bucket without them.

## What it costs

Nothing, at hobby traffic. The 400,000 GB-s monthly Lambda free tier covers
roughly 13,000 answers; ECR storage for three ~1 GB images is about USD 0.11.
Generation is the only real line item — see
[ADR-0007](../adr/0007-default-model-gemini-flash-lite.md).

Set a hard spend limit on the OpenRouter key. It is the one ceiling that
concurrency, a bug, or an abusive client cannot exceed.

## If it does not work

| Symptom | Cause |
|---|---|
| 403 from a custom domain | `Host` header, see above |
| Answers arrive all at once, not streamed | Function URL is not in `RESPONSE_STREAM` mode, or a proxy is buffering |
| `healthz` reports `index unavailable` | `index.db` was not baked into the image |
| First request takes 5 s | Cold start, expected. A warming EventBridge rule is the fix, not provisioned concurrency — that forfeits the free tier |
| `Runtime.InvalidEntrypoint` | Image built for amd64; rebuild with `--platform linux/arm64` |
