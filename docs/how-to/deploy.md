# Deploy to production

Puts ask-gen3 on Google Cloud Run behind a public URL. About 15 minutes of setup
once, then deploys are a button. The reasoning behind this shape is
[ADR-0008](../adr/0008-cloud-run-deployment.md).

## Prerequisites

- A Google Cloud account with billing enabled. Cloud Run's free tier still
  requires a billing account attached; it just does not charge within the
  limits.
- [gcloud](https://cloud.google.com/sdk/docs/install), authenticated:
  `gcloud init`
- An OpenRouter API key
- A built `index.db` — `just index` for the full corpus, which takes about an
  hour

Docker is not needed. Cloud Build builds the image.

## 1. Create the project and its services

```sh
gcloud projects create ask-gen3 --name=ask-gen3
gcloud billing projects link ask-gen3 --billing-account=<your-billing-account-id>
gcloud config set project ask-gen3
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com
gcloud artifacts repositories create ask-gen3 \
  --repository-format=docker --location=us-central1
```

`gcloud billing accounts list` gives the billing account ID.

## 2. Store the API key

```sh
grep '^OPENROUTER_API_KEY=' .env | cut -d= -f2- | tr -d '"'"'"' ' | tr -d '\n' |
  gcloud secrets create openrouter-api-key --data-file=- --replication-policy=automatic
```

The `tr -d '\n'` matters. A trailing newline is stored verbatim and comes back
as a confusing 401 from OpenRouter. If you create the secret in the console
instead, make sure it has a *version* with a value — an empty secret fails the
deploy with `Secret ... versions/latest was not found`.

Then let the runtime service account read it:

```sh
NUM=$(gcloud projects describe ask-gen3 --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding openrouter-api-key \
  --member="serviceAccount:$NUM-compute@developer.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor
```

## 3. Build and deploy

```sh
just deploy
```

That is `gcloud builds submit` followed by `gcloud run deploy`. The first build
takes about 5 minutes; most of it is downloading the embedding weights and
pushing a ~780 MB image.

Every setting the service runs with is on that one command — the secret mount,
`--allow-unauthenticated`, and the scaling caps — so the recipe and the CI
workflow produce the same service and neither inherits console state. The cap
that matters is `--max-instances 2`: the rate limits in `server/app.py` are
per-instance counters, so the real ceiling is instances × limit, and this is
what stops an abusive client from billing you for a hundred of them.

The recipe does not scan the image, generate an SBOM or attest provenance;
only the CI workflow in step 4 does. Use the recipe to bootstrap, then deploy
through CI so every running image can be verified.

It prints the service URL. Check it:

```sh
curl https://<service>-<hash>-uc.a.run.app/health
```

## 4. Wire up CI deploys

```sh
gh variable set GCP_PROJECT --body ask-gen3
gh variable set GCP_WIF_PROVIDER --body <workload-identity-provider-resource-name>
gh variable set GCP_DEPLOY_SA --body github-deploy@ask-gen3.iam.gserviceaccount.com
```

`.github/workflows/deploy.yml` then runs automatically whenever the weekly index
build succeeds, and on demand from the Actions tab. It downloads `index.db` from
the index run, submits the build to Cloud Build, scans the image and stops on a
fixable HIGH or CRITICAL CVE, attaches an SBOM and a provenance attestation,
deploys the new revision by digest, and fails the run if `/health` does not come
back with a non-empty index. Authentication is Workload Identity Federation — no
service account key is stored in GitHub.

If the secret is named something other than `openrouter-api-key`:

```sh
gh variable set OPENROUTER_SECRET_NAME --body <name>
```

Verify what a deploy published:

```sh
gh attestation verify \
  oci://us-central1-docker.pkg.dev/ask-gen3/ask-gen3/app:<sha> \
  --repo haraprasadj/ask-gen3
```

To roll back, redeploy an earlier image:

```sh
gcloud run deploy ask-gen3 --region us-central1 \
  --image us-central1-docker.pkg.dev/ask-gen3/ask-gen3/app:<tag>
```

## What it costs

Nothing, at hobby traffic. Cloud Run's free tier is 180,000 vCPU-seconds,
360,000 GiB-seconds and 2 million requests per month — roughly 50 CPU-hours.
Artifact Registry storage for the image is about USD 0.10/month. Generation is
the only real line item — see
[ADR-0007](../adr/0007-default-model-gemini-flash-lite.md).

Set a hard spend limit on the OpenRouter key. It is the one ceiling that
concurrency, a bug, or an abusive client cannot exceed.

## If it does not work

| Symptom | Cause |
|---|---|
| `COPY failed: ... index.db: file does not exist` | `gcloud builds submit` falls back to `.gitignore` when there is no `.gitignore`-shadowing `.gcloudignore`, and `.gitignore` excludes `*.db`. Keep `.gcloudignore` in place. Also check the file exists — switching to a branch that does not track it deletes it |
| `/healthz` returns a Google 404 page | Cloud Run's frontend reserves that path and never forwards it. Use `/health`, which serves the same handler |
| `Secret ... versions/latest was not found` | The secret exists but has no version. Add one |
| `toomanyrequests: Rate exceeded` during build | A public registry rate-limiting Cloud Build. Retry |
| Answers arrive all at once, not streamed | A proxy is buffering. Cloud Run itself does not; the app sets `X-Accel-Buffering: no` |
| `/health` reports `index unavailable` | `index.db` was not baked into the image |
| First request takes 5 s | Cold start, expected. `--min-instances 1` fixes it and costs money at idle |
