# Coglossa Hosted Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A public, per-person-password Coglossa preview: Vercel front door (always on), Supabase Postgres and Storage, Modal API, burst worker and L4 GPU scorer (all scale to zero), and OpenRouter models. The air-gapped compose deployment does not change.

**Architecture:** The shared code gains four optional features, all off by default:
- S3 storage
- an API proxy secret and a logout URL
- a worker kick hook
- a scorer bearer token.

Two new directories carry everything that is hosted-only:
- `deploy/hosted/vercel/`: login and the `/api` rewrite in Vercel Routing Middleware
- `deploy/hosted/modal/`: the Modal app.

A GitHub workflow deploys both on `main`.

**Tech Stack:**
- Python 3.12, pydantic 2, FastAPI, procrastinate, httpx
- boto3 and moto
- Modal (Python SDK)
- Vercel Routing Middleware (`@vercel/functions`) in TypeScript, tested with Vitest
- pnpm, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-10-14-coglossa-hosted-preview-design.md` (v1 spec: `docs/superpowers/specs/2026-10-09-coglossa-v1-design.md`)

## Global Constraints

- Every new setting defaults to off. With no new settings, behaviour, the compose stack, the bundle and the air-gap test are unchanged (spec §1.1).
- Coverage stays ≥ 95% branch for each Python package (`--cov-branch --cov-fail-under=95`) and ≥ 95% lines/branches/functions/statements for each TypeScript package. CI enforces this.
- `pytest` runs with `filterwarnings = ["error"]`. Tests need no network access, except the Postgres the existing fixtures already use.
- No secret is ever committed. Config files name environment variables (`*_env`); values live in a Modal Secret, Vercel env or GitHub secrets (spec §7).
- Header names are fixed: `X-Coglossa-Proxy-Secret` (proxy secret), `X-Remote-User` (user name), and `Authorization: Bearer <token>` (kick and scorer).
- Config-validation messages follow the existing style: plain language, lower-case, and naming the setting (for example `auth: environment variable COGLOSSA_PROXY_SECRET is not set`).
- Python code matches the surrounding code: ruff (line length 100), and docstrings and comments explain *why*, at the existing density.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_012TBMbjzsQyykrdRBR7Vuv5
  ```

## Review Focus

1. **Uploading a DOCX or PDF to S3 storage.** Ingestors choose a format by file extension, so `S3Storage.local_file` must yield a temp file with the key's suffix (test in Task 1).
2. **A browser that sends its own `X-Remote-User` or `X-Coglossa-Proxy-Secret`.** Vercel must strip both and set its own, so nobody can sign their edits as someone else (test in Task 5).
3. **A user removed from `COGLOSSA_USERS` whose cookie has not expired.** Their next request must be refused (test in Task 5).
4. **The kick endpoint being down or slow.** Uploads and guide approvals must still succeed promptly, with the job left queued for the cron (test in Task 3).
5. **The live progress stream (SSE) behind the proxy-secret middleware.** It must still stream event by event, not buffer until the run ends (test in Task 2).

## Execution order and the provisioning gate

Tasks 1–7 are code and run in order: subagent-implemented, each followed by a task review. Task 8 is **controller-run with the user's accounts**: spikes S1–S3, secrets, the first deploy and the end-to-end check. Tasks 5 and 6 are written so the spike outcomes change only one function each:
- Task 5: `forward()` in `deploy/hosted/vercel/lib/route.ts`
- Task 6: the scorer function's `enable_memory_snapshot`.

---

### Task 1: S3 storage (`storage.kind: s3`)

**Files:**
- Modify: `engine/coglossa/config.py` (StorageConfig area, about line 131; the `storage` field, line 300; storage path resolution, line 427; `validate_config`)
- Modify: `engine/coglossa/storage.py`
- Modify: `engine/coglossa/doctor.py:328-333`
- Modify: `engine/pyproject.toml` (`boto3` dependency; `moto[s3]` in dev), `engine/uv.lock`, `api/uv.lock`
- Modify: `deploy/config/coglossa.example.yaml` (a commented s3 example)
- Modify: `docs/operator-guide.md` (one paragraph on `storage.kind: s3`)
- Test: `engine/tests/test_storage_s3.py` (new), `engine/tests/test_config_m3.py`, `engine/tests/test_doctor*.py` (whichever file covers `_storage`)

**Interfaces:**
- Produces:
  - `coglossa.config.S3StorageConfig`
  - `CoglossaConfig.storage: StorageConfig | S3StorageConfig`, discriminated by `kind`, where a missing `kind` means `local`
  - `coglossa.storage.S3Storage(cfg: S3StorageConfig, *, client: Any | None = None)`
  - `coglossa.storage.describe_storage(cfg: StorageConfig | S3StorageConfig) -> str`
  - `build_storage(cfg)` returns `S3Storage` for kind s3.

- [ ] **Step 1: Add the dependencies**

`boto3` becomes a regular dependency. The import is lazy, inside `S3Storage`, so local-storage installs never load it. A plain dependency keeps the app image and bundle builds unchanged.

```bash
cd engine && uv add 'boto3>=1.35,<2' && uv add --group dev 'moto[s3]>=5.0,<6'
cd ../api && uv lock
```

- [ ] **Step 2: Write the failing tests**

Create `engine/tests/test_storage_s3.py`:

```python
import os

import boto3
import pytest
from moto import mock_aws

from coglossa.config import S3StorageConfig
from coglossa.storage import S3Storage, StorageError, build_storage, describe_storage

BUCKET = "coglossa-files"


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("COGLOSSA_S3_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("COGLOSSA_S3_SECRET_ACCESS_KEY", "test-secret")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield


def _cfg(**kw) -> S3StorageConfig:
    return S3StorageConfig.model_validate({"kind": "s3", "bucket": BUCKET, **kw})


def test_put_get_overwrite(s3):
    store = S3Storage(_cfg())
    store.put("projects/p1/documents/d1/story.txt", b"one")
    store.put("projects/p1/documents/d1/story.txt", b"two")
    assert store.get("projects/p1/documents/d1/story.txt") == b"two"


def test_prefix_is_prepended(s3):
    S3Storage(_cfg(prefix="preview")).put("a/b.txt", b"x")
    got = boto3.client("s3", region_name="us-east-1").get_object(
        Bucket=BUCKET, Key="preview/a/b.txt"
    )
    assert got["Body"].read() == b"x"


def test_local_file_keeps_the_suffix_and_is_removed(s3):
    store = S3Storage(_cfg())
    store.put("d/report.docx", b"PK\x03\x04")
    with store.local_file("d/report.docx") as path:
        assert path.suffix == ".docx"
        assert path.read_bytes() == b"PK\x03\x04"
    assert not path.exists()


def test_missing_key(s3):
    store = S3Storage(_cfg())
    with pytest.raises(StorageError, match="stored file not found: nope.txt"):
        store.get("nope.txt")
    with pytest.raises(StorageError, match="stored file not found"), store.local_file("x.txt"):
        pass


@pytest.mark.parametrize("key", ["", "/etc/passwd", "../x", "a/../../x", "a\\b", "a//b", "a/./b"])
def test_unsafe_keys_are_rejected(s3, key):
    with pytest.raises(StorageError, match="invalid storage key"):
        S3Storage(_cfg()).put(key, b"x")


def test_delete_prefix_removes_the_key_and_everything_under_it(s3):
    store = S3Storage(_cfg())
    for key in ["p/1/a.txt", "p/1/b/c.txt", "p/1", "p/10/keep.txt"]:
        store.put(key, b"x")
    store.delete_prefix("p/1/")
    assert store.get("p/10/keep.txt") == b"x"
    for gone in ["p/1/a.txt", "p/1/b/c.txt", "p/1"]:
        with pytest.raises(StorageError):
            store.get(gone)


def test_delete_prefix_pages_through_many_keys(s3):
    store = S3Storage(_cfg())
    for i in range(1005):  # more than one list page and one delete batch (1000)
        store.put(f"many/{i}.txt", b"x")
    store.delete_prefix("many")
    listed = boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket=BUCKET)
    assert listed.get("KeyCount", 0) == 0


def test_check_writable_and_failures(s3):
    S3Storage(_cfg()).check_writable()
    with pytest.raises(StorageError, match="storage bucket no-such-bucket .* is not writable"):
        S3Storage(_cfg(bucket="no-such-bucket")).check_writable()
    with pytest.raises(StorageError, match="cannot store a.txt"):
        S3Storage(_cfg(bucket="no-such-bucket")).put("a.txt", b"x")
    with pytest.raises(StorageError, match="cannot read a.txt"):
        S3Storage(_cfg(bucket="no-such-bucket")).get("a.txt")
    with pytest.raises(StorageError, match="cannot delete a/"):
        S3Storage(_cfg(bucket="no-such-bucket")).delete_prefix("a/")


def test_missing_credentials_are_a_storage_error(s3, monkeypatch):
    monkeypatch.delenv("COGLOSSA_S3_SECRET_ACCESS_KEY")
    with pytest.raises(StorageError, match="COGLOSSA_S3_SECRET_ACCESS_KEY is not set"):
        S3Storage(_cfg())


def test_build_and_describe(s3):
    cfg = _cfg(endpoint_url="https://ref.storage.supabase.co/storage/v1/s3", prefix="pv")
    assert isinstance(build_storage(cfg), S3Storage)
    assert describe_storage(cfg) == (
        "s3://coglossa-files/pv at https://ref.storage.supabase.co/storage/v1/s3"
    )
    assert describe_storage(_cfg()) == "s3://coglossa-files"
    assert os.environ["COGLOSSA_S3_ACCESS_KEY_ID"] == "test-key"
```

Add to `engine/tests/test_config_m3.py`:

```python
def test_storage_kind_defaults_to_local_and_s3_is_accepted(make_config, monkeypatch):
    assert make_config().storage.kind == "local"
    assert make_config(storage={"path": "/x"}).storage.kind == "local"
    monkeypatch.setenv("COGLOSSA_S3_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("COGLOSSA_S3_SECRET_ACCESS_KEY", "s")
    cfg = make_config(storage={"kind": "s3", "bucket": "b", "endpoint_url": "https://e/s3"})
    assert cfg.storage.kind == "s3" and cfg.storage.bucket == "b"
    assert cfg.storage.region == "us-east-1" and cfg.storage.prefix == ""


def test_s3_storage_rejects_unknown_fields_and_kinds(make_config):
    with pytest.raises(ValidationError):
        make_config(storage={"kind": "s3", "bucket": "b", "path": "/x"})
    with pytest.raises(ValidationError):
        make_config(storage={"kind": "gcs"})


def test_s3_storage_env_vars_must_be_set(tmp_path, monkeypatch):
    monkeypatch.delenv("COGLOSSA_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("COGLOSSA_S3_SECRET_ACCESS_KEY", raising=False)
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump({**BASE_CONFIG, "storage": {"kind": "s3", "bucket": "b"}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="storage: environment variable COGLOSSA_S3_ACCESS_KEY_ID"):
        load_config(path)


def test_s3_storage_is_not_path_resolved(tmp_path, monkeypatch):
    monkeypatch.setenv("COGLOSSA_S3_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("COGLOSSA_S3_SECRET_ACCESS_KEY", "s")
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump({**BASE_CONFIG, "storage": {"kind": "s3", "bucket": "b"}}),
        encoding="utf-8",
    )
    cfg, _ = load_config(path)
    assert cfg.storage.kind == "s3"
```

Bring in whatever imports the file lacks: `pytest`, `yaml`, `ValidationError` from pydantic, `ConfigError` and `load_config` from `coglossa.config`, and `BASE_CONFIG` from `tests.conftest`.

