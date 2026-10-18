"""Durable finite-JSON and checksum helpers for MySQL random-walk Runs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Mapping


CHECKSUM_FILE = "checksums.sha256"
RESULT_MANIFEST_FILE = "result_manifest.json"
CHECKSUM_EXCLUSIONS = {CHECKSUM_FILE, RESULT_MANIFEST_FILE}


class ArtifactError(RuntimeError):
    """A formal artifact is unsafe, invalid, or incomplete."""


def _directory(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise ArtifactError(f"directory is absent: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ArtifactError(f"path must be a non-symlink directory: {path}")


def _regular(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise ArtifactError(f"file is absent: {path}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ArtifactError(f"path must be a regular non-symlink file: {path}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sha256_file(path: Path) -> str:
    _regular(path)
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    try:
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            digest.update(block)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, payload: bytes, *, replace: bool = True) -> None:
    _directory(path.parent)
    if path.exists() or path.is_symlink():
        if not replace:
            raise ArtifactError(f"refusing to overwrite formal artifact: {path}")
        _regular(path)
    temporary = path.parent / f".{path.name}.tmp.{os.getpid()}.{secrets.token_hex(12)}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o644,
    )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ArtifactError(f"short write for {path}")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def atomic_write_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    replace: bool = True,
) -> None:
    if not isinstance(payload, Mapping):
        raise ArtifactError("formal JSON payload must be an object")
    try:
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ArtifactError(f"formal JSON is not finite/serializable: {exc}") from exc
    atomic_write_bytes(path, encoded, replace=replace)


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    _directory(path.parent)
    if path.exists() or path.is_symlink():
        _regular(path)
    try:
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ArtifactError(f"formal JSONL row is not finite/serializable: {exc}") from exc
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o644)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ArtifactError(f"short JSONL append for {path}")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _relative(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ArtifactError(f"artifact escapes Run root: {path}") from exc
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ArtifactError(f"unsafe artifact path: {relative}")
    return relative.as_posix()


def artifact_files(root: Path) -> tuple[Path, ...]:
    _directory(root)
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = _relative(root, path)
        if any(part.startswith(".") and ".tmp." in part for part in Path(relative).parts):
            raise ArtifactError(f"temporary file remains at finalization: {relative}")
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ArtifactError(f"symlink is forbidden in Run artifacts: {relative}")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ArtifactError(f"non-regular Run artifact is forbidden: {relative}")
        if relative not in CHECKSUM_EXCLUSIONS:
            files.append(path)
    return tuple(sorted(files, key=lambda item: _relative(root, item)))


def fsync_artifact_tree(root: Path) -> dict[str, int]:
    """Flush every regular artifact and directory before checksumming."""

    files = artifact_files(root)
    for path in files:
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    directories = [root, *(path for path in root.rglob("*") if path.is_dir())]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _directory(directory)
        _fsync_directory(directory)
    return {"file_count": len(files), "directory_count": len(directories)}


def write_checksum_manifest(root: Path) -> dict[str, Any]:
    entries = [(sha256_file(path), _relative(root, path)) for path in artifact_files(root)]
    payload = "".join(f"{digest}  {relative}\n" for digest, relative in entries).encode("utf-8")
    manifest = root / CHECKSUM_FILE
    atomic_write_bytes(manifest, payload)
    validation = validate_checksum_manifest(root)
    return {
        "relative_path": CHECKSUM_FILE,
        "sha256": sha256_file(manifest),
        "entry_count": len(entries),
        "validation": validation,
    }


def validate_checksum_manifest(root: Path) -> dict[str, Any]:
    manifest = root / CHECKSUM_FILE
    _regular(manifest)
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        pieces = line.split("  ", 1)
        if len(pieces) != 2 or len(pieces[0]) != 64:
            raise ArtifactError(f"invalid checksum line {line_number}")
        digest, relative_text = pieces
        try:
            int(digest, 16)
        except ValueError as exc:
            raise ArtifactError(f"invalid digest on checksum line {line_number}") from exc
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ArtifactError(f"unsafe checksum path on line {line_number}")
        normalized = relative.as_posix()
        if normalized in seen or normalized in CHECKSUM_EXCLUSIONS:
            raise ArtifactError(f"duplicate or excluded checksum path: {normalized}")
        target = root / relative
        _regular(target)
        if sha256_file(target) != digest:
            raise ArtifactError(f"checksum mismatch: {normalized}")
        seen.add(normalized)
        entries.append((digest, normalized))
    expected = {_relative(root, path) for path in artifact_files(root)}
    if seen != expected:
        raise ArtifactError(
            f"checksum file set differs: missing={sorted(expected-seen)} extra={sorted(seen-expected)}"
        )
    if [relative for _, relative in entries] != sorted(seen):
        raise ArtifactError("checksum entries are not lexicographically ordered")
    return {"status": "passed", "entry_count": len(entries)}
