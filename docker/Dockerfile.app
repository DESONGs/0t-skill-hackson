# syntax=docker/dockerfile:1.7

FROM node:20-bookworm-slim AS node_runtime

FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:/usr/local/bin:${PATH}"

WORKDIR /app

COPY --from=node_runtime /usr/local/ /usr/local/

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir uv

COPY pyproject.toml README.md uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra storage

COPY .env.example ./.env.example
COPY AGENTS.md ./AGENTS.md
COPY AGENT_QUICKSTART.md ./AGENT_QUICKSTART.md
COPY CLAUDE.md ./CLAUDE.md
COPY CONFIGURATION.md ./CONFIGURATION.md
COPY START_HERE.md ./START_HERE.md
COPY agent.md ./agent.md
COPY bin ./bin
COPY frontend ./frontend
COPY scripts ./scripts
COPY services ./services
COPY skills ./skills
COPY src ./src
COPY tests ./tests
COPY vendor ./vendor

RUN chmod +x scripts/*.sh bin/ot-enterprise

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra storage

RUN mkdir -p /app/.ot-workspace /app/skills

RUN --mount=type=cache,target=/root/.npm \
    uv run --no-sync ot-runtime-bootstrap --workspace-dir /app/.ot-workspace prepare

FROM base AS runtime

CMD ["uv", "run", "--no-sync", "ot-enterprise", "runtime", "overview", "--workspace-dir", ".ot-workspace"]

FROM base AS verify

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra storage --extra dev

CMD ["./scripts/verify.sh"]