In the doctor tests, add a case where a config with `storage.kind: s3` and a moto bucket reports `PASS` with the message `s3://<bucket> is writable`, and one where a missing bucket reports `FAIL` with the fix `fix storage (bucket, endpoint_url, keys)`. Follow the existing `_storage` tests in that file.

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `cd engine && uv run pytest tests/test_storage_s3.py tests/test_config_m3.py -q --no-cov`
Expected: FAIL with `ImportError: cannot import name 'S3StorageConfig'`

- [ ] **Step 4: Implement the config**

In `engine/coglossa/config.py`:
- add `Annotated` to the `typing` import
- add `Discriminator, Tag` to the pydantic import
- keep `StorageConfig` as the local class
- after it, add:

```python
class S3StorageConfig(_Strict):
    """Files in an S3-compatible bucket (AWS S3, Supabase Storage, MinIO). The keys are read
    from the environment variables named here; they never go in the config file."""

    kind: Literal["s3"]
    endpoint_url: str | None = None  # None: AWS S3 itself
    region: str = "us-east-1"
    bucket: str = Field(min_length=3, max_length=63)
    prefix: str = ""  # keys go under this prefix, e.g. one bucket shared by several sites
    access_key_id_env: str = "COGLOSSA_S3_ACCESS_KEY_ID"
    secret_access_key_env: str = "COGLOSSA_S3_SECRET_ACCESS_KEY"


def _storage_kind(value: Any) -> str:
    if isinstance(value, dict):
        return value.get("kind", "local")
    return getattr(value, "kind", "local")


AnyStorageConfig = Annotated[
    Annotated[StorageConfig, Tag("local")] | Annotated[S3StorageConfig, Tag("s3")],
    Discriminator(_storage_kind),
]
```

Update `StorageConfig.kind`'s comment to `# or "s3": S3StorageConfig`. Change the field to `storage: AnyStorageConfig = Field(default_factory=StorageConfig)`.

In `load_config`, replace the two storage lines (427–428) with:

```python
    if isinstance(cfg.storage, StorageConfig):
        storage = cfg.storage.model_copy(update={"path": (base / cfg.storage.path).resolve()})
        cfg = cfg.model_copy(update={"storage": storage})
```

In `validate_config`, after the backend `api_key_env` loop, add:

```python
    if isinstance(cfg.storage, S3StorageConfig):
        for var in (cfg.storage.access_key_id_env, cfg.storage.secret_access_key_env):
            if not os.environ.get(var):
                add("error", f"storage: environment variable {var} is not set")
```

- [ ] **Step 5: Implement `S3Storage`**

In `engine/coglossa/storage.py`:
1. Change the module docstring's first line to `"""Uploaded files (spec §2.1): a Storage interface with local-volume and S3 implementations.`
2. Change the import to `from coglossa.config import S3StorageConfig, StorageConfig`, and add `from typing import Any` (alongside `Protocol`).
3. Move key validation out of `LocalStorage._path` into a module function, and have `_path` call it:

```python
def check_key(key: str) -> list[str]:
    """The key's parts; raises StorageError for a key that could escape the storage root."""
    parts = key.split("/")
    if not key or key.startswith("/") or "\\" in key or any(p in ("", ".", "..") for p in parts):
        raise StorageError(f"invalid storage key: {key!r}")
    return parts
```

`LocalStorage._path` becomes `return self.root.joinpath(*check_key(key))`.

4. Add the class and the helpers:

```python
class S3Storage:
    """Files in an S3-compatible bucket. boto3 is imported here, so a local-storage install
    never loads it. Supabase Storage needs path-style addressing."""

    def __init__(self, cfg: S3StorageConfig, *, client: Any | None = None) -> None:
        self.cfg = cfg
        if client is None:
            import boto3
            from botocore.config import Config

            keys = {}
            for field, var in (
                ("aws_access_key_id", cfg.access_key_id_env),
                ("aws_secret_access_key", cfg.secret_access_key_env),
            ):
                value = os.environ.get(var)
                if not value:
                    raise StorageError(f"environment variable {var} is not set")
                keys[field] = value
            client = boto3.client(
                "s3",
                endpoint_url=cfg.endpoint_url,
                region_name=cfg.region,
                config=Config(s3={"addressing_style": "path"}, retries={"mode": "standard"}),
                **keys,
            )
        self._s3 = client

    def _key(self, key: str) -> str:
        check_key(key)
        return f"{self.cfg.prefix.strip('/')}/{key}" if self.cfg.prefix.strip("/") else key

    def put(self, key: str, data: bytes) -> None:
        try:
            self._s3.put_object(Bucket=self.cfg.bucket, Key=self._key(key), Body=data)
        except _s3_errors() as e:
            raise StorageError(f"cannot store {key}: {_reason(e)}") from e

    def get(self, key: str) -> bytes:
        try:
            got = self._s3.get_object(Bucket=self.cfg.bucket, Key=self._key(key))
            return got["Body"].read()
        except _s3_errors() as e:
            if _code(e) in ("NoSuchKey", "404"):
                raise StorageError(f"stored file not found: {key}") from e
            raise StorageError(f"cannot read {key}: {_reason(e)}") from e

    @contextlib.contextmanager
    def local_file(self, key: str) -> Iterator[Path]:
        data = self.get(key)  # ingestors pick a format by the suffix, so the temp file keeps it
        fd, name = tempfile.mkstemp(suffix=PurePosixPath(key).suffix, prefix="coglossa-")
        path = Path(name)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            yield path
        finally:
            path.unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        base = self._key(prefix.rstrip("/"))
        try:
            keys = [base]
            pages = self._s3.get_paginator("list_objects_v2").paginate(
                Bucket=self.cfg.bucket, Prefix=base + "/"
            )
            for page in pages:
                keys += [o["Key"] for o in page.get("Contents", [])]
            for start in range(0, len(keys), 1000):  # DeleteObjects takes at most 1000 keys
                batch = [{"Key": k} for k in keys[start : start + 1000]]
                self._s3.delete_objects(
                    Bucket=self.cfg.bucket, Delete={"Objects": batch, "Quiet": True}
                )
        except _s3_errors() as e:
            raise StorageError(f"cannot delete {prefix}: {_reason(e)}") from e

    def check_writable(self) -> None:
        probe = self._key(f".coglossa-check-{uuid.uuid4().hex}")
        try:
            self._s3.put_object(Bucket=self.cfg.bucket, Key=probe, Body=b"")
            self._s3.delete_object(Bucket=self.cfg.bucket, Key=probe)
        except _s3_errors() as e:
            raise StorageError(
                f"storage bucket {self.cfg.bucket} at {self.cfg.endpoint_url or 'AWS S3'} "
                f"is not writable: {_reason(e)}"
            ) from e


def _s3_errors() -> tuple[type[Exception], ...]:
    from botocore.exceptions import BotoCoreError, ClientError

    return (BotoCoreError, ClientError)


def _code(e: Exception) -> str:
    return str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))


def _reason(e: Exception) -> str:
    error = getattr(e, "response", {}).get("Error", {})
    if error:
        return f"{error.get('Code', '')}: {error.get('Message', '')}".strip(": ")
    return " ".join(str(e).split())


def describe_storage(cfg: StorageConfig | S3StorageConfig) -> str:
    if isinstance(cfg, StorageConfig):
        return str(cfg.path)
    where = f"s3://{cfg.bucket}" + (f"/{cfg.prefix.strip('/')}" if cfg.prefix.strip("/") else "")
    return f"{where} at {cfg.endpoint_url}" if cfg.endpoint_url else where


def build_storage(cfg: StorageConfig | S3StorageConfig) -> Storage:
    if isinstance(cfg, S3StorageConfig):
        return S3Storage(cfg)
    return LocalStorage(cfg.path)
```

Add `import uuid` at the top. `delete_objects` with a key that does not exist is not an error in S3 or moto. That is intended: `delete_prefix` of nothing succeeds, as `LocalStorage` does.

- [ ] **Step 6: Update doctor**

In `engine/coglossa/doctor.py`, `_storage` becomes:

```python
def _storage(cfg: CoglossaConfig) -> DoctorCheck:
    where = describe_storage(cfg.storage)
    fix = (
        "fix storage (bucket, endpoint_url, keys)"
        if cfg.storage.kind == "s3"
        else "fix storage.path or its permissions"
    )
    try:
        build_storage(cfg.storage).check_writable()
    except StorageError as e:
        return _check("storage", CheckStatus.FAIL, str(e), fix)
    return _check("storage", CheckStatus.PASS, f"{where} is writable")
```

Import `describe_storage` from `coglossa.storage`.

- [ ] **Step 7: Run all engine and api tests**

Run: `cd engine && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`, then `cd ../api && uv sync --all-groups && uv run pytest -q`
Expected: PASS, with coverage ≥ 95%

- [ ] **Step 8: Document it**

In `deploy/config/coglossa.example.yaml`, under `storage:` add:

```yaml
  # kind: s3                             # or an S3-compatible bucket instead of the volume:
  # endpoint_url: https://<ref>.storage.supabase.co/storage/v1/s3   # omit for AWS S3
  # bucket: coglossa-files
  # access_key_id_env: COGLOSSA_S3_ACCESS_KEY_ID           # names of variables in .env
  # secret_access_key_env: COGLOSSA_S3_SECRET_ACCESS_KEY
```

In `docs/operator-guide.md`, in the section covering `storage`, add one paragraph: S3-compatible storage is optional, the keys come from the named environment variables, `doctor` writes and deletes a probe object, and air-gapped sites keep the volume.

Run `make deploy-check` from the repo root. Expected: PASS (`check_compose.py` validates the example config).

- [ ] **Step 9: Commit**

```bash
git add engine api/uv.lock deploy/config/coglossa.example.yaml docs/operator-guide.md
git commit -m "storage: optional S3-compatible backend (storage.kind: s3)"
```

---

### Task 2: API proxy secret and logout URL (`auth.proxy_secret_env`, `auth.logout_url`)

**Files:**
- Modify: `engine/coglossa/config.py` (`AuthConfig`, about line 154; `validate_config`)
- Create: `api/coglossa_api/proxy.py`
- Modify: `api/coglossa_api/app.py` (`create_app`)
- Modify: `api/coglossa_api/routes/system.py` (`Me`, `get_me`)
- Modify: `schema/openapi.json` and `web/src/api/schema.d.ts` (regenerated)
- Modify: `web/src/components/Layout.tsx`, `web/src/styles/*` (one rule)
- Modify: `deploy/config/coglossa.example.yaml` (the commented `auth` line)
- Test: `engine/tests/test_config_m4.py`, `api/tests/test_proxy.py` (new), `api/tests/test_system.py`, `web/src/components/Layout.test.tsx`

**Interfaces:**
- Produces:
  - `AuthConfig.proxy_secret_env: str | None = None`
  - `AuthConfig.logout_url: str | None = None`
  - `coglossa_api.proxy.ProxySecretMiddleware(app, secret: str)`
  - `coglossa_api.proxy.PROXY_SECRET_HEADER = "X-Coglossa-Proxy-Secret"`
  - `GET /api/me` returns `{"user": str | null, "logout_url": str | null}`
- Task 5 sets `logout_url: /logout` and relies on the header name.

- [ ] **Step 1: Write the failing config tests**

Append to `engine/tests/test_config_m4.py`:

