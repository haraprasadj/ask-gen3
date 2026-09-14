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
    uv run evals/check.py

lint:
    uvx ruff check .

fmt:
    uvx ruff format .

check: lint test

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
deploy tag="latest":
    @test -f index.db || (echo "no index.db — run 'just index' first" && exit 1)
    gcloud builds submit --tag us-central1-docker.pkg.dev/ask-gen3/ask-gen3/app:{{tag}} --region=us-central1
    gcloud run deploy ask-gen3 --region us-central1 \
      --image us-central1-docker.pkg.dev/ask-gen3/ask-gen3/app:{{tag}}

# Run that image the way production does, against local Ollama.
docker-run:
    docker run --rm -p 8000:8000 \
      -e BASE_URL=http://host.docker.internal:11434/v1 \
      -e MODEL=${MODEL:-qwen3.8:latest} ask-gen3
