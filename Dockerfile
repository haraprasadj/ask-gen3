# A plain container: uvicorn on $PORT. Runs the same under `docker run` and on
# Cloud Run, which streams server-sent events without any adapter (ADR-0008).
#
# Both images are pinned by digest, not tag, for the same reason the workflows
# pin actions by commit SHA: a tag can be moved onto other code. Dependabot's
# docker ecosystem moves these forward.
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

COPY --from=ghcr.io/astral-sh/uv:0.9.7@sha256:ba4857bf2a068e9bc0e64eed8563b065908a4cd6bfb66b531a9c424c8e25e142 /uv /bin/uv

ENV PORT=8000 \
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

# Bake the embedding weights in so a cold start never reaches the network. The
# download writes some of the cache 0600, which the unprivileged runtime user
# cannot read; it degrades to a slower path with a warning instead of failing,
# so make the whole cache world-readable while we are still root.
RUN uv run --no-sync python -c "\
from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')" \
 && chmod -R a+rX /opt/fastembed

# Only now forbid Hugging Face network access: everything needed is on disk,
# and a cold start must never wait on hf.co.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY server/ ./server/
COPY index.db ./index.db

EXPOSE 8000
# Nothing in the image is written at runtime — the index is opened read-only and
# the weights are already on disk — so the process has no reason to be root.
USER nobody

# Call the venv directly: `uv run` wants a writable cache dir under $HOME, which
# a read-only container filesystem does not provide.
CMD ["/app/.venv/bin/uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