```python
def test_proxy_secret_and_logout_url_default_off(make_config):
    cfg = make_config()
    assert cfg.auth.proxy_secret_env is None and cfg.auth.logout_url is None


def test_proxy_secret_env_must_be_set(tmp_path, monkeypatch):
    monkeypatch.delenv("COGLOSSA_PROXY_SECRET", raising=False)
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump({**BASE_CONFIG, "auth": {"proxy_secret_env": "COGLOSSA_PROXY_SECRET"}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="auth: environment variable COGLOSSA_PROXY_SECRET"):
        load_config(path)
    monkeypatch.setenv("COGLOSSA_PROXY_SECRET", "s" * 32)
    cfg, _ = load_config(path)
    assert cfg.auth.proxy_secret_env == "COGLOSSA_PROXY_SECRET"
```

Add the imports the file lacks.

- [ ] **Step 2: Write the failing API tests**

Create `api/tests/test_proxy.py`. Use the existing fixtures: `write_config` and `serve` from `api/tests/conftest.py`, and the `Api`/recording-queue helpers the other API tests use. Read `api/tests/test_system.py` and `api/tests/test_progress.py` first and copy how they build the app. The tests:

```python
SECRET = "s3cret-" * 5


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("COGLOSSA_PROXY_SECRET", SECRET)


async def test_requests_without_the_secret_are_refused(secret_env, write_config, ...):
    # build the app with auth: {mode: trusted_header, proxy_secret_env: COGLOSSA_PROXY_SECRET}
    # GET /api/me with X-Remote-User: ana and no secret          -> 401, detail mentions the proxy
    # GET /api/me with a wrong secret                            -> 401
    # GET /api/me with X-Coglossa-Proxy-Secret: SECRET and user  -> 200 {"user": "ana", ...}
    # GET /api/health without the secret                         -> 200 (liveness stays open)
    # GET / (web app route, if web_dir is set in the test)       -> 401 without the secret


async def test_the_progress_stream_still_streams(secret_env, ...):
    # Seed a run as test_progress.py does. Open GET /api/runs/{id}/events with the secret
    # via client.stream(...). The first event arrives while the run is not terminal,
    # i.e. read the first "event: progress" chunk and assert on it before closing the
    # stream. A buffering middleware would hang here, so wrap the read in
    # asyncio.timeout(5).


async def test_without_the_setting_nothing_changes(write_config, ...):
    # auth mode none: GET /api/me -> 200 {"user": null, "logout_url": null} with no secret
```

Write these as real tests in the style of the neighbouring files. Every comment above is an assertion to implement.

Add to `api/tests/test_system.py`: with `auth: {mode: trusted_header, logout_url: /logout}`, `GET /api/me` (with `X-Remote-User: ana`) returns `{"user": "ana", "logout_url": "/logout"}`.

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `cd engine && uv run pytest tests/test_config_m4.py -q --no-cov`, then `cd ../api && uv run pytest tests/test_proxy.py tests/test_system.py -q --no-cov`
Expected: FAIL (extra field `proxy_secret_env` not permitted; `logout_url` missing)

- [ ] **Step 4: Implement the config**

In `AuthConfig`, add these after `header`:

```python
    # A secret the authenticating proxy adds to every request (header X-Coglossa-Proxy-Secret);
    # requests without it are refused. For an API whose address is reachable without the proxy.
    proxy_secret_env: str | None = None
    logout_url: str | None = None  # the web app links "Sign out" here (the proxy's own page)
```

In `validate_config`, add:

```python
    if cfg.auth.proxy_secret_env and not os.environ.get(cfg.auth.proxy_secret_env):
        add("error", f"auth: environment variable {cfg.auth.proxy_secret_env} is not set")
```

- [ ] **Step 5: Implement the middleware**

Create `api/coglossa_api/proxy.py`:

```python
"""Spec (hosted preview) §4.2: refuse requests that did not come through the authenticating
proxy. A pure ASGI middleware, not BaseHTTPMiddleware, so streamed responses (the progress
events) pass through unbuffered."""

import hmac

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

PROXY_SECRET_HEADER = "X-Coglossa-Proxy-Secret"
OPEN_PATHS = frozenset({"/api/health"})  # liveness checks never carry the secret


class ProxySecretMiddleware:
    def __init__(self, app: ASGIApp, secret: str) -> None:
        self.app = app
        self._secret = secret.encode()
        self._header = PROXY_SECRET_HEADER.lower().encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in OPEN_PATHS:
            await self.app(scope, receive, send)
            return
        given = next((v for k, v in scope["headers"] if k == self._header), b"")
        if hmac.compare_digest(given, self._secret):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {"detail": "this address only answers requests from the Coglossa sign-in proxy"},
            status_code=401,
        )
        await response(scope, receive, send)
```

In `create_app`, after `install_error_handlers(app)`:

```python
    if cfg.auth.proxy_secret_env:
        # validate_config refused an unset variable, so the value is there
        app.add_middleware(ProxySecretMiddleware, secret=os.environ[cfg.auth.proxy_secret_env])
```

Add `import os` and `from coglossa_api.proxy import ProxySecretMiddleware`.

In `routes/system.py`, add `logout_url: str | None = None` to `Me`, with a comment that it is the proxy's sign-out address, or None. `get_me` becomes:

```python
@router.get("/me")
async def get_me(user: User, ctx: Ctx) -> Me:
    return Me(user=user, logout_url=ctx.cfg.auth.logout_url)
```

Import `Ctx` from `coglossa_api.context` if the module does not already.

- [ ] **Step 6: Regenerate the schema and web types**

Run: `make schema`
Expected: `schema/openapi.json` and `web/src/api/schema.d.ts` gain `logout_url`. The `test_openapi.py` and web "generated API types" checks pass.

- [ ] **Step 7: Add the web Sign-out link (test first)**

Add to `web/src/components/Layout.test.tsx`:

```tsx
it("offers Sign out when the proxy has a sign-out page", async () => {
  server.use(
    http.get("*/api/me", () => HttpResponse.json({ user: "ana", logout_url: "/logout" })),
  );
  renderApp("/nowhere");
  const button = await screen.findByRole("button", { name: "Sign out" });
  const form = button.closest("form");
  expect(form).toHaveAttribute("action", "/logout");
  expect(form).toHaveAttribute("method", "post");
});

it("has no Sign out without a sign-out page", async () => {
  server.use(http.get("*/api/me", () => HttpResponse.json({ user: "ana", logout_url: null })));
  renderApp("/nowhere");
  expect(await screen.findByText("Signed in as ana")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
});
```

In `Layout.tsx`, after the "Signed in as" span:

```tsx
        {me.data?.logout_url ? (
          <form method="post" action={me.data.logout_url} className="signout">
            <button type="submit" className="link">
              Sign out
            </button>
          </form>
        ) : null}
```

Add a CSS rule in the stylesheet that already holds `.topbar .user`. Inline the form and style the button like the existing text links: no border, no background, `color: inherit`, `cursor: pointer`, `text-decoration: underline`. If a `.link` button style already exists, reuse it and add nothing.

Run: `cd web && pnpm lint && pnpm test`
Expected: PASS, coverage ≥ 95%

- [ ] **Step 8: Run all the suites**

Run: `cd engine && uv run pytest -q && cd ../api && uv run pytest -q`
Expected: PASS

Update the commented auth line in `deploy/config/coglossa.example.yaml` to:

```yaml
# auth: { mode: trusted_header, header: X-Remote-User }   # behind an authenticating proxy
#   proxy_secret_env: COGLOSSA_PROXY_SECRET   # refuse requests without the proxy's secret
#   logout_url: /logout                       # where the web app's "Sign out" posts
```

- [ ] **Step 9: Commit**

```bash
git add engine api schema web deploy/config/coglossa.example.yaml
git commit -m "auth: optional proxy secret and sign-out link for a front-door proxy"
```

---

### Task 3: Kicking a worker (`worker.kick_url`) and the pending-job check

**Files:**
- Create: `engine/coglossa/worker/kick.py`
- Modify: `engine/coglossa/config.py` (`WorkerConfig`, line 136; `validate_config`)
- Modify: `engine/coglossa/worker/app.py` (add `has_due_jobs`)
- Modify: `engine/coglossa/doctor.py` (add a `worker` line when a kick URL is set)
- Modify: `api/coglossa_api/app.py` (wrap the queue in the lifespan)
- Test: `engine/tests/test_kick.py` (new), `engine/tests/test_worker*.py` (the file that tests `_queue_settled` and burst mode), `engine/tests/test_config_m3.py`, the doctor test file, `api/tests/test_app.py`

**Interfaces:**
- Consumes: `coglossa.service.JobQueue` (protocol: `async defer(task, *, lock=None, **kwargs)`)
- Produces:
  - `coglossa.worker.kick.HttpKicker(url: str, token: str | None, *, min_interval_s: float = 2.0, timeout_s: float = 5.0, transport: httpx.AsyncBaseTransport | None = None, clock: Callable[[], float] = time.monotonic)` with `kick() -> None` (schedules the POST, never raises), `drain() -> None` (awaits scheduled POSTs; for tests) and `aclose() -> None`
  - `KickingQueue(inner: JobQueue, kicker: HttpKicker)`, which implements `JobQueue`
  - `build_kicker(cfg: WorkerConfig) -> HttpKicker | None`
  - `coglossa.worker.app.has_due_jobs(url: str) -> bool` (async)
  - `WorkerConfig.kick_url: str | None`, `WorkerConfig.kick_token_env: str | None`
- Task 6 calls `has_due_jobs` from the Modal cron. Its kick endpoint checks the bearer token this task sends.

- [ ] **Step 1: Write the failing tests**

Create `engine/tests/test_kick.py`:

```python
import asyncio
import logging

import httpx

from coglossa.config import WorkerConfig
from coglossa.testing.queue import RecordingQueue
from coglossa.worker.kick import HttpKicker, KickingQueue, build_kicker


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _recorder(status: int = 200, delay: float = 0.0):
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if delay:
            await asyncio.sleep(delay)
        return httpx.Response(status)

    return seen, httpx.MockTransport(handler)


async def test_kick_posts_with_the_token():
    seen, transport = _recorder()
    kicker = HttpKicker("https://kick.example/", "tok", transport=transport)
    kicker.kick()
    await kicker.drain()
    assert [(r.method, str(r.url)) for r in seen] == [("POST", "https://kick.example/")]
    assert seen[0].headers["authorization"] == "Bearer tok"
    await kicker.aclose()


async def test_kicks_close_together_are_merged():
    seen, transport = _recorder()
    clock = Clock()
    kicker = HttpKicker("https://k/", None, transport=transport, clock=clock)
    kicker.kick()
    clock.now += 1.0
    kicker.kick()  # within min_interval_s: merged
    clock.now += 2.5
    kicker.kick()
    await kicker.drain()
    assert len(seen) == 2 and "authorization" not in seen[0].headers
    await kicker.aclose()


async def test_a_failed_kick_is_logged_not_raised(caplog):
    seen, transport = _recorder(status=503)
    kicker = HttpKicker("https://k/", "t", transport=transport)
    with caplog.at_level(logging.WARNING, logger="coglossa.worker"):
        kicker.kick()
        await kicker.drain()
    assert "worker kick failed" in caplog.text and "503" in caplog.text

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    down = HttpKicker("https://k/", "t", transport=httpx.MockTransport(refuse))
    down.kick()
    await down.drain()  # no exception
    await kicker.aclose()
    await down.aclose()


async def test_queue_defers_then_kicks_without_waiting_for_the_kick():
    seen, transport = _recorder(delay=0.5)
    kicker = HttpKicker("https://k/", "t", transport=transport)
    inner = RecordingQueue()
    queue = KickingQueue(inner, kicker)
    async with asyncio.timeout(0.2):  # a slow kick endpoint does not hold up the request
        await queue.defer("run_job", lock="run:1", run_id="1")
    assert inner.jobs[-1][0] == "run_job"
    await kicker.drain()
    assert len(seen) == 1
    await kicker.aclose()


def test_build_kicker(monkeypatch):
    assert build_kicker(WorkerConfig()) is None
    monkeypatch.setenv("KICK_TOKEN", "t")
    kicker = build_kicker(
        WorkerConfig(kick_url="https://k/", kick_token_env="KICK_TOKEN")  # type: ignore[call-arg]
    )
    assert kicker is not None and kicker.token == "t"
```

