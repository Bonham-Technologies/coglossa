# syntax=docker/dockerfile:1
# The scorer image (spec §2.1): coglossa-scorer serving MetricX-24 and LaBSE. The weights are
# not in the image: compose mounts the bundle's models/ directory at /models (read-only).
#   docker build --platform linux/amd64 -f deploy/docker/scorer.Dockerfile --build-arg TORCH_INDEX=cpu \
#     -t coglossa/scorer:dev .
# TORCH_INDEX picks the torch build from download.pytorch.org/whl/<index>: cpu, or a CUDA build
# (which also runs on a CPU-only host; tag it coglossa/scorer:<version>-cuda, as
# make image-scorer-cuda does). The torch version is always the one scorer/uv.lock pins
# for Linux.

FROM python:3.12-slim-bookworm AS python
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ARG TORCH_INDEX=cpu
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src/scorer
COPY scorer/pyproject.toml scorer/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-install-package torch
# Torch's version is the one uv.lock pins for Linux (the entry from the PyTorch index); fail
# clearly if the lock has none. Every other locked package is held at its locked version by a
# constraints file, so this step adds only torch and its CUDA-only extras (nvidia-*, triton).
# --index outranks --default-index in uv, so the PyTorch index wins for torch and PyPI supplies
# the rest.
RUN version=$(python3 -c 'import sys, tomllib; lock = tomllib.load(open("uv.lock", "rb")); v = [p["version"] for p in lock["package"] if p["name"] == "torch" and "download.pytorch.org" in p["source"].get("registry", "")]; sys.exit("error: uv.lock has no Linux torch entry from download.pytorch.org") if not v else print(v[0].split("+")[0])') \
 && uv export --frozen --no-dev --no-hashes --no-emit-project | grep -v '^torch==' > /tmp/constraints.txt \
 && uv pip install --python /opt/venv/bin/python -c /tmp/constraints.txt "torch==${version}+${TORCH_INDEX}" \
      --default-index https://pypi.org/simple --index "https://download.pytorch.org/whl/${TORCH_INDEX}"
COPY scorer/coglossa_scorer coglossa_scorer
RUN uv pip install --python /opt/venv/bin/python --no-deps .

FROM python:3.12-slim-bookworm
ARG VERSION=dev
ARG TORCH_INDEX=cpu
LABEL org.opencontainers.image.title="coglossa-scorer" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/Bonham-Technologies/coglossa" \
      coglossa.torch-index="${TORCH_INDEX}"
RUN groupadd --gid 10001 coglossa \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin coglossa
COPY --from=python /opt/venv /opt/venv
# MALLOC_ARENA_MAX: each request thread got its own glibc arena, and under bench load the
# scorer's memory grew from 1 GiB past an 8 GiB limit and it was killed; two arenas hold it at 3-6.
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=/tmp/huggingface MALLOC_ARENA_MAX=2
USER 10001:10001
EXPOSE 8080
CMD ["coglossa-scorer", "--qe-model", "/models/metricx-24-hybrid-large-v2p6", \
     "--qe-tokenizer", "/models/mt5-xl", "--embed-model", "/models/labse", \
     "--host", "0.0.0.0", "--port", "8080"]
