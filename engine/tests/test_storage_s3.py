import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from coglossa.config import S3StorageConfig
from coglossa.storage import S3Storage, StorageError, build_storage, describe_storage

BUCKET = "coglossa-files"


@pytest.fixture
def s3(monkeypatch, tmp_path):
    # botocore reads the machine's ~/.aws/config, whose s3 settings would leak into the client
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
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


class _StubPaginator:
    def paginate(self, **_):
        return [{"Contents": [{"Key": "p/1/a.txt"}, {"Key": "p/1/b/c.txt"}]}]


class _SupabaseLikeClient:
    """Supabase's S3 API refuses DeleteObjects (400, empty body) whatever its parameters, and
    deletes single objects fine (checked against the preview's bucket)."""

    def __init__(self, deny: str | None = None):
        self.deleted: list[str] = []
        self.deny = deny

    def get_paginator(self, _):
        return _StubPaginator()

    def delete_objects(self, **_):
        raise ClientError(
            {"Error": {}, "ResponseMetadata": {"HTTPStatusCode": 400}}, "DeleteObjects"
        )

    def delete_object(self, *, Bucket, Key):
        if Key == self.deny:
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "DeleteObject"
            )
        self.deleted.append(Key)


def test_delete_prefix_works_where_deleteobjects_is_refused():
    client = _SupabaseLikeClient()
    S3Storage(_cfg(), client=client).delete_prefix("p/1/")
    assert client.deleted == ["p/1", "p/1/a.txt", "p/1/b/c.txt"]


def test_delete_prefix_reports_a_refused_delete():
    store = S3Storage(_cfg(), client=_SupabaseLikeClient(deny="p/1/a.txt"))
    with pytest.raises(StorageError, match="cannot delete p/1/: .*Access Denied"):
        store.delete_prefix("p/1/")


def test_without_the_s3_extra_s3_storage_says_what_to_install(s3, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_boto3(name, *args, **kwargs):
        if name == "boto3" or name.startswith("botocore"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_boto3)
    with pytest.raises(
        StorageError, match=r"S3 storage needs the s3 extra: install coglossa\[s3\]"
    ):
        S3Storage(_cfg())


def test_checksums_are_only_sent_and_checked_where_s3_requires_them(s3):
    config = S3Storage(_cfg())._s3.meta.config
    assert config.request_checksum_calculation == "when_required"
    assert config.response_checksum_validation == "when_required"
    assert config.s3 == {"addressing_style": "path"}