Read `engine/coglossa/testing/queue.py` first and match `RecordingQueue`'s real attribute and record shape, adjusting `inner.jobs[-1][0]` accordingly. If `defer` raises in the inner queue, the kick must not happen; add that test with a queue whose `defer` raises `RuntimeError`.

Add to the config tests: `WorkerConfig().kick_url is None`; a `kick_url` that does not start with `http://` or `https://` is a `ValidationError`; a `kick_token_env` naming an unset variable is the error `worker: environment variable X is not set`.

Add to the worker tests, using the test database exactly as the `_queue_settled` tests do:
- `has_due_jobs(url)` is False on an empty queue
- it is True after a job is deferred
- it is False for a job scheduled an hour ahead.

Add to `api/tests/test_app.py`: with `worker: {kick_url: ..., kick_token_env: ...}` set and the env var set, the app's `ctx.queue` is a `KickingQueue`; without them it is not. Check this through the lifespan, as the existing app tests do.

Add to the doctor tests: with a kick URL, doctor has a `worker` PASS line `kicks https://k/ when a job is queued`. Without one, there is no `worker` line.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd engine && uv run pytest tests/test_kick.py -q --no-cov`
Expected: FAIL with `ModuleNotFoundError: coglossa.worker.kick`

- [ ] **Step 3: Implement the config**

In `WorkerConfig`, add:

```python
    # POSTed after each queued job, so an on-demand worker starts (hosted preview); unset: an
    # always-on worker polls the queue
    kick_url: str | None = None
    kick_token_env: str | None = None  # sent as "Authorization: Bearer <value>"

    @field_validator("kick_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("must be an http:// or https:// URL")
        return value
```

In `validate_config`, add:

```python
    if cfg.worker.kick_token_env and not os.environ.get(cfg.worker.kick_token_env):
        add("error", f"worker: environment variable {cfg.worker.kick_token_env} is not set")
```

- [ ] **Step 4: Implement `kick.py`**

```python
"""Starting an on-demand worker (hosted preview spec §4.3). After a job is queued the API posts
to `worker.kick_url`; the POST runs in the background and a failure is only logged, because a
periodic kick (the cron) also starts workers."""

import asyncio
import logging
import os
import time
from collections.abc import Callable

import httpx

from coglossa.config import WorkerConfig
from coglossa.service import JobQueue

log = logging.getLogger("coglossa.worker")


class HttpKicker:
    def __init__(
        self,
        url: str,
        token: str | None,
        *,
        min_interval_s: float = 2.0,
        timeout_s: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self.token = token
        self._min_interval = min_interval_s
        self._clock = clock
        self._last: float | None = None
        self._pending: set[asyncio.Task[None]] = set()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.AsyncClient(timeout=timeout_s, headers=headers, transport=transport)

    def kick(self) -> None:
        now = self._clock()
        if self._last is not None and now - self._last < self._min_interval:
            return  # one worker start covers jobs queued moments apart
        self._last = now
        task = asyncio.get_running_loop().create_task(self._post())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _post(self) -> None:
        try:
            response = await self._client.post(self.url)
            response.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("worker kick failed: %s", " ".join(f"{type(e).__name__}: {e}".split()))

    async def drain(self) -> None:
        await asyncio.gather(*self._pending)

    async def aclose(self) -> None:
        await self.drain()
        await self._client.aclose()


class KickingQueue:
    """A JobQueue that kicks a worker after each job it queues."""

    def __init__(self, inner: JobQueue, kicker: HttpKicker) -> None:
        self.inner = inner
        self.kicker = kicker

    async def defer(self, task: str, *, lock: str | None = None, **kwargs: str) -> None:
        await self.inner.defer(task, lock=lock, **kwargs)
        self.kicker.kick()


def build_kicker(cfg: WorkerConfig) -> HttpKicker | None:
    if cfg.kick_url is None:
        return None
    token = os.environ.get(cfg.kick_token_env) if cfg.kick_token_env else None
    return HttpKicker(cfg.kick_url, token)
```

In `engine/coglossa/worker/app.py`, after `_queue_settled`:

```python
async def has_due_jobs(url: str) -> bool:
    """Whether any job is waiting to run now: the on-demand cron starts a worker only then."""
    async with open_app(url) as opened:
        row = await opened.connector.execute_query_one_async(_PENDING_SQL, queue=QUEUE)
    return bool(row["due"])
```

In the doctor's `run_doctor`, after the scorer check:

```python
    if cfg.worker.kick_url:
        checks.append(
            _check("worker", CheckStatus.PASS, f"kicks {cfg.worker.kick_url} when a job is queued")
        )
```

In the API lifespan in `create_app`, wrap the queue:

```python
            async with open_queue(db.url) as queue:
                kicker = build_kicker(cfg.worker)
                if kicker is not None:
                    queue = KickingQueue(queue, kicker)
                app.state.ctx = ApiContext(..., queue=queue)
                try:
                    yield
                finally:
                    if kicker is not None:
                        await kicker.aclose()
```

Keep the existing `ApiContext(...)` arguments exactly; only `queue=` changes.

- [ ] **Step 5: Run all the suites**

Run: `cd engine && uv run ruff check . && uv run ruff format --check . && uv run pytest -q && cd ../api && uv run pytest -q`
Expected: PASS, coverage ≥ 95%

- [ ] **Step 6: Commit**

```bash
git add engine api
git commit -m "worker: optional kick URL to start an on-demand worker; has_due_jobs"
```

---

### Task 4: Scorer bearer token, and an app factory for the scorer

**Files:**
- Modify: `engine/coglossa/config.py` (`ScorerConfig`, line 208; `validate_config`)
- Modify: `engine/coglossa/scoring/client.py` (`HttpScorer.__init__`, `build_scorer`)
- Modify: `scorer/coglossa_scorer/app.py` (`create_app`)
- Modify: `scorer/coglossa_scorer/cli.py` (split `build_app` out of `main`)
- Test: `engine/tests/test_scoring_client*.py` (the HttpScorer tests), `engine/tests/test_config*.py`, `scorer/tests/test_app.py`, `scorer/tests/test_cli.py`

**Interfaces:**
- Produces:
  - `ScorerConfig.api_key_env: str | None = None`
  - `HttpScorer(..., api_key: str | None = None)`
  - `coglossa_scorer.app.create_app(qe, embedder, *, device: str, token: str | None = None)`
  - `coglossa_scorer.cli.build_app(argv: list[str] | None = None) -> FastAPI`. It raises `SystemExit` on argument errors, as argparse does, and on model-load failure prints `error: cannot load model: …` and raises `SystemExit(1)`.
  - `main` calls `build_app` then `uvicorn.run`.
  - The service reads `COGLOSSA_SCORER_TOKEN`.
- Task 6's Modal scorer calls `build_app([...])`.

- [ ] **Step 1: Write the failing tests**

Engine, next to the existing `HttpScorer` tests, which use `httpx.MockTransport`:

```python
async def test_http_scorer_sends_the_bearer_token():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "ok", "device": "cpu"})

    scorer = HttpScorer("http://s/", api_key="tok", transport=httpx.MockTransport(handler))
    await scorer.health()
    assert seen[0].headers["authorization"] == "Bearer tok"
    await scorer.aclose()


async def test_http_scorer_sends_no_token_by_default():
    # same, without api_key: no authorization header


def test_build_scorer_reads_the_key(monkeypatch, make_config):
    monkeypatch.setenv("SCORER_TOKEN", "tok")
    cfg = make_config(scorer={"url": "http://s/", "api_key_env": "SCORER_TOKEN"})
    scorer = build_scorer(cfg)
    assert scorer is not None and scorer._client.headers["authorization"] == "Bearer tok"
```

Config: a `scorer.api_key_env` naming an unset variable is the error `scorer: environment variable X is not set`.

Scorer service, in `scorer/tests/test_app.py` and following its fakes for `qe` and `embedder`:
- with `token="tok"`, `GET /health` without a token returns 200
- `POST /v1/qe` without a token returns 401 with detail `missing or wrong scorer token`
- `POST /v1/qe` with `Authorization: Bearer wrong` returns 401
- with `Bearer tok` it returns 200 and scores
- with `token=None`, everything works without a header, as today.

`scorer/tests/test_cli.py`:
- `build_app` with a token in `COGLOSSA_SCORER_TOKEN` (monkeypatched) returns an app that refuses `/v1/qe` without it. Monkeypatch the model loaders `MetricXQE.from_pretrained` and `LabseEmbedder.from_pretrained`, as the existing CLI tests do.
- an empty `COGLOSSA_SCORER_TOKEN` means no token.
- `main` still calls `uvicorn.run` with the built app. Keep the existing CLI tests green.

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `cd engine && uv run pytest -q --no-cov -k "token or api_key"`, then `cd ../scorer && uv run pytest -q --no-cov`
Expected: FAIL

- [ ] **Step 3: Implement the engine side**

In `ScorerConfig`, add `api_key_env: str | None = None  # sent as "Authorization: Bearer <value>"`.

In `validate_config`, add:

```python
    if cfg.scorer is not None and cfg.scorer.api_key_env and not os.environ.get(
        cfg.scorer.api_key_env
    ):
        add("error", f"scorer: environment variable {cfg.scorer.api_key_env} is not set")
```

`HttpScorer.__init__` gets `api_key: str | None = None`, and passes `headers={"Authorization": f"Bearer {api_key}"} if api_key else None` to `httpx.AsyncClient`. `build_scorer` passes `api_key=os.environ.get(sc.api_key_env) if sc.api_key_env else None`. Add `import os`.

- [ ] **Step 4: Implement the scorer side**

In `create_app`, add the `token: str | None = None` parameter. After `app = FastAPI(...)`:

```python
    if token is not None:
        expected = f"Bearer {token}".encode()

        @app.middleware("http")
        async def require_token(request: Request, call_next: Any) -> Any:
            # /health stays open: liveness checks and the engine's probe carry no token
            given = request.headers.get("authorization", "").encode()
            if request.url.path != "/health" and not hmac.compare_digest(given, expected):
                return JSONResponse({"detail": "missing or wrong scorer token"}, status_code=401)
            return await call_next(request)
```

The scorer returns plain JSON responses, not streams, so `@app.middleware("http")` is fine here. Add these imports: `hmac`; `Request` from fastapi; `JSONResponse` from `fastapi.responses`.

In `cli.py`, move the body of `main` up to the model loading into `build_app(argv)`. It parses the args, sets the offline env defaults, loads the models (on failure, prints the existing error line and `raise SystemExit(1)`), reads `token = os.environ.get("COGLOSSA_SCORER_TOKEN") or None`, and returns `create_app(qe, embedder, device=args.device, token=token)`. `main` becomes:

```python
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        app = build_app(argv)
    except SystemExit as e:
        return int(e.code or 0)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0
```

