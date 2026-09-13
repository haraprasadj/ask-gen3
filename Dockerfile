# Runs identically under `docker run` and as a Lambda container image: the AWS
# Lambda Web Adapter translates Function URL invocations into ordinary HTTP,
# which is what lets a plain FastAPI app stream responses on Lambda (ADR-0006).
FROM public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 AS adapter
FROM python:3.13-slim

COPY --from=adapter /lambda-adapter /opt/extensions/lambda-adapter
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /bin/uv

ENV AWS_LWA_INVOKE_MODE=response_stream \
    AWS_LWA_PORT=8000 \
    PORT=8000 \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed \
    INDEX_PATH=/app/index.db \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Runtime dependencies only. The ingest group — git, YAML, the clone and embed
# pipeline — never ships, which is the point of building the index elsewhere
# (ADR-0005).
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Fail the build, not the first request, if this base image happens to ship a
# CPython without loadable SQLite extensions (see README prerequisites).
RUN uv run --no-sync python -c "\
import sqlite3, sqlite_vec; c = sqlite3.connect(':memory:'); \
c.enable_load_extension(True); sqlite_vec.load(c); \
print('sqlite-vec', c.execute('select vec_version()').fetchone()[0])"

# Bake the embedding weights in so a cold start never reaches the network.
RUN uv run --no-sync python -c "\
from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

# Only now forbid Hugging Face network access: everything needed is on disk,
# and a cold start must never wait on hf.co.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY server/ ./server/
COPY index.db ./index.db

EXPOSE 8000
# Call the venv directly: `uv run` wants a cache dir under $HOME, and Lambda's
# $HOME is read-only.
CMD ["/app/.venv/bin/uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
