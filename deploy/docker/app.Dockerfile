# syntax=docker/dockerfile:1
# The Coglossa app image (spec §10): the API serving the built web app, the worker, the CLI
# (migrations, doctor) and the fake model and scorer servers of the demo. Build it from the
# repository root (make image-app):
#   docker build --platform linux/amd64 -f deploy/docker/app.Dockerfile -t coglossa/app:dev .

FROM node:22-bookworm-slim AS web
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable
WORKDIR /src/web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.12-slim-bookworm AS python
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY engine/pyproject.toml engine/uv.lock engine/
COPY api/pyproject.toml api/uv.lock api/
# Dependencies first, so a code change does not reinstall them.
RUN cd api && uv sync --frozen --no-dev --no-install-project --no-install-package coglossa
COPY engine/coglossa engine/coglossa
COPY api/coglossa_api api/coglossa_api
RUN cd api && uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
ARG VERSION=dev
LABEL org.opencontainers.image.title="coglossa-app" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/Bonham-Technologies/coglossa"
RUN groupadd --gid 10001 coglossa \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin coglossa \
 && mkdir -p /data/files /etc/coglossa \
 && chown coglossa:coglossa /data/files
COPY --from=python /opt/venv /opt/venv
COPY --from=web /src/web/dist /opt/coglossa/web
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    COGLOSSA_CONFIG=/etc/coglossa/coglossa.yaml
USER 10001:10001
WORKDIR /data
EXPOSE 8080
CMD ["coglossa-api", "--host", "0.0.0.0", "--port", "8080", "--web-dir", "/opt/coglossa/web"]