Keep the existing `parser.error` for "load at least one model" inside `build_app`. It raises `SystemExit(2)`, which `main` returns as 2. Before changing it, check what the existing CLI tests expect for that case; they define the contract.

- [ ] **Step 5: Run all the suites**

Run: `cd engine && uv run pytest -q && cd ../scorer && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
Expected: PASS, coverage ≥ 95%

- [ ] **Step 6: Commit**

```bash
git add engine scorer
git commit -m "scorer: optional bearer token (client and service); scorer build_app factory"
```

---

### Task 5: Vercel front door (`deploy/hosted/vercel/`)

**Files:**
- Create: `deploy/hosted/vercel/package.json`, `pnpm-lock.yaml`, `tsconfig.json`, `vitest.config.ts`, `vercel.json`, `.gitignore` (`public/`, `node_modules/`, `coverage/`, `.vercel/`)
- Create: `deploy/hosted/vercel/lib/auth.ts`, `lib/route.ts`, `middleware.ts`
- Create: `deploy/hosted/vercel/static/login.html`
- Create: `deploy/hosted/vercel/build.sh`, `deploy/hosted/add-user.mjs`
- Test: `deploy/hosted/vercel/lib/auth.test.ts`, `lib/route.test.ts`
- Modify: `.github/workflows/ci.yml` (new job `hosted-web`)

**Interfaces:**
- Consumes: the API header names (Global Constraints) and `logout_url: /logout` (Task 2)
- Produces: the Vercel env contract `COGLOSSA_USERS` (JSON object name → `pbkdf2_sha256$<iter>$<salt b64>$<hash b64>`), `COGLOSSA_SESSION_SECRET` (≥ 32 chars) and `COGLOSSA_API_ORIGIN` (e.g. `https://ws--coglossa-api.modal.run`, no trailing slash). Also `build.sh`, which leaves the deployable site in `deploy/hosted/vercel/public/`.

- [ ] **Step 1: Scaffold the package**

`package.json`:

```json
{
  "name": "coglossa-front-door",
  "private": true,
  "type": "module",
  "packageManager": "pnpm@10.32.1",
  "engines": { "node": ">=22" },
  "scripts": {
    "test": "vitest run --coverage",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": { "@vercel/functions": "<latest 2.x or 3.x on npm, exact>" },
  "devDependencies": {
    "@vitest/coverage-v8": "<same major as web/>",
    "typescript": "<same as web/>",
    "vitest": "<same as web/>"
  }
}
```

Use the exact versions `web/package.json` pins for vitest, coverage-v8 and typescript. For `@vercel/functions`, pin the newest release that is at least two weeks old (`pnpm view @vercel/functions time --json`). Then run `pnpm install` to produce the lockfile.

`vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["lib/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["lib/**/*.ts"],
      exclude: ["lib/**/*.test.ts"],
      thresholds: { lines: 95, branches: 95, functions: 95, statements: 95 },
    },
  },
});
```

`tsconfig.json`: `"target": "ES2022"`, `"module": "ESNext"`, `"moduleResolution": "Bundler"`, `"strict": true`, `"lib": ["ES2022", "DOM"]`, `"types": ["node"]`, `"noEmit": true`, and include `["lib", "middleware.ts"]`. Add `@types/node` to devDependencies at the major matching Node 22.

- [ ] **Step 2: Write the failing auth tests**

`lib/auth.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  hashPassword,
  parseUsers,
  readCookie,
  readSession,
  signSession,
  verifyPassword,
} from "./auth";

const SECRET = "x".repeat(32);

describe("passwords", () => {
  it("verifies a hash it made and rejects a wrong password", async () => {
    const encoded = await hashPassword("correct horse", { iterations: 1000 });
    expect(encoded).toMatch(/^pbkdf2_sha256\$1000\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+$/);
    expect(await verifyPassword("correct horse", encoded)).toBe(true);
    expect(await verifyPassword("wrong", encoded)).toBe(false);
  });
  it("rejects malformed hashes", async () => {
    for (const bad of ["", "md5$1$a$b", "pbkdf2_sha256$x$a$b", "pbkdf2_sha256$1000$a"]) {
      expect(await verifyPassword("p", bad)).toBe(false);
    }
  });
});

describe("users", () => {
  it("parses the JSON map and refuses anything else", () => {
    expect(parseUsers('{"ana": "pbkdf2_sha256$1$a$b"}').get("ana")).toBe("pbkdf2_sha256$1$a$b");
    expect(parseUsers(undefined).size).toBe(0);
    expect(parseUsers("not json").size).toBe(0);
    expect(parseUsers('["ana"]').size).toBe(0);
    expect(parseUsers('{"ana": 3}').size).toBe(0);
  });
});

describe("sessions", () => {
  it("round-trips a name until it expires", async () => {
    const value = await signSession("ana", 2_000, SECRET);
    expect(await readSession(value, SECRET, 1_000)).toBe("ana");
    expect(await readSession(value, SECRET, 2_001)).toBeNull();
  });
  it("rejects tampering, a wrong secret and garbage", async () => {
    const value = await signSession("ana", 2_000, SECRET);
    const [body, mac] = value.split(".");
    const forged = btoa("bob|2000").replace(/=+$/, "") + "." + mac;
    expect(await readSession(forged, SECRET, 1_000)).toBeNull();
    expect(await readSession(value, "y".repeat(32), 1_000)).toBeNull();
    for (const bad of ["", "abc", `${body}.`, ".x", "a.b.c"]) {
      expect(await readSession(bad, SECRET, 1_000)).toBeNull();
    }
  });
  it("keeps a name containing the separator intact", async () => {
    const value = await signSession("a|b", 2_000, SECRET);
    expect(await readSession(value, SECRET, 1_000)).toBe("a|b");
  });
});

describe("cookies", () => {
  it("finds one cookie among several", () => {
    expect(readCookie("a=1; cg_session=xyz; b=2", "cg_session")).toBe("xyz");
    expect(readCookie(null, "cg_session")).toBeNull();
    expect(readCookie("a=1", "cg_session")).toBeNull();
  });
});
```

- [ ] **Step 3: Implement `lib/auth.ts`**

```ts
/** Sign-in for the hosted preview (spec §5): PBKDF2 password hashes and HMAC-signed session
 * cookies, using WebCrypto only, so it runs in Vercel Routing Middleware. */

const enc = new TextEncoder();
const ITERATIONS = 210_000;

const b64 = (bytes: ArrayBuffer | Uint8Array): string =>
  btoa(String.fromCharCode(...new Uint8Array(bytes)));
const unb64 = (text: string): Uint8Array => Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
const b64url = (text: string): string =>
  btoa(String.fromCharCode(...enc.encode(text)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
const unb64url = (text: string): string =>
  new TextDecoder().decode(unb64(text.replace(/-/g, "+").replace(/_/g, "/")));

async function pbkdf2(password: string, salt: Uint8Array, iterations: number): Promise<ArrayBuffer> {
  const key = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, [
    "deriveBits",
  ]);
  return crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations }, key, 256);
}

function equal(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

export async function hashPassword(
  password: string,
  { iterations = ITERATIONS }: { iterations?: number } = {},
): Promise<string> {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  return `pbkdf2_sha256$${iterations}$${b64(salt)}$${b64(await pbkdf2(password, salt, iterations))}`;
}

export async function verifyPassword(password: string, encoded: string): Promise<boolean> {
  const parts = encoded.split("$");
  if (parts.length !== 4 || parts[0] !== "pbkdf2_sha256") return false;
  const iterations = Number(parts[1]);
  if (!Number.isInteger(iterations) || iterations < 1) return false;
  try {
    const got = new Uint8Array(await pbkdf2(password, unb64(parts[2]), iterations));
    return equal(got, unb64(parts[3]));
  } catch {
    return false; // a salt or hash that is not base64
  }
}

export function parseUsers(json: string | undefined): Map<string, string> {
  try {
    const data: unknown = JSON.parse(json ?? "");
    if (typeof data !== "object" || data === null || Array.isArray(data)) return new Map();
    const entries = Object.entries(data as Record<string, unknown>);
    if (entries.some(([, v]) => typeof v !== "string")) return new Map();
    return new Map(entries as [string, string][]);
  } catch {
    return new Map();
  }
}

async function mac(body: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return b64(await crypto.subtle.sign("HMAC", key, enc.encode(body)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

/** `<base64url(name|expiresAtMs)>.<HMAC>`; the name is everything before the last "|". */
export async function signSession(name: string, expiresAt: number, secret: string): Promise<string> {
  const body = b64url(`${name}|${expiresAt}`);
  return `${body}.${await mac(body, secret)}`;
}

export async function readSession(
  value: string,
  secret: string,
  now: number,
): Promise<string | null> {
  const parts = value.split(".");
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
  const expected = await mac(parts[0], secret);
  if (!equal(enc.encode(expected), enc.encode(parts[1]))) return null;
  let decoded: string;
  try {
    decoded = unb64url(parts[0]);
  } catch {
    return null;
  }
  const cut = decoded.lastIndexOf("|");
  const expiresAt = Number(decoded.slice(cut + 1));
  if (cut < 1 || !Number.isFinite(expiresAt) || now > expiresAt) return null;
  return decoded.slice(0, cut);
}

export function readCookie(header: string | null, name: string): string | null {
  for (const part of (header ?? "").split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return null;
}
```

Run: `cd deploy/hosted/vercel && pnpm test`
Expected: the auth tests PASS. Coverage fails until `route.ts` exists; that is fine at this step.

- [ ] **Step 4: Write the failing routing tests**

`lib/route.ts` exports `decide(request: Request, env: Env, now: number): Promise<Decision>`, a pure function, and `forward()`. The middleware applies the decision. Tests in `lib/route.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { hashPassword, signSession } from "./auth";
import { decide, type Env, SESSION_COOKIE } from "./route";

const SECRET = "s".repeat(32);
const NOW = 1_000_000;

async function env(): Promise<Env> {
  return {
    users: new Map([["ana", await hashPassword("pw", { iterations: 1000 })]]),
    sessionSecret: SECRET,
    apiOrigin: "https://ws--coglossa-api.modal.run",
    proxySecret: "p".repeat(32),
    failDelayMs: 0,
  };
}

async function signedIn(path: string, init: RequestInit = {}, name = "ana"): Promise<Request> {
  const cookie = `${SESSION_COOKIE}=${await signSession(name, NOW + 60_000, SECRET)}`;
  const headers = new Headers(init.headers);
  headers.set("cookie", cookie);
  return new Request(`https://preview.example${path}`, { ...init, headers });
}

