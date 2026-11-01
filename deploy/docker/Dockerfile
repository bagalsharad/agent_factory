# Agent Factory — production image (API + dashboard; the same image runs workers).
# Multi-stage: build the web UI, resolve Python deps from uv.lock, then copy into a slim, non-root runtime.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim

FROM ${NODE_IMAGE} AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --extra postgres --extra otlp
COPY src/ src/
COPY templates/ templates/
COPY examples/ examples/
COPY migrations/ migrations/
COPY alembic.ini ./
RUN uv sync --frozen --no-dev --extra postgres --extra otlp

FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="agent-factory" \
      org.opencontainers.image.description="Local-first AI agent engineering platform" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN groupadd --system --gid 10001 agentfactory && \
    useradd --system --uid 10001 --gid agentfactory --home-dir /app --shell /usr/sbin/nologin agentfactory
WORKDIR /app
COPY --from=build --chown=root:root /app /app
COPY --from=web --chown=root:root /web/dist /app/web/dist
RUN mkdir -p /data && chown agentfactory:agentfactory /data
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AF_DATA_DIR=/data \
    AF_HOST=0.0.0.0 \
    AF_PORT=8787 \
    AF_LOG_JSON=true
USER agentfactory
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=4).status == 200 else 1)"
ENTRYPOINT ["agent-factory"]
CMD ["serve"]
