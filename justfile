# .env holds every provider setting; a recipe chooses only which provider is
# live. Note this also puts OPENROUTER_API_KEY in every recipe's environment,
# not only the two that need it.
set dotenv-load := true

name := "ask-gen3"
region := env("REGION", "us-central1")
gcp_project := env("GCP_PROJECT", "ask-gen3")
image := region + "-docker.pkg.dev/" + gcp_project + "/" + name + "/app"

default:
    @just --list

# Install dependencies, on the Python in .python-version (uv fetches it).
setup:
    uv sync
    @test -f .env || (cp .env.example .env && echo "created .env — add your OPENROUTER_API_KEY")

# Run every self-check.
test:
    uv run python -m ingest.test_schema
    uv run python -m ingest.test_chunk
    uv run python -m ingest.test_build
    uv run python -m server.test_retrieve
    uv run python -m server.test_web
    uv run python -m server.test_agent
    uv run python -m server.test_app
    uv run evals/check.py

lint:
    uvx ruff check .

fmt:
    uvx ruff format .

check: lint test

# Start a changelog entry for this change in changelog.d/ (CONTRIBUTING.md).
fragment:
    uvx scriv@1.8.0 create

# At release: fold every fragment into CHANGELOG.md under pyproject's version.
changelog:
    uvx scriv@1.8.0 collect

# Dependency CVEs and a secret scan of the history, as CI runs them. Needs docker.
audit:
    # Audits the synced environment rather than an exported requirements file:
    # same packages, and pip-audit's -r mode builds a throwaway venv to resolve
    # one, which is slow and fails on some Python builds.
    uv run --with pip-audit pip-audit --strict
    docker run --rm -v "$PWD:/repo" \
      ghcr.io/gitleaks/gitleaks:v8.28.0@sha256:cdbb7c955abce02001a9f6c9f602fb195b7fadc1e812065883f695d1eeaba854 \
      detect --source=/repo --redact --verbose

# Build the full index (~1 hour). Override with `just index "--only fence,indexd"`.
index args="":
    uv run python -m ingest.build --out index.db {{args}}

# A two-repo index for development, in about two minutes.
index-dev:
    uv run python -m ingest.build --only indexd,dictionaryutils --out index.db

# Serve locally against Ollama: OLLAMA_* in .env. Needs `ollama serve` and a
# tool-capable tag — and `ollama show <tag>` is the only honest source for how
# big that tag is, the name is not.
run:
    PROVIDER=ollama uv run uvicorn server.app:app --reload --port 8000

# Serve the way production does: OPENROUTER_* in .env, no reload.
run-prod:
    @test -f .env || (echo "no .env — run 'just setup' then add your key" && exit 1)
    PROVIDER=openrouter uv run uvicorn server.app:app --port 8000

# Build the container image locally. Needs an index.db to bake in.
docker:
    @test -f index.db || (echo "no index.db — run 'just index-dev' first" && exit 1)
    docker build -t ask-gen3 .

# Build on Cloud Build and roll out a new Cloud Run revision (docs/how-to/deploy.md).
# No CVE gate, SBOM or attestation: those run only in deploy.yml (SECURITY.md).
# Override the project the way CI does: `just gcp_project=my-project deploy`.
deploy tag="latest":
    @test -f index.db || (echo "no index.db — run 'just index' first" && exit 1)
    gcloud builds submit --tag {{image}}:{{tag}} --region={{region}} --project={{gcp_project}}
    gcloud run deploy {{name}} --region {{region}} --project {{gcp_project}} \
      --image {{image}}:{{tag}} \
      --allow-unauthenticated --ingress all \
      --max-instances 2 --concurrency 20 --cpu 1 --memory 2Gi --timeout 300 \
      --set-secrets OPENROUTER_API_KEY=openrouter-api-key:latest

# Run that image the way production does, against local Ollama.
docker-run:
    docker run --rm -p 8000:8000 \
      -e PROVIDER=ollama \
      -e OLLAMA_BASE_URL=http://host.docker.internal:11434/v1 \
      -e OLLAMA_MODEL="${OLLAMA_MODEL:-qwen3:8b}" ask-gen3
