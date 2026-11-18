.PHONY: airgap-test bundle flores demo demo-down deploy-check weights images image-app image-scorer image-scorer-cuda test test-engine test-scorer test-api test-web e2e dev-stack lint fmt schema db-up db-down

COGLOSSA_VERSION ?= dev
PLATFORM ?= linux/amd64
# CUDA build of torch for the scorer (download.pytorch.org/whl/<index>): the newest CUDA 12.x
# index that has the torch version scorer/uv.lock pins (cu128 and cu129 do not). CUDA 12 needs
# NVIDIA driver 525 or newer.
CUDA_INDEX ?= cu126
WEIGHTS_DIR ?= $(HOME)/coglossa-weights
# FLORES-200 for bench (CC BY-SA 4.0): make flores fetches it; make bundle ships it when present
FLORES_DIR ?= $(HOME)/coglossa-flores
SCORER ?= cuda
DB_URL ?= postgresql://coglossa:coglossa@127.0.0.1:55432/postgres
# The scorer's torch has no Intel-macOS wheels, so there its targets run in a Linux container
# with the scorer image's base, uv and lock (deploy/docker/scorer-dev.Dockerfile): the packages
# a client runs. SCORER_IN_DOCKER=1 forces the container anywhere; =0 runs natively.
SCORER_IN_DOCKER ?= $(if $(filter Darwin-x86_64,$(shell uname -s)-$(shell uname -m)),1,0)
ifeq ($(SCORER_IN_DOCKER),1)
SCORER_UV = docker build -q -f deploy/docker/scorer-dev.Dockerfile -t coglossa/scorer-dev . >/dev/null \
	&& docker run --rm --platform $(PLATFORM) -v "$(CURDIR)/scorer:/src/scorer" \
	-v "$(CURDIR)/schema:/src/schema:ro" \
	-v coglossa-scorer-venv:/opt/venv -v coglossa-scorer-uv-cache:/root/.cache/uv \
	-v "$(WEIGHTS_DIR):$(WEIGHTS_DIR)" -e COGLOSSA_REAL_MODELS -e COGLOSSA_METRICX_DIR -e COGLOSSA_MT5_TOKENIZER_DIR \
	-e COGLOSSA_LABSE_DIR coglossa/scorer-dev \
	uv run --frozen --all-groups
else
SCORER_UV = cd scorer && uv run
endif
COMPOSE_DEV = docker compose -f deploy/compose.dev.yaml
# Cloud sessions (CLAUDE_CODE_REMOTE) run Postgres natively, not in Docker, and a container
# resumed under a running session comes back with it stopped and no SessionStart hook to
# restart it: start any stopped local cluster (pg_ctlcluster clears a stale pid file).
# Elsewhere this does nothing.
DB_NATIVE_START = if [ "$${CLAUDE_CODE_REMOTE:-}" = true ] && command -v pg_lsclusters >/dev/null; then \
	pg_lsclusters -h | awk '$$4 == "down" {print $$1, $$2}' | while read -r ver name; do \
	pg_ctlcluster "$$ver" "$$name" start || true; done; fi
# Before a suite that needs the database: one line and a stop when it is down, rather than an
# error for every test (CGL-41).
DB_CHECK = export COGLOSSA_TEST_DATABASE_URL=$${COGLOSSA_TEST_DATABASE_URL:-$(DB_URL)} \
	&& uv run python -m coglossa.testing.postgres

test: test-engine test-scorer test-api test-web
	python3 deploy/tests/check_workflows.py

test-engine: db-up
	@$(DB_NATIVE_START)
	cd engine && $(DB_CHECK) && uv run pytest

test-scorer:
	$(SCORER_UV) pytest

weights:
	mkdir -p $(WEIGHTS_DIR)
	$(SCORER_UV) coglossa-scorer-weights fetch --out $(WEIGHTS_DIR)
	$(SCORER_UV) coglossa-scorer-weights verify $(WEIGHTS_DIR)

flores:
	cd engine && uv run coglossa bench fetch-flores --out $(abspath $(FLORES_DIR))

test-api: db-up
	@$(DB_NATIVE_START)
	cd api && $(DB_CHECK) && uv run pytest

test-web:
	cd web && pnpm test

