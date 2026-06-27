# API image (Feature 018). Build context = repository ROOT (per-package uv.lock with editable path
# deps needs the sibling sources). Dependency closure: api → {db, probability}; probability →
# {db, eval}; eval → {db}. features/serving/training are NOT needed for serving and are excluded.
# Both stages use the same uv base so the copied venv (editable .pth → /app/*/src) stays valid.
#
# Reproducibility (FR-011): pin the base image tag; for release, pin by digest. Frozen lockfile.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
# copy the internal dependency closure as siblings (api's ../db ../probability ../eval resolve)
COPY db/ ./db/
COPY eval/ ./eval/
COPY probability/ ./probability/
COPY api/ ./api/
RUN cd api && uv sync --frozen --no-dev

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime
LABEL org.opencontainers.image.title="horseracing-api"
# org.opencontainers.image.revision (git SHA) is set at build time via --label (see compose/README)
WORKDIR /app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    ALEMBIC_SCRIPT_LOCATION=/app/db/migrations \
    PYTHONUNBUFFERED=1
RUN useradd -m -u 10001 appuser && chown -R appuser /app
USER appuser
EXPOSE 8000
# default: serve read-only API. migrate service overrides command (see docker-compose.yml).
CMD ["uvicorn", "horseracing_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