describe("decide", () => {
  it("lets the login page and its stylesheet through", async () => {
    for (const path of ["/login", "/login.html"]) {
      expect((await decide(new Request(`https://p.example${path}`), await env(), NOW)).kind).toBe(
        "next",
      );
    }
  });

  it("sends a signed-out page view to /login", async () => {
    const d = await decide(new Request("https://p.example/projects"), await env(), NOW);
    expect(d).toEqual({ kind: "redirect", location: "/login", setCookie: null });
  });

  it("answers a signed-out API call with 401", async () => {
    const d = await decide(new Request("https://p.example/api/me"), await env(), NOW);
    expect(d.kind).toBe("unauthorized");
  });

  it("signs in with the right password and sets a 7-day cookie", async () => {
    const body = new URLSearchParams({ name: "ana", password: "pw" });
    const d = await decide(
      new Request("https://p.example/login", { method: "POST", body }),
      await env(),
      NOW,
    );
    expect(d.kind).toBe("redirect");
    if (d.kind !== "redirect") throw new Error();
    expect(d.location).toBe("/");
    expect(d.setCookie).toMatch(
      /^cg_session=[^;]+; Path=\/; HttpOnly; Secure; SameSite=Lax; Max-Age=604800$/,
    );
  });

  it("refuses a wrong password or unknown user", async () => {
    for (const [name, password] of [
      ["ana", "nope"],
      ["bob", "pw"],
      ["", ""],
    ]) {
      const body = new URLSearchParams({ name, password });
      const d = await decide(
        new Request("https://p.example/login", { method: "POST", body }),
        await env(),
        NOW,
      );
      expect(d).toEqual({ kind: "redirect", location: "/login?failed=1", setCookie: null });
    }
  });

  it("signs out", async () => {
    const d = await decide(await signedIn("/logout", { method: "POST" }), await env(), NOW);
    expect(d).toEqual({
      kind: "redirect",
      location: "/login",
      setCookie: "cg_session=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0",
    });
  });

  it("forwards API calls with its own identity headers, stripping the client's", async () => {
    const d = await decide(
      await signedIn("/api/projects?limit=5", {
        headers: { "x-remote-user": "mallory", "x-coglossa-proxy-secret": "guess" },
      }),
      await env(),
      NOW,
    );
    expect(d.kind).toBe("forward");
    if (d.kind !== "forward") throw new Error();
    expect(d.url).toBe("https://ws--coglossa-api.modal.run/api/projects?limit=5");
    expect(d.headers.get("x-remote-user")).toBe("ana");
    expect(d.headers.get("x-coglossa-proxy-secret")).toBe("p".repeat(32));
  });

  it("serves the web app to a signed-in user", async () => {
    expect((await decide(await signedIn("/projects/1"), await env(), NOW)).kind).toBe("next");
  });

  it("refuses a cookie whose user has been removed", async () => {
    const d = await decide(await signedIn("/api/me", {}, "carol"), await env(), NOW);
    expect(d.kind).toBe("unauthorized");
  });

  it("refuses everything when the front door is misconfigured", async () => {
    const broken = { ...(await env()), sessionSecret: "" };
    const d = await decide(await signedIn("/projects"), broken, NOW);
    expect(d.kind).toBe("misconfigured");
  });
});
```

Add a test that `readEnv()` reads `process.env` (`COGLOSSA_USERS`, `COGLOSSA_SESSION_SECRET`, `COGLOSSA_API_ORIGIN`, `COGLOSSA_PROXY_SECRET`), trims a trailing `/` from the origin, and defaults `failDelayMs` to 1000. Add a test that a session secret shorter than 32 characters, or an empty origin or proxy secret, makes `decide` return `misconfigured` with no cookie check.

- [ ] **Step 5: Implement `lib/route.ts` and `middleware.ts`**

```ts
/** Routing for the front door (spec §3, §5). Pure: middleware.ts turns a Decision into a
 * response. */
import { parseUsers, readCookie, readSession, signSession, verifyPassword } from "./auth";

export const SESSION_COOKIE = "cg_session";
const WEEK_S = 7 * 24 * 3600;
const COOKIE_ATTRS = "Path=/; HttpOnly; Secure; SameSite=Lax";
const OPEN_PATHS = new Set(["/login", "/login.html"]);
const IDENTITY_HEADERS = ["x-remote-user", "x-coglossa-proxy-secret"];

export interface Env {
  users: Map<string, string>;
  sessionSecret: string;
  apiOrigin: string;
  proxySecret: string;
  failDelayMs: number;
}

export type Decision =
  | { kind: "next" }
  | { kind: "redirect"; location: string; setCookie: string | null }
  | { kind: "unauthorized" }
  | { kind: "misconfigured" }
  | { kind: "forward"; url: string; headers: Headers };

export function readEnv(env: Record<string, string | undefined> = process.env): Env {
  return {
    users: parseUsers(env.COGLOSSA_USERS),
    sessionSecret: env.COGLOSSA_SESSION_SECRET ?? "",
    apiOrigin: (env.COGLOSSA_API_ORIGIN ?? "").replace(/\/+$/, ""),
    proxySecret: env.COGLOSSA_PROXY_SECRET ?? "",
    failDelayMs: 1000,
  };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function signIn(request: Request, env: Env, now: number): Promise<Decision> {
  const form = await request.formData().catch(() => new FormData());
  const name = String(form.get("name") ?? "").trim();
  const encoded = env.users.get(name);
  if (!encoded || !(await verifyPassword(String(form.get("password") ?? ""), encoded))) {
    await sleep(env.failDelayMs); // slows password guessing; no lockout at demo scale
    return { kind: "redirect", location: "/login?failed=1", setCookie: null };
  }
  const value = await signSession(name, now + WEEK_S * 1000, env.sessionSecret);
  return {
    kind: "redirect",
    location: "/",
    setCookie: `${SESSION_COOKIE}=${value}; ${COOKIE_ATTRS}; Max-Age=${WEEK_S}`,
  };
}

export async function decide(request: Request, env: Env, now: number): Promise<Decision> {
  const url = new URL(request.url);
  if (OPEN_PATHS.has(url.pathname) && request.method !== "POST") return { kind: "next" };
  if (env.sessionSecret.length < 32 || !env.apiOrigin || !env.proxySecret) {
    return { kind: "misconfigured" };
  }
  if (url.pathname === "/login") return signIn(request, env, now);
  if (url.pathname === "/logout" && request.method === "POST") {
    return {
      kind: "redirect",
      location: "/login",
      setCookie: `${SESSION_COOKIE}=; ${COOKIE_ATTRS}; Max-Age=0`,
    };
  }
  const cookie = readCookie(request.headers.get("cookie"), SESSION_COOKIE);
  const name = cookie ? await readSession(cookie, env.sessionSecret, now) : null;
  const user = name !== null && env.users.has(name) ? name : null; // removed users are out
  const isApi = url.pathname === "/api" || url.pathname.startsWith("/api/");
  if (user === null) {
    return isApi
      ? { kind: "unauthorized" }
      : { kind: "redirect", location: "/login", setCookie: null };
  }
  if (!isApi) return { kind: "next" };
  return forward(request, url, user, env);
}

/** The /api rewrite. Spike S1 (plan Task 8) decides whether this stays a header-adding
 * rewrite; its fallback is a streaming proxy function, swapped in here. */
export function forward(request: Request, url: URL, user: string, env: Env): Decision {
  const headers = new Headers(request.headers);
  for (const h of IDENTITY_HEADERS) headers.delete(h);
  headers.set("x-remote-user", user);
  headers.set("x-coglossa-proxy-secret", env.proxySecret);
  return { kind: "forward", url: `${env.apiOrigin}${url.pathname}${url.search}`, headers };
}
```

`middleware.ts`, thin glue excluded from the coverage include list:

```ts
import { next, rewrite } from "@vercel/functions";
import { decide, readEnv } from "./lib/route";

export const config = { runtime: "nodejs" };

export default async function middleware(request: Request): Promise<Response> {
  const d = await decide(request, readEnv(), Date.now());
  switch (d.kind) {
    case "next":
      return next();
    case "redirect": {
      const headers = new Headers({ location: d.location });
      if (d.setCookie) headers.set("set-cookie", d.setCookie);
      return new Response(null, { status: 303, headers });
    }
    case "unauthorized":
      return Response.json({ detail: "signed out: sign in again" }, { status: 401 });
    case "misconfigured":
      return new Response("Coglossa preview is not configured.", { status: 503 });
    case "forward":
      return rewrite(d.url, { request: { headers: d.headers } });
  }
}
```

Check the installed `@vercel/functions` types for `rewrite`'s second parameter. If the version has no `request.headers` option, this compiles with an error; leave it, note it in the report, and the controller resolves it with spike S1 in Task 8.

- [ ] **Step 6: Add the login page, `vercel.json` and the build script**

`static/login.html` is a single page with no JavaScript and inline CSS, using system fonts (the web app's Noto fonts load from its own bundle). It has:
- the title "Coglossa — Sign in" and the heading "Coglossa"
- a form `method="post" action="/login"` with labelled inputs `name` (autocomplete `username`) and `password` (autocomplete `current-password`) and a "Sign in" button
- a paragraph `<p class="error" hidden>Wrong name or password.</p>`, shown with a 3-line inline `<script>` that unhides it when `location.search` includes `failed=1`. This is the one allowed script, written inline.

Centre the form in a card about 22rem wide; it must work at phone width.

`vercel.json`:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "buildCommand": "",
  "outputDirectory": "public",
  "cleanUrls": false,
  "rewrites": [
    { "source": "/login", "destination": "/login.html" },
    { "source": "/((?!api/|assets/).*)", "destination": "/index.html" }
  ]
}
```

`build.sh`, run from anywhere (`set -euo pipefail`):

```bash
#!/usr/bin/env bash
# Builds the front door's site into deploy/hosted/vercel/public: the web app plus the login page.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
(cd "$root/web" && pnpm install --frozen-lockfile && pnpm build)
rm -rf "$here/public"
mkdir -p "$here/public"
cp -R "$root/web/dist/." "$here/public/"
cp "$here/static/login.html" "$here/public/login.html"
```

`deploy/hosted/add-user.mjs`: a Node script, `node deploy/hosted/add-user.mjs <name>`. It reads the password from stdin (twice, without echo when stdin is a TTY, using `readline` with muted output), then prints the line `"<name>": "<hash>"` to add to `COGLOSSA_USERS`. It imports `hashPassword` from `./vercel/lib/auth.ts` through Node 22's `--experimental-strip-types`. If that flag is not usable, duplicate the 10-line PBKDF2 function there and add a Vitest test that both produce hashes `verifyPassword` accepts. Run shellcheck on `build.sh` (the repo's `make deploy-check` runs shellcheck on `deploy/*.sh` and `deploy/tests/*.sh`; add `deploy/hosted/vercel/*.sh` to that Makefile line).

- [ ] **Step 7: Add the CI job**

In `.github/workflows/ci.yml`, add a job `hosted-web` modelled on the `web` job (pnpm setup, Node 22) with `working-directory: deploy/hosted/vercel` and the steps `pnpm install --frozen-lockfile`, `pnpm typecheck` and `pnpm test`. If `pnpm typecheck` fails only because of the `rewrite` options type (see Step 5), the controller rules on it in Task 8. Do not weaken the check.

- [ ] **Step 8: Verify and commit**

Run: `cd deploy/hosted/vercel && pnpm typecheck && pnpm test && cd ../../.. && make deploy-check`
Expected: PASS, coverage ≥ 95% on `lib/`

```bash
git add deploy/hosted/vercel deploy/hosted/add-user.mjs .github/workflows/ci.yml Makefile
git commit -m "hosted: Vercel front door (sign-in, sessions, /api rewrite with identity headers)"
```

---

### Task 6: Modal backend (`deploy/hosted/modal/`) and the hosted config

**Files:**
- Create: `deploy/hosted/coglossa.hosted.yaml` (a template; see Step 1)
- Create: `deploy/hosted/modal/app.py`, `deploy/hosted/modal/settings.py`, `deploy/hosted/modal/upload_weights.py`
- Create: `deploy/hosted/modal/test_settings.py`, `deploy/hosted/modal/requirements.txt` (`modal==<pin>` and `pyyaml`)
- Create: `deploy/hosted/README.md`
- Modify: `.github/workflows/ci.yml` (new job `hosted-modal`)

**Interfaces:**
- Consumes:
  - `coglossa_api.app.create_app(cfg, config_path)`
  - `coglossa.config.load_config(path) -> (cfg, issues)`
  - `coglossa.worker.app.run_worker(cfg, burst=True)` and `has_due_jobs(url)` (Task 3)
  - `coglossa.store.db.database_url(cfg)`
  - `coglossa_scorer.cli.build_app(argv)` (Task 4)
  - the `coglossa db upgrade` and `coglossa doctor` CLIs
