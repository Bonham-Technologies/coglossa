# syntax=docker/dockerfile:1
# The scorer's development environment on a host that cannot run it natively (Intel macOS:
# torch has no wheels there after 2.2.2). The same base, uv and lock as the scorer image
# (scorer.Dockerfile), so tests run on the packages a client runs. The Makefile uses it:
#   make test-scorer      (on Intel macOS; elsewhere it runs natively)
# The source is mounted at run time; the venv and uv's cache live in Docker volumes.
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_CACHE_DIR=/root/.cache/uv
WORKDIR /src/scorer
