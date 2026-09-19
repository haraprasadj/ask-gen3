name := "ask-gen3"
region := env("REGION", "us-central1")
gcp_project := env("GCP_PROJECT", "ask-gen3")
image := region + "-docker.pkg.dev/" + gcp_project + "/" + name + "/app"

default:
    @just --list

# Install dependencies. Needs a Python built with loadable SQLite extensions.
setup:
    uv venv --python "$(brew --prefix python@3.13)/bin/python3.13" --allow-existing
    uv sync
    @test -f .env || (cp .env.example .env && echo "created .env — add your OPENROUTER_API_KEY")

# Run every self-check.
test:
    uv run python -m ingest.test_schema
    uv run python -m ingest.test_chunk
    uv run python -m server.test_retrieve
    uv run python -m server.test_agent
    uv run python -m server.test_app
    uv run evals/check.py

lint:
    uvx ruff check .

fmt:
    uvx ruff format .

check: lint test

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

# Serve locally against Ollama. Needs `ollama serve` and a tool-capable model.
run:
    BASE_URL=http://localhost:11434/v1 MODEL=${MODEL:-qwen3.8:latest} \
      uv run uvicorn server.app:app --reload --port 8000

# Serve the way production does: OpenRouter, no reload. Reads .env.
run-prod:
    @test -f .env || (echo "no .env — run 'just setup' then add your key" && exit 1)
    uv run --env-file .env uvicorn server.app:app --port 8000

# Build the container image locally. Needs an index.db to bake in.
docker:
    @test -f index.db || (echo "no index.db — run 'just index-dev' first" && exit 1)
    docker build -t ask-gen3 .

# Build on Cloud Build and roll out a new Cloud Run revision (docs/how-to/deploy.md).
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
      -e BASE_URL=http://host.docker.internal:11434/v1 \
      -e MODEL=${MODEL:-qwen3.8:latest} ask-gen3