- Produces:
  - Modal app `coglossa` with functions `api`, `kick`, `worker`, `cron`, `scorer`, `migrate` and `doctor`
  - web endpoints labelled `coglossa-api`, `coglossa-kick` and `coglossa-scorer`, so their URLs are `https://<workspace>--coglossa-api.modal.run` and so on
  - the Modal Secret contract (spec §7) plus `COGLOSSA_KICK_URL`, `COGLOSSA_SCORER_URL` and `COGLOSSA_S3_ENDPOINT`.

- [ ] **Step 1: Write the hosted config template**

The account-specific URLs are not committed. They live in the Modal Secret, and `settings.render_config` substitutes them at container start. `deploy/hosted/coglossa.hosted.yaml`:

```yaml
# Hosted preview (docs/superpowers/specs/2026-10-14-coglossa-hosted-preview-design.md).
# ${NAME} values come from the Modal Secret coglossa-hosted when the container starts
# (deploy/hosted/modal/settings.py); no secret is in this file.
backends:
  openrouter:
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    max_concurrency: 8
    timeout_s: 300
models:
  gemma-4-26b-a4b:
    backend: openrouter
    name: google/gemma-4-26b-a4b-it
    context_window: 65536
    structured_output: json_schema
    max_output_tokens: 16384
    extra_body:
      provider: { require_parameters: true, order: [coreweave, deepinfra, parasail], quantizations: [bf16, fp8] }
  qwen3.6-35b-a3b:
    backend: openrouter
    name: qwen/qwen3.6-35b-a3b
    context_window: 65536
    structured_output: json_schema
    max_output_tokens: 16384
    extra_body:
      reasoning: { enabled: false }
      provider: { require_parameters: true, order: [coreweave, deepinfra, parasail], quantizations: [bf16, fp8] }
roles:
  literal_translator:    { model: gemma-4-26b-a4b }
  contextual_translator: { model: gemma-4-26b-a4b }
  reviewer:              { model: gemma-4-26b-a4b }
  profiler_guide:        { model: gemma-4-26b-a4b }
  judge:                 { model: qwen3.6-35b-a3b }
  roundtrip:             { model: qwen3.6-35b-a3b }
scorer:
  url: ${COGLOSSA_SCORER_URL}
  api_key_env: COGLOSSA_SCORER_TOKEN
  timeout_s: 600
  max_concurrency: 4
storage:
  kind: s3
  endpoint_url: ${COGLOSSA_S3_ENDPOINT}
  region: us-east-1
  bucket: coglossa-files
worker:
  concurrency: 8
  scorer_wait_s: 300
  kick_url: ${COGLOSSA_KICK_URL}
  kick_token_env: COGLOSSA_KICK_TOKEN
api:
  max_upload_mb: 20
auth:
  mode: trusted_header
  header: X-Remote-User
  proxy_secret_env: COGLOSSA_PROXY_SECRET
  logout_url: /logout
```

Copy the model blocks from `bench/m6-calibration/coglossa.gemma-pipeline.yaml`; that file is the source of truth if anything above differs from it. If the config has a database pool-size setting (check `DatabaseConfig` in `engine/coglossa/config.py`), set it so that API plus two workers stays under 15 connections, and say so in a comment. If there is no such setting, add nothing and note the default pool sizes in the README.

- [ ] **Step 2: Write the failing settings tests**

`deploy/hosted/modal/settings.py` is plain Python, importable without Modal, so CI can test it. `test_settings.py`:

```python
import pytest
import yaml

from settings import HOSTED_TEMPLATE, render_config

ENV = {
    "COGLOSSA_SCORER_URL": "https://ws--coglossa-scorer.modal.run",
    "COGLOSSA_S3_ENDPOINT": "https://ref.storage.supabase.co/storage/v1/s3",
    "COGLOSSA_KICK_URL": "https://ws--coglossa-kick.modal.run",
}


def test_render_fills_every_placeholder(tmp_path):
    out = render_config(HOSTED_TEMPLATE, tmp_path / "c.yaml", ENV)
    text = out.read_text(encoding="utf-8")
    assert "${" not in text
    data = yaml.safe_load(text)
    assert data["worker"]["kick_url"] == ENV["COGLOSSA_KICK_URL"]
    assert data["roles"]["judge"]["model"] != data["roles"]["reviewer"]["model"]


def test_a_missing_value_names_the_variable(tmp_path):
    with pytest.raises(KeyError, match="COGLOSSA_KICK_URL"):
        render_config(HOSTED_TEMPLATE, tmp_path / "c.yaml", {k: v for k, v in ENV.items() if k != "COGLOSSA_KICK_URL"})


def test_the_rendered_config_is_valid(tmp_path, monkeypatch):
    coglossa_config = pytest.importorskip("coglossa.config")
    for var in [
        "OPENROUTER_API_KEY", "COGLOSSA_SCORER_TOKEN", "COGLOSSA_S3_ACCESS_KEY_ID",
        "COGLOSSA_S3_SECRET_ACCESS_KEY", "COGLOSSA_KICK_TOKEN", "COGLOSSA_PROXY_SECRET",
    ]:
        monkeypatch.setenv(var, "x" * 32)
    path = render_config(HOSTED_TEMPLATE, tmp_path / "c.yaml", ENV)
    cfg, issues = coglossa_config.load_config(path)
    assert cfg.storage.kind == "s3" and not [i for i in issues if i.level == "error"]
```

`settings.py`:

```python
"""Hosted preview settings shared by the Modal app and its tests; no Modal import here."""

import os
from collections.abc import Mapping
from pathlib import Path
from string import Template

HERE = Path(__file__).resolve().parent
HOSTED_TEMPLATE = HERE.parent / "coglossa.hosted.yaml"


def render_config(template: Path, out: Path, env: Mapping[str, str] = os.environ) -> Path:
    """The hosted config with ${NAME} filled from the environment (the Modal Secret). A
    missing value raises KeyError naming it, so the container fails at start, not mid-run."""
    out.write_text(Template(template.read_text(encoding="utf-8")).substitute(env), encoding="utf-8")
    return out
```

The CI job (Step 6) runs these tests in the `engine` environment plus pyyaml, so `coglossa.config` is importable and `test_the_rendered_config_is_valid` runs and does not skip.

- [ ] **Step 3: Write `app.py`**

Read the Modal docs (`modal.com/docs/reference`) for the pinned version before writing; the decorator names below are Modal 1.x. Pin `modal` in `requirements.txt` to the newest 1.x release that is at least two weeks old.

```python
"""Coglossa hosted preview on Modal (spec §6): the API, a burst worker, the GPU scorer and a
cron, all scaling to zero. Deploy: modal deploy deploy/hosted/modal/app.py"""

import subprocess
from pathlib import Path

import modal

from settings import HOSTED_TEMPLATE, render_config

REPO = Path(__file__).resolve().parents[3]
CONFIG = Path("/etc/coglossa/coglossa.hosted.yaml")  # the template, baked into the image
RENDERED = Path("/tmp/coglossa.yaml")
IGNORE = [".venv", "**/__pycache__", "tests", "node_modules", "dist", "coverage"]

app = modal.App("coglossa")
secret = modal.Secret.from_name("coglossa-hosted")
weights = modal.Volume.from_name("coglossa-weights", create_if_missing=True)

app_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("uv==0.13.0")
    .add_local_dir(REPO / "engine", "/src/engine", copy=True, ignore=IGNORE)
    .add_local_dir(REPO / "api", "/src/api", copy=True, ignore=IGNORE)
    .run_commands(
        # the locked dependencies, then the two projects, into the system Python Modal runs
        "cd /src/api && uv export --frozen --no-dev --no-emit-project --no-hashes"
        " | grep -v '^-e ' > /tmp/req.txt",
        "uv pip install --system -r /tmp/req.txt /src/engine /src/api",
    )
    .add_local_file(HOSTED_TEMPLATE, str(CONFIG), copy=True)
    .add_local_python_source("settings")
    .env({"COGLOSSA_CONFIG": str(RENDERED)})
)


def _config():
    from coglossa.config import load_config

    render_config(CONFIG, RENDERED)
    cfg, _ = load_config(RENDERED)
    return cfg


@app.function(image=app_image, secrets=[secret], cpu=1.0, memory=2048, scaledown_window=600)
@modal.concurrent(max_inputs=50)
@modal.asgi_app(label="coglossa-api")
def api():
    from coglossa_api.app import create_app

    return create_app(_config(), RENDERED)


@app.function(image=app_image, secrets=[secret], cpu=2.0, memory=4096, timeout=86400, max_containers=2)
def worker():
    import asyncio

    from coglossa.worker.app import run_worker

    asyncio.run(run_worker(_config(), burst=True, install_signal_handlers=False))


@app.function(image=app_image, secrets=[secret], cpu=0.25)
@modal.fastapi_endpoint(method="POST", label="coglossa-kick")
def kick(request: "fastapi.Request"):  # noqa: F821  (fastapi is in the image)
    import hmac
    import os

    from fastapi.responses import JSONResponse

    expected = f"Bearer {os.environ['COGLOSSA_KICK_TOKEN']}".encode()
    if not hmac.compare_digest(request.headers.get("authorization", "").encode(), expected):
        return JSONResponse({"detail": "wrong kick token"}, status_code=401)
    worker.spawn()
    return {"spawned": True}


@app.function(image=app_image, secrets=[secret], cpu=0.25, schedule=modal.Period(minutes=5))
def cron():
    """Starts a worker when a job is waiting: one queued as a worker decided to exit, a failed
    kick, or a retry whose backoff has passed."""
    import asyncio

    from coglossa.store.db import database_url
    from coglossa.worker.app import has_due_jobs

    if asyncio.run(has_due_jobs(database_url(_config()))):
        worker.spawn()


@app.function(image=app_image, secrets=[secret], timeout=900)
def migrate():
    render_config(CONFIG, RENDERED)
    subprocess.run(["coglossa", "db", "upgrade"], check=True)


@app.function(image=app_image, secrets=[secret], timeout=900)
def doctor():
    render_config(CONFIG, RENDERED)
    subprocess.run(["coglossa", "doctor"], check=False)


scorer_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("uv==0.13.0")
    .add_local_dir(REPO / "scorer", "/src/scorer", copy=True, ignore=IGNORE)
    .run_commands(
        # mirrors deploy/docker/scorer.Dockerfile with TORCH_INDEX=cu124: the locked torch
        # version from download.pytorch.org, everything else from the lock via PyPI
        "<the scorer.Dockerfile's version-extraction and constraints commands, adapted to"
        " install into the system Python with --system>",
    )
    .env({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
)


@app.function(
    image=scorer_image,
    gpu="L4",
    memory=16384,
    secrets=[secret],
    volumes={"/models": weights},
    scaledown_window=300,
    timeout=3600,
)
@modal.concurrent(max_inputs=8)
@modal.asgi_app(label="coglossa-scorer")
def scorer():
    from coglossa_scorer.cli import build_app

    return build_app([
        "--qe-model", "/models/metricx-24-hybrid-large-v2p6",
        "--qe-tokenizer", "/models/mt5-xl",
        "--embed-model", "/models/labse",
        "--device", "cuda",
    ])
```

