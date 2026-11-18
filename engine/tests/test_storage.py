import pytest

from coglossa.config import StorageConfig
from coglossa.storage import LocalStorage, StorageError, build_storage, safe_filename


def test_put_get_and_local_file(tmp_path):
    store = LocalStorage(tmp_path / "files")
    store.put("projects/p1/documents/d1/story.txt", b"hello")
    assert store.get("projects/p1/documents/d1/story.txt") == b"hello"
    with store.local_file("projects/p1/documents/d1/story.txt") as path:
        assert path.read_bytes() == b"hello"
        assert path.suffix == ".txt"
    assert not list((tmp_path / "files").rglob("*.tmp"))


def test_put_overwrites_atomically(tmp_path):
    store = LocalStorage(tmp_path)
    store.put("a/b.txt", b"one")
    store.put("a/b.txt", b"two")
    assert store.get("a/b.txt") == b"two"


@pytest.mark.parametrize("key", ["", "/etc/passwd", "../x", "a/../../x", "a\\b", "a//b", "a/./b"])
def test_unsafe_keys_are_rejected(tmp_path, key):
    with pytest.raises(StorageError):
        LocalStorage(tmp_path).put(key, b"x")


def test_missing_key(tmp_path):
    store = LocalStorage(tmp_path)
    with pytest.raises(StorageError, match="not found"):
        store.get("nope.txt")
    with pytest.raises(StorageError, match="not found"), store.local_file("nope.txt"):
        pass


def test_delete_prefix(tmp_path):
    store = LocalStorage(tmp_path)
    store.put("projects/p1/a.txt", b"1")
    store.put("projects/p1/sub/b.txt", b"2")
    store.put("projects/p2/c.txt", b"3")
    store.delete_prefix("projects/p1/")
    assert not (tmp_path / "projects" / "p1").exists()
    assert store.get("projects/p2/c.txt") == b"3"
    store.delete_prefix("projects/none/")  # missing prefix is fine


def test_check_writable(tmp_path):
    LocalStorage(tmp_path / "new").check_writable()
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(StorageError, match="not writable"):
        LocalStorage(blocker / "sub").check_writable()


def test_write_failure_is_a_storage_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(StorageError, match="cannot store"):
        LocalStorage(blocker).put("a.txt", b"x")


def test_build_storage(tmp_path):
    store = build_storage(StorageConfig(path=tmp_path))
    assert isinstance(store, LocalStorage) and store.root == tmp_path


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("story.docx", "story.docx"),
        ("mon récit (v2).TXT", "mon_r_cit__v2_.TXT"),
        ("../../etc/passwd", "passwd"),
        ("...", "document"),
        ("", "document"),
    ],
)
def test_safe_filename(name, expected):
    assert safe_filename(name) == expected


def test_failed_write_leaves_no_temp_file(tmp_path, monkeypatch):
    store = LocalStorage(tmp_path)

    def fail_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("coglossa.storage.os.replace", fail_replace)
    with pytest.raises(StorageError, match="cannot store"):
        store.put("a/b.txt", b"x" * 100)
    assert not list(tmp_path.rglob("*.tmp"))
    assert not (tmp_path / "a" / "b.txt").exists()


def test_delete_prefix_removes_a_plain_file(tmp_path):
    store = LocalStorage(tmp_path)
    store.put("projects/p1/only.txt", b"1")
    store.delete_prefix("projects/p1/only.txt")
    assert not (tmp_path / "projects" / "p1" / "only.txt").exists()


def test_delete_prefix_failure_is_a_storage_error(tmp_path, monkeypatch):
    store = LocalStorage(tmp_path)
    store.put("projects/p1/a.txt", b"1")

    def fail_rmtree(path):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("coglossa.storage.shutil.rmtree", fail_rmtree)
    with pytest.raises(StorageError, match="cannot delete projects/p1/"):
        store.delete_prefix("projects/p1/")


def test_stored_files_are_group_readable_and_synced(tmp_path, monkeypatch):
    import os
    import stat

    from coglossa.storage import FILE_MODE, LocalStorage

    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    storage = LocalStorage(tmp_path)
    storage.put("a/b.txt", b"x")
    assert stat.S_IMODE((tmp_path / "a" / "b.txt").stat().st_mode) == FILE_MODE == 0o640
    assert len(synced) == 1


def test_put_closes_the_temp_file_when_chmod_fails(tmp_path, monkeypatch):
    import os

    import pytest

    from coglossa.storage import LocalStorage, StorageError

    closed = []
    real_close = os.close

    def fail(fd, mode):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "fchmod", fail)
    monkeypatch.setattr(os, "close", lambda fd: (closed.append(fd), real_close(fd))[1])
    with pytest.raises(StorageError, match="cannot store a.txt: Operation not permitted"):
        LocalStorage(tmp_path).put("a.txt", b"x")
    assert len(closed) == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("story.txt", "story.txt"),
        ("../../etc/passwd.txt", "passwd.txt"),
        ("C:\\Users\\me\\Mon livre.docx", "Mon livre.docx"),
        (" رواية.txt ", "رواية.txt"),
        ("a\x00b.md", "ab.md"),
        ("a\nb.txt", "a b.txt"),
        ("a\r\n\tb.txt", "a b.txt"),
        ("dir/", "dir"),
        ("", ""),
    ],
)
def test_display_name(name, shown):
    from coglossa.storage import display_name

    assert display_name(name) == shown