e2e: db-up
	cd web && pnpm build && COGLOSSA_TEST_DATABASE_URL=$${COGLOSSA_TEST_DATABASE_URL:-$(DB_URL)} pnpm e2e

dev-stack: db-up
	cd api && COGLOSSA_TEST_DATABASE_URL=$${COGLOSSA_TEST_DATABASE_URL:-$(DB_URL)} uv run python devstack.py --port 8080

db-up:
	$(COMPOSE_DEV) up -d --wait postgres

db-down:
	$(COMPOSE_DEV) down

lint:
	cd engine && uv run ruff check . && uv run ruff format --check .
	$(SCORER_UV) ruff check .
	$(SCORER_UV) ruff format --check .
	cd api && uv run ruff check . && uv run ruff format --check .
	cd web && pnpm lint

fmt:
	cd engine && uv run ruff format . && uv run ruff check --fix .
	$(SCORER_UV) ruff format .
	$(SCORER_UV) ruff check --fix .
	cd api && uv run ruff format . && uv run ruff check --fix .
	cd web && pnpm fmt

schema:
	cd engine && uv run python -m coglossa.schema.export ../schema
	cd api && uv run python -m coglossa_api.openapi ../schema/openapi.json
	cd web && pnpm gen:api

images: image-app image-scorer

image-app:
	docker build --platform $(PLATFORM) -f deploy/docker/app.Dockerfile \
	  --build-arg VERSION=$(COGLOSSA_VERSION) -t coglossa/app:$(COGLOSSA_VERSION) .
	deploy/tests/check-app-image.sh coglossa/app:$(COGLOSSA_VERSION)

image-scorer:
	docker build --platform $(PLATFORM) -f deploy/docker/scorer.Dockerfile \
	  --build-arg VERSION=$(COGLOSSA_VERSION) --build-arg TORCH_INDEX=cpu \
	  -t coglossa/scorer:$(COGLOSSA_VERSION) .
	deploy/tests/check-scorer-image.sh coglossa/scorer:$(COGLOSSA_VERSION) cpu

image-scorer-cuda:
	docker build --platform $(PLATFORM) -f deploy/docker/scorer.Dockerfile \
	  --build-arg VERSION=$(COGLOSSA_VERSION) --build-arg TORCH_INDEX=$(CUDA_INDEX) \
	  -t coglossa/scorer:$(COGLOSSA_VERSION)-cuda .
	deploy/tests/check-scorer-image.sh coglossa/scorer:$(COGLOSSA_VERSION)-cuda $(CUDA_INDEX)

bundle:
	CUDA_INDEX=$(CUDA_INDEX) deploy/bundle.sh --weights $(WEIGHTS_DIR) --scorer $(SCORER) \
	  $(if $(wildcard $(FLORES_DIR)),--flores $(FLORES_DIR))
	deploy/tests/check-bundle.sh dist/$$(ls -t dist | grep -m1 'linux-amd64.tar$$')

COMPOSE_DEMO = cd deploy && COGLOSSA_DB_PASSWORD=$${COGLOSSA_DB_PASSWORD:-demo} \
  COGLOSSA_HTTP_BIND=127.0.0.1 docker compose -p coglossa-demo -f compose.yaml -f compose.demo.yaml

demo: images
	$(COMPOSE_DEMO) up -d --wait
	@echo "Coglossa with demo models: http://127.0.0.1:$${COGLOSSA_HTTP_PORT:-8080}"

demo-down:
	$(COMPOSE_DEMO) down -v

# Always the pinned shellcheck container, never a local binary or the runner's: a different
# shellcheck version has twice flagged what another accepts (e.g. SC2015 on `A && B || C`), so
# local runs and CI (whose runner has Docker) must run the same version.
SHELLCHECK := docker run --rm -v "$(CURDIR):/mnt" -w /mnt koalaman/shellcheck:v0.10.0

deploy-check:
	$(SHELLCHECK) deploy/*.sh deploy/tests/*.sh deploy/hosted/vercel/*.sh
	deploy/tests/test-install.sh
	deploy/tests/test-restore.sh
	deploy/tests/test-bundle.sh
	python3 deploy/tests/check_compose.py
	python3 deploy/tests/check_workflows.py
	python3 deploy/tests/check_tracker_links.py

airgap-test:
	deploy/tests/airgap-test.sh