The one deliberately open string in this plan is the scorer image's `run_commands`. Write it by translating lines 16–25 of `deploy/docker/scorer.Dockerfile`: same lock, same torch extraction, `TORCH_INDEX=cu124`, and `uv pip install --system` instead of the venv. That Dockerfile is the source of truth; copy its logic, do not invent new logic.

`upload_weights.py`: `modal run deploy/hosted/modal/upload_weights.py --source ~/coglossa-weights`. It uploads `metricx-24-hybrid-large-v2p6`, `mt5-xl`, `labse` and `weights.json` into the Volume `coglossa-weights` with `weights.batch_upload()`, skips names already present unless `--force` is given, and prints each name with its size.

- [ ] **Step 4: Check that `app.py` imports**

Run: `cd deploy/hosted/modal && uv run --with-requirements requirements.txt python -c "import app; print(sorted(app.app.registered_functions))"`
Expected: `['api', 'cron', 'doctor', 'kick', 'migrate', 'scorer', 'worker']`. Importing needs no Modal account.

Turn this into `deploy/hosted/modal/check_app.py`, a script that asserts the list and the labels and exits non-zero otherwise, so CI runs it.

- [ ] **Step 5: Write `deploy/hosted/README.md`**

The README covers:
- what runs where (the spec §2 table)
- the one-time setup, in order:
  1. Supabase: create the project and the `coglossa-files` bucket (private), generate the S3 keys, and copy the session-pooler URL
  2. Modal: `modal token new`, upload the weights, and create the Secret `coglossa-hosted` with every variable in spec §7 plus `COGLOSSA_SCORER_URL`, `COGLOSSA_KICK_URL` and `COGLOSSA_S3_ENDPOINT`
  3. the first `modal deploy`, then copy the printed URLs into the Secret and redeploy
  4. `modal run …::migrate`
  5. the Vercel project from `deploy/hosted/vercel`, with env vars from spec §7
  6. adding users with `add-user.mjs`
  7. the GitHub secrets for the deploy workflow
- how to generate a 32-byte secret (`openssl rand -base64 32`)
- costs and the spending limits from spec §8
- `modal run deploy/hosted/modal/app.py::doctor`
- a pointer that the air-gapped install is in `docs/operator-guide.md` and is unaffected.

Use plain, imperative steps with exact commands.

- [ ] **Step 6: Add the CI job**

`hosted-modal` in `ci.yml`:
- check out the repo, `setup-uv` with Python 3.12
- `cd engine && uv sync --all-groups`
- run the settings tests with the engine environment: `cd engine && uv run --with pyyaml pytest ../deploy/hosted/modal/test_settings.py -q -p no:cacheprovider --no-cov --rootdir ../deploy/hosted/modal`
- then `uv run --with-requirements deploy/hosted/modal/requirements.txt python deploy/hosted/modal/check_app.py`.

If running the test file from the engine project trips its `addopts` coverage or `filterwarnings`, run it with `-o addopts=""` and say so in a comment.

- [ ] **Step 7: Commit**

```bash
git add deploy/hosted .github/workflows/ci.yml
git commit -m "hosted: Modal app (api, burst worker, kick, cron, L4 scorer) and hosted config"
```

---

### Task 7: Deploy workflow and smoke test

**Files:**
- Create: `.github/workflows/hosted.yml`
- Create: `deploy/hosted/smoke.py`
- Create: `deploy/hosted/test_smoke.py`
- Modify: `deploy/hosted/README.md` (the GitHub secrets section)

**Interfaces:**
- Consumes:
  - `deploy/hosted/vercel/build.sh` (Task 5)
  - `deploy/hosted/modal/app.py` with its `migrate` function (Task 6)
- Produces:
  - `smoke.py --site URL --api-origin URL --user NAME`, with the password read from `COGLOSSA_SMOKE_PASSWORD`. It exits 0 when all checks pass and prints one line per check.

- [ ] **Step 1: Write the failing smoke tests**

`smoke.py` uses `httpx` (sync). Its checks:
1. `POST {site}/login` with the form (follow no redirects) returns 303 to `/` and sets `cg_session`.
2. `GET {site}/api/me` with that cookie returns 200 and `user == NAME`.
3. `GET {api_origin}/api/me` with no secret returns 401.
4. `GET {api_origin}/api/health` returns 200.

`test_smoke.py` drives `run_checks(client_factory)` against an `httpx.MockTransport` that plays the site and the API, and asserts:
- all pass → exit 0, with four `ok` lines
- `/api/me` direct returning 200 → exit 1, naming check 3 ("the API answers without the proxy secret")
- login returning 303 to `/login?failed=1` → exit 1, naming check 1.

- [ ] **Step 2: Implement `smoke.py`**

Write it so that `run_checks(site, api_origin, user, password, transport=None) -> list[tuple[str, bool, str]]` and `main(argv) -> int` are testable. Run the tests: `uv run --with httpx --with pytest pytest deploy/hosted/test_smoke.py -q`. Expected: PASS.

- [ ] **Step 3: Write `hosted.yml`**

```yaml
name: hosted
on:
  workflow_run:
    workflows: [ci]
    types: [completed]
    branches: [main]
  workflow_dispatch:
concurrency: { group: hosted-deploy, cancel-in-progress: false }
jobs:
  deploy:
    if: github.event_name == 'workflow_dispatch' || github.event.workflow_run.conclusion == 'success'
    runs-on: ubuntu-latest
    env:
      MODAL_TOKEN_ID: ${{ secrets.MODAL_TOKEN_ID }}
      MODAL_TOKEN_SECRET: ${{ secrets.MODAL_TOKEN_SECRET }}
      VERCEL_TOKEN: ${{ secrets.VERCEL_TOKEN }}
      VERCEL_ORG_ID: ${{ secrets.VERCEL_ORG_ID }}
      VERCEL_PROJECT_ID: ${{ secrets.VERCEL_PROJECT_ID }}
      COGLOSSA_SMOKE_PASSWORD: ${{ secrets.COGLOSSA_SMOKE_PASSWORD }}
      SITE_URL: ${{ vars.COGLOSSA_SITE_URL }}
      API_ORIGIN: ${{ vars.COGLOSSA_API_ORIGIN }}
    steps:
      - name: skip without deploy secrets
        id: gate
        run: |
          if [ -z "$MODAL_TOKEN_ID" ] || [ -z "$VERCEL_TOKEN" ]; then
            echo "::notice::hosted deploy skipped: MODAL_TOKEN_ID or VERCEL_TOKEN is not set"
            echo "run=false" >> "$GITHUB_OUTPUT"
          else
            echo "run=true" >> "$GITHUB_OUTPUT"
          fi
      - if: steps.gate.outputs.run == 'true'
        uses: actions/checkout@v4
        with: { ref: "${{ github.event.workflow_run.head_sha || github.sha }}" }
      - if: steps.gate.outputs.run == 'true'
        uses: astral-sh/setup-uv@v6
        with: { python-version: "3.12" }
      - if: steps.gate.outputs.run == 'true'
        name: deploy the backend
        run: |
          uv run --with-requirements deploy/hosted/modal/requirements.txt modal deploy deploy/hosted/modal/app.py
          uv run --with-requirements deploy/hosted/modal/requirements.txt modal run deploy/hosted/modal/app.py::migrate
      - if: steps.gate.outputs.run == 'true'
        uses: pnpm/action-setup@v4
      - if: steps.gate.outputs.run == 'true'
        uses: actions/setup-node@v4
        with: { node-version: "22" }
      - if: steps.gate.outputs.run == 'true'
        name: deploy the front door
        run: |
          deploy/hosted/vercel/build.sh
          cd deploy/hosted/vercel && npx --yes vercel@<pin> deploy --prod --yes --token "$VERCEL_TOKEN"
      - if: steps.gate.outputs.run == 'true' && env.COGLOSSA_SMOKE_PASSWORD != ''
        name: smoke test
        run: uv run --with httpx python deploy/hosted/smoke.py --site "$SITE_URL" --api-origin "$API_ORIGIN" --user ci
```

Match how `ci.yml` sets up pnpm (`pnpm/action-setup` with the version from `packageManager`). Pin `vercel@` to the newest CLI release that is at least two weeks old.

- [ ] **Step 4: Document the secrets and commit**

In the README, list the GitHub secrets (`MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `COGLOSSA_SMOKE_PASSWORD`) and repository variables (`COGLOSSA_SITE_URL`, `COGLOSSA_API_ORIGIN`), and the `ci` user that the smoke test signs in as.

```bash
git add .github/workflows/hosted.yml deploy/hosted
git commit -m "hosted: deploy workflow (Modal, migrations, Vercel) with a smoke test"
```

---

### Task 8: Provisioning, spikes and first deploy (controller-run, with the user)

The controller runs this task, not a subagent. It needs the user's accounts.

- [ ] **S1 (Vercel proxying).** Deploy `deploy/hosted/vercel` to a throwaway Vercel project with `COGLOSSA_API_ORIGIN=https://httpbin.org`. Fake users and secrets are fine.
  - `/api/anything` must echo `X-Remote-User` and `X-Coglossa-Proxy-Secret` set by the middleware.
  - A 20 MB multipart POST to `/api/anything` must arrive whole.
  - An SSE endpoint must stream through. Rewrite `/api/sse` to a public test stream, or test this after the Modal API is up with `/api/runs/{id}/events`.

  If headers fail, implement the streaming-proxy fallback in `forward()`. If bodies are capped, implement the direct-upload fallback (spec §9). Record each outcome in the ledger, then delete the throwaway project.
- [ ] **S2 (Supabase pooler).** With the user's `coglossa-preview` project:
  - create the private bucket `coglossa-files` and the S3 keys
  - run `coglossa db upgrade` against the session-pooler URL from this machine
  - defer a job, then run `coglossa worker --burst` and check that it drains.
- [ ] **S3 (GPU scorer).** After `modal deploy`, time a cold `/health` request followed by the first `/v1/qe`. The target is under 120 s. If it is slower, enable memory snapshots on `scorer` and re-time.
- [ ] **Secrets.** Create the Modal Secret, the Vercel env vars and the GitHub secrets and variables. The new OpenRouter key has a $25 limit. Set the Modal budget to $30. Add the users the user names, plus `ci`.
- [ ] **End to end.** Through the public URL, sign in, upload a short document, approve the guide, and wait for the run to complete with qe and xsim present. Export a DOCX. Record the timings and costs in the PR.
- [ ] **Merge.** Open a PR and merge it when every CI check is green (standing rule). The `hosted` workflow then deploys `main`.

## Self-review notes

- **Spec coverage.** Spec sections map to tasks as follows:
  - §4.1 → Task 1
  - §4.2 → Task 2
  - §4.3 → Task 3
  - §4.4 → Task 4
  - §5 → Task 5
  - §6 → Task 6
  - §7 → Tasks 6–8
  - §8 deploy, doctor, limits → Tasks 6–8
  - §9 → Task 8
  - §10 → the tests in each task, plus the smoke test in Task 7
  - §3 "cron catches retries" → `has_due_jobs` in Task 3 and `cron` in Task 6.
- **Deviation from the spec, ruled here:** the Sign-out link shows when `/api/me` returns a `logout_url` (`auth.logout_url`), not whenever a user is signed in. This keeps the link from pointing to a missing page on air-gapped `trusted_header` installs, whose proxies have their own sign-out.
- **Deviation from the spec, ruled here:** URLs specific to the account (scorer, kick, S3 endpoint) are filled in from the Modal Secret at container start (`settings.render_config`), not committed in the config file.
- **Deviation from the spec, ruled here:** the cron starts a worker only when `has_due_jobs` says a job is waiting. This avoids starting a 2-CPU worker every 5 minutes.
