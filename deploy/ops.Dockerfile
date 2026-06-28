# Ops image (Feature 024): the WRITE service + worker for on-demand netkeiba refresh. Separate from
# the read-only api image. Build context = repository ROOT (per-package uv.lock with editable path
# deps needs the sibling sources). Dependency closure: ops → {db, scrape}; scrape → {db, features};
# features → {db}. api/probability/eval/serving/training are NOT needed here.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
COPY db/ ./db/
COPY features/ ./features/
COPY scrape/ ./scrape/
COPY ops/ ./ops/
RUN cd ops && uv sync --frozen --no-dev

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime
LABEL org.opencontainers.image.title="horseracing-ops"
WORKDIR /app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
RUN useradd -m -u 10002 opsuser && chown -R opsuser /app
USER opsuser
EXPOSE 8001
# default: serve the ops write API. The worker service overrides command (see docker-compose.yml).
CMD ["uvicorn", "horseracing_ops.app:app", "--host", "0.0.0.0", "--port", "8001"]
