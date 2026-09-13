# 6. Deploy as a Lambda container image, not a long-running container

Date: 2026-09-13

## Status

Accepted. Amends the artifact delivery mechanism of
[ADR-0005](0005-index-as-a-build-artifact.md); reaffirms
[ADR-0001](0001-sqlite-as-the-retrieval-layer.md).

## Context

[docs/architecture.md](../architecture.md) assumed a small always-on container
on Fly.io without justifying it against serverless options. Prompted to compare,
we priced AWS (figures from the AWS pricing pages, 2026-09-13, us-east-1).

An answer costs roughly 15 s of wall clock — three OpenRouter round trips plus
streaming — at 2 GB of memory, so ~30 GB-seconds. At $0.0000166667/GB-s with
the 400,000 GB-s always-free monthly allowance:

| Answers/month | Billed GB-s | Lambda | Always-on container |
|---|---|---|---|
| 1 000 | 0 | $0 | ~$2–6 |
| 10 000 | 0 | $0 | ~$2–6 |
| 20 000 | 200 000 | $3.33 | ~$2–6 |
| 50 000 | 1 100 000 | $18.33 | ~$2–6 |

The crossover is around 25–30 k answers/month. A newly launched public Q&A tool
is far below that, and traffic is bursty, which is precisely the shape
scale-to-zero bills well and an always-on box bills badly.

The counter-consideration is that Lambda bills wall-clock time, and an agent
loop is mostly idle waiting on an upstream API. That is what makes the
per-answer figure 30 GB-s rather than the ~1 GB-s of actual compute, and it is
why adding network calls to the retrieval path is expensive here in a way it
would not be on a rented box.

## Decision

Package the application as an arm64 Lambda container image with `index.db` and
the ONNX embedding weights baked into the image. Measured at 700 MB carrying a
3 MB development index, so roughly 1.05 GB once the projected 350 MB index
ships — against a 10 GB limit. Expose it through a Lambda Function URL in `RESPONSE_STREAM` invoke
mode, which carries the SSE token stream; streaming is free under 6 MB per
request and answers are orders of magnitude below that. Cloudflare stays in
front for caching, TLS and bot filtering.

Memory is set to 2 GB, above the 1769 MB threshold for a full vCPU, because
query embedding is CPU-bound.

Cold starts are absorbed rather than bought off: the embedding model is
lazy-loaded on first `search`, the SQLite connection is module-level, and an
EventBridge ping keeps one execution environment warm during the day.
Provisioned concurrency is explicitly rejected — AWS excludes functions using it
from the free tier, which would forfeit the entire saving.

## Consequences

Hosting cost is $0 at expected traffic, and OpenRouter generation becomes the
only meaningful line item — the budget in architecture.md drops to roughly
USD 10/month, essentially all of it tokens. There is no volume to manage and no
process to supervise.

Publishing an index now means building and deploying a container image, so
index updates and code deploys share one pipeline. This is a tightening of
ADR-0005, where the index was a release asset fetched at boot: rollback is now
redeploying the previous image tag rather than repointing a URL. Image storage in ECR is ~$0.11/month.

The first request after idle pays a 2–5 s cold start on top of a response that
already takes ~15 s. Acceptable for this workload; if it stops being
acceptable, the fix is a warming schedule, not provisioned concurrency.

Past ~30 k answers/month the economics invert and this ADR should be revisited.
The application stays a plain ASGI app behind an adapter, so moving back to a
container host is a deployment change, not a rewrite.

## Alternatives considered

- **S3 Vectors for retrieval** — priced at ~$0.30/month for this corpus
  (60 k vectors ≈ 150 MB: $0.009 storage, $0.12 re-ingestion, and the $2.50 per
  million query fee dominating), so cost is not the objection. It is rejected
  because it serves dense similarity only: there is no BM25, and hybrid
  retrieval exists in this design precisely because Gen3 questions are dense
  with exact identifiers (`presigned_url`, `GEN3_HOSTNAME`, `arborist`) that
  384-dimension embeddings retrieve poorly. Restoring lexical search alongside
  it means OpenSearch Serverless at a ~$175/month floor, which is two orders of
  magnitude worse than the file it would replace. It also puts a network round
  trip inside billed Lambda wall-clock time on every one of the ~3 searches per
  answer, and its deleted vectors continue to count toward index size and query
  cost for about a day.
- **CodeBuild for index builds** — the free tier is 100 build-minutes/month
  against a ~20-minute weekly build, leaving no headroom before $0.005/minute,
  plus a buildspec and an IAM role. GitHub Actions is unlimited and free for
  public repositories, and the source is already on GitHub.
- **App Runner or ECS Fargate** — a closer match to the original design, but
  both bill continuously with no scale-to-zero worth the name, at more than the
  Fly figure they would replace.
- **Fly.io with scale-to-zero** (the superseded assumption) — simpler mental
  model, no cold-start engineering, no vendor adapter; costs $2–6/month more
  than nothing and needs a volume for the index.
