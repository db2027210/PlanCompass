from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "shell/mysql_job_physical_copy_v1.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "job_physical_copy", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COPY = load_module()


@pytest.fixture(autouse=True)
def local_target_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production helper pins 999:999; tests cannot chown to it."""
    monkeypatch.setattr(COPY, "MYSQL_DATADIR_UID", os.getuid())
    monkeypatch.setattr(COPY, "MYSQL_DATADIR_GID", os.getgid())


def test_userspace_copy_preserves_content_without_clone_api(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    (source / "nested").mkdir(parents=True)
    (source / "nested/data").write_bytes(b"x" * (1024 * 1024 + 17))
    (source / "empty").write_bytes(b"")
    regular, count, byte_count, normalization, target_permissions = COPY.copy_tree(
        source, destination
    )
    assert count == 2
    assert byte_count == 1024 * 1024 + 17
    assert (destination / "nested/data").read_bytes() == (
        source / "nested/data"
    ).read_bytes()
    assert {
        row["relative"] for row in regular
    } == {"nested/data", "empty"}
    source_text = MODULE_PATH.read_text(encoding="utf-8")
    assert "copy_file_range(" not in source_text
    assert "sendfile(" not in source_text
    assert "fcntl.ioctl" in source_text
    assert "shutil.copy" not in source_text
    assert normalization["excluded_root_logical_socket"] == {
        "path": "mysql.sock",
        "target": "/var/run/mysqld/mysqld.sock",
    }
    assert normalization["path_type_size_sha256_content_identity"] is True
    assert normalization["ownership_and_mode_excluded_from_content_identity"] is True
    assert target_permissions["target_only"] is True
    assert target_permissions["directory_mode"] == "0700"
    assert target_permissions["regular_file_mode"] == "0600"
    assert target_permissions["source_metadata_sha256_before"] == target_permissions["source_metadata_sha256_after"]
    assert (destination / "nested").stat().st_mode & 0o777 == 0o700
    assert (destination / "nested/data").stat().st_mode & 0o777 == 0o600


def test_copy_skips_only_exact_root_logical_mysql_socket(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "mysql.sock").symlink_to("/var/run/mysqld/mysqld.sock")
    (source / "data").write_bytes(b"data")
    regular, count, byte_count, normalization, target_permissions = COPY.copy_tree(
        source, destination
    )
    assert [row["relative"] for row in regular] == ["data"]
    assert count == 1 and byte_count == 4
    assert not (destination / "mysql.sock").exists()
    assert normalization["other_symlinks_rejected"] is True
    assert target_permissions["socket_excluded_from_target"] is True


@pytest.mark.parametrize(
    ("relative", "target"),
    [
        ("mysql.sock", "/tmp/not-the-mysql-runtime-socket"),
        ("nested/mysql.sock", "/var/run/mysqld/mysqld.sock"),
        ("mysqld.sock", "/var/run/mysqld/mysqld.sock"),
        ("ordinary-link", "data"),
    ],
)
def test_copy_rejects_any_other_transient_or_symlink(
    tmp_path: Path, relative: str, target: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    link = source / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    if relative == "ordinary-link":
        (source / "data").write_bytes(b"data")
    link.symlink_to(target)
    with pytest.raises(COPY.CopyError, match="transient|symlink"):
        COPY.copy_tree(source, tmp_path / "destination")


def test_nonsharing_audit_rejects_shared_or_overlapping_extents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "relative": "data",
        "source": Path("/source/data"),
        "destination": Path("/destination/data"),
        "size": 4096,
    }

    def shared(path: Path):
        return [{
            "logical": 0,
            "physical": 4096 if "destination" in str(path) else 8192,
            "length": 4096,
            "flags": (
                COPY.FIEMAP_EXTENT_SHARED
                if "destination" in str(path)
                else 0
            ),
        }]

    monkeypatch.setattr(COPY, "fiemap", shared)
    with pytest.raises(COPY.CopyError, match="shared extent"):
        COPY.audit_nonsharing([row])

    def overlap(path: Path):
        return [{
            "logical": 0,
            "physical": 4096,
            "length": 4096,
            "flags": 0,
        }]

    monkeypatch.setattr(COPY, "fiemap", overlap)
    with pytest.raises(COPY.CopyError, match="overlap"):
        COPY.audit_nonsharing([row])


def test_nonsharing_audit_accepts_disjoint_extents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "relative": "data",
        "source": Path("/source/data"),
        "destination": Path("/destination/data"),
        "size": 4096,
    }

    def disjoint(path: Path):
        return [{
            "logical": 0,
            "physical": 8192 if "destination" in str(path) else 4096,
            "length": 4096,
            "flags": 0,
        }]

    monkeypatch.setattr(COPY, "fiemap", disjoint)
    result = COPY.audit_nonsharing([row])
    assert result["fiemap_extent_shared_absent"] is True
    assert (
        result["source_destination_physical_extent_overlap_absent"] is True
    )
