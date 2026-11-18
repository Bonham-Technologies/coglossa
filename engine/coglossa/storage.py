"""Uploaded files (spec §2.1): a Storage interface with local-volume and S3 implementations.
Keys are relative '/'-separated paths; nothing outside the storage root is reachable."""

import contextlib
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from coglossa.config import S3StorageConfig, StorageConfig


class StorageError(Exception):
    """A storage operation failed; the message is safe to show to an operator."""


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def local_file(self, key: str) -> AbstractContextManager[Path]: ...

    def delete_prefix(self, prefix: str) -> None: ...

    def check_writable(self) -> None: ...


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def safe_filename(name: str) -> str:
    """A storage-safe basename that keeps the extension (ingestors pick a format by it)."""
    base = PurePosixPath(name.replace("\\", "/")).name
    cleaned = _UNSAFE.sub("_", base).strip(".")
    return cleaned or "document"


# Group-readable, so an API and a worker that run as different users in one group can share
# the volume; the storage root's own permissions keep everyone else out.
FILE_MODE = 0o640


_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def display_name(name: str) -> str:
    """The name to show for an uploaded file: no directories, no NUL, each run of other control
    characters (CR, LF, TAB...) a single space, so it fits a one-line message or a header."""
    base = PurePosixPath(name.replace("\x00", "").replace("\\", "/")).name
    return _CONTROL.sub(" ", base).strip()


def check_key(key: str) -> list[str]:
    """The key's parts; raises StorageError for a key that could escape the storage root."""
    parts = key.split("/")
    if not key or key.startswith("/") or "\\" in key or any(p in ("", ".", "..") for p in parts):
        raise StorageError(f"invalid storage key: {key!r}")
    return parts


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root.joinpath(*check_key(key))

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        tmp: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            try:
                os.fchmod(fd, FILE_MODE)
                f = os.fdopen(fd, "wb")
            except BaseException:
                os.close(fd)
                raise
            with f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())  # the data is on disk before the name points to it
            os.replace(tmp, path)
        except OSError as e:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
            raise StorageError(f"cannot store {key}: {e.strerror or e}") from e

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as e:
            raise StorageError(f"stored file not found: {key}") from e
        except OSError as e:
            raise StorageError(f"cannot read {key}: {e.strerror or e}") from e

    @contextlib.contextmanager
    def local_file(self, key: str) -> Iterator[Path]:
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"stored file not found: {key}")
        yield path

    def delete_prefix(self, prefix: str) -> None:
        target = self._path(prefix.rstrip("/"))
        try:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        except OSError as e:
            raise StorageError(f"cannot delete {prefix}: {e.strerror or e}") from e

    def check_writable(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".coglossa-check-"):
                pass
        except OSError as e:
            raise StorageError(
                f"storage path {self.root} is not writable: {e.strerror or e}"
            ) from e


class S3Storage:
    """Files in an S3-compatible bucket. boto3 comes with the `s3` extra and is imported here,
    so a local-storage install neither needs nor loads it. Supabase Storage needs path-style
    addressing."""

    def __init__(self, cfg: S3StorageConfig, *, client: Any | None = None) -> None:
        self.cfg = cfg
        if client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as e:
                raise StorageError("S3 storage needs the s3 extra: install coglossa[s3]") from e

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
                config=Config(
                    s3={"addressing_style": "path"},
                    retries={"mode": "standard"},
                    # boto3 1.36+ adds checksums to every upload by default, which some
                    # S3-compatible stores refuse; send and check them only where S3 requires
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                ),
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
            # One DeleteObject per key, not DeleteObjects: Supabase's S3 API refuses that with
            # a bare 400 whatever its parameters, and a project holds a handful of files.
            for key in keys:
                self._s3.delete_object(Bucket=self.cfg.bucket, Key=key)
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
