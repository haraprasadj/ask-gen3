# 8. Deploy on Cloud Run, not Lambda

Date: 2026-09-14

## Status

Accepted. Supersedes [ADR-0006](0006-lambda-container-deployment.md); reaffirms
[ADR-0005](0005-index-as-a-build-artifact.md) and
[ADR-0001](0001-sqlite-as-the-retrieval-layer.md).

## Context

ADR-0006 chose Lambda on economics that still hold: this workload is bursty and
mostly idle, so scale-to-zero bills well and an always-on box bills badly. It
did not hold on availability. The AWS account could not serve the function
publicly.

The function itself was healthy — `lambda invoke` returned 200 with a correct
`/healthz` body — but every request to its Function URL returned 403
`AccessDeniedException`, for anonymous and SigV4-signed callers alike, with no
CloudWatch entry to show the request had arrived. The resource policy was the
documented public one (`Principal: "*"`, `lambda:InvokeFunctionUrl`, condition
`lambda:FunctionUrlAuthType=NONE`) and matched the URL's `AuthType NONE`.
Removing and re-adding the permission changed nothing over twelve hours. The
concurrency quota had risen from 10 to 1000 in that window, which suggests a
new-account restriction that lifted for quotas but not for Function URL
invocation. Resolving it required an AWS support case of unknown duration.

A Hugging Face Space was tried as a fallback and failed differently: the Space
built and pushed, then refused to start with `Quota exceeded for flavor
cpu-basic (requested=1): current=0, limit=0`. Two account-level gates, neither
fixable in the repository.

## Decision

Deploy the same container on Google Cloud Run, in `us-central1`, with
`index.db` and the ONNX weights still baked into the image (ADR-0005 unchanged).
1 GiB of memory, 1 vCPU, `--max-instances 3`, scale to zero, public via
`--allow-unauthenticated`, and `OPENROUTER_API_KEY` mounted from Secret Manager
rather than set as a plain environment variable — an improvement on the Lambda
setup, where the key was readable via `lambda:GetFunction`.

Images are built by Cloud Build rather than locally. Cloud Run is x86-only, the
development machines are arm64, and cross-building through QEMU is roughly 10x
slower on the fastembed weight-download step.

The AWS Lambda Web Adapter is removed from the image. It was only ever loaded by
the Lambda runtime; on Cloud Run it was an unused binary that still cost a pull
from `public.ecr.aws`, which rate-limited a build.

## Consequences

Hosting stays at $0 for this traffic. Cloud Run's free tier is 180,000 vCPU-s,
360,000 GiB-s and 2M requests per month — about 50 CPU-hours, far above expected
use. Artifact Registry storage for the ~780 MB image is roughly $0.10/month, in
place of ECR's $0.11. OpenRouter generation remains the only real line item.

The health endpoint is served at both `/healthz` and `/health`. Cloud Run's
frontend reserves `/healthz` and returns its own 404 without forwarding the
request to the container; this is invisible in the container logs, because the
request never arrives.

Cold starts remain 2–5 s and are still absorbed rather than bought off; the
Cloud Run equivalent of provisioned concurrency is `--min-instances 1`, which
costs real money at idle and is rejected for the same reason.

Rollback is redeploying a previous image tag, unchanged from ADR-0006.

All AWS resources for this project were deleted: the function, its Function URL,
the ECR repository and image, both IAM roles, the CloudWatch log group and two
leftover S3 buckets. Month-to-date AWS cost for the project is $0.

## Alternatives considered

- **Wait for AWS support** — the ADR-0006 design was sound and the account
  restriction may lift on its own. Rejected because the wait is unbounded and
  the port was a day's work; ADR-0006 remains in the repository if the economics
  or the account state ever argue for going back.
- **Fly.io** — no free compute tier as of 2026-09-14. A 1 GB `shared-cpu-1x` is
  $5.70/month always-on in the cheapest region, or roughly $0.50–1/month with
  autostop, since stopped machines still pay $0.15/GB/month for their root
  filesystem. Cheap, but not free, and it needs a `fly.toml` that Cloud Run does
  not.
- **Render free tier** — 512 MB of memory, which is tight for the ONNX embedder
  plus sqlite-vec, with a slow wake from spin-down. Likely to OOM under the very
  workload it would be hosting.
- **A rented VPS (Hetzner, ~EUR 4/month)** — always on, no cold start, and the
  simplest mental model. Rejected because it reintroduces a machine to patch and
  supervise, which is what ADR-0006 was right to avoid.
- **Keeping the Lambda Web Adapter in the image** — would leave the image
  dual-target. Rejected: it is dead weight on Cloud Run, it cost a build
  failure, and re-adding it is a two-line change documented in ADR-0006.
