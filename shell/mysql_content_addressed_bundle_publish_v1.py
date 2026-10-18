#!/usr/bin/env python3
"""Transactionally publish one sealed content-addressed support bundle.

The source and destination are local paths.  For a remote host, transfer the
source into an attempt-owned incoming directory first, then run this program
on that host.  No existing destination is overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
from typing import Any


HEX64 = re.compile(r"[0-9a-f]{64}$")


class PublishError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(root: Path, expected_id: str) -> dict[str, Any]:
    manifest_path = root / "bundle_manifest.json"
    sidecar = root / "bundle_manifest.json.sha256"
    if root.is_symlink() or not root.is_dir() or manifest_path.is_symlink() or not manifest_path.is_file():
        raise PublishError("bundle root/manifest differs")
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = {key: item for key, item in value.items() if key != "bundle_id"}
    payload_id = hashlib.sha256(canonical(unsigned)).hexdigest()
    files = value.get("files")
    if (
        value.get("bundle_id") != expected_id
        or root.name != expected_id
        or payload_id != expected_id
        or not isinstance(files, dict)
        or sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii")
        != "%s  bundle_manifest.json\n" % sha256(manifest_path)
    ):
        raise PublishError("bundle manifest identity differs")
    expected_paths = set(files) | {"bundle_manifest.json", "bundle_manifest.json.sha256"}
    actual_paths = set()
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise PublishError("symlink prohibited in bundle")
        if candidate.is_file():
            actual_paths.add(candidate.relative_to(root).as_posix())
    if actual_paths != expected_paths:
        raise PublishError("bundle exact file set differs")
    for relative, expected in files.items():
        candidate = root / relative
        if HEX64.fullmatch(str(expected)) is None or sha256(candidate) != expected:
            raise PublishError("bundle file closure differs: " + relative)
    return value


def fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def seal(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o444 if path.is_file() else 0o555)
    os.chmod(root, 0o555)


def publish(source: Path, destination_root: Path, expected_id: str) -> dict[str, Any]:
    if HEX64.fullmatch(expected_id) is None:
        raise PublishError("expected bundle ID differs")
    source = source.resolve(strict=True)
    verify(source, expected_id)
    destination_root = destination_root.resolve(strict=True)
    if destination_root.is_symlink() or not destination_root.is_dir():
        raise PublishError("destination root differs")
    destination = destination_root / expected_id
    if destination.exists() or destination.is_symlink():
        verify(destination, expected_id)
        return {"status": "PASS_ALREADY_PUBLISHED_IDENTICAL", "path": str(destination), "bundle_id": expected_id}
    stage = destination_root / (".incoming-%s-%s" % (expected_id, secrets.token_hex(12)))
    stage.mkdir(mode=0o700)
    try:
        manifest = json.loads((source / "bundle_manifest.json").read_text(encoding="utf-8"))
        for relative in sorted(set(manifest["files"]) | {"bundle_manifest.json", "bundle_manifest.json.sha256"}):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
        # The temporary root name is intentionally not content-addressed, so
        # verify its bytes through a same-name view before the atomic rename.
        view = stage.parent / expected_id
        if view.exists() or view.is_symlink():
            raise PublishError("publication destination appeared concurrently")
        fsync_tree(stage)
        os.rename(stage, view)
        try:
            verify(view, expected_id)
            seal(view)
        except Exception:
            # The content address is published only after all byte checks.  A
            # post-rename seal failure remains fail-closed and must be audited;
            # never overwrite or silently remove it.
            raise
        descriptor = os.open(destination_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return {"status": "PASS_ATOMICALLY_PUBLISHED", "path": str(view), "bundle_id": expected_id}
    except Exception:
        if stage.exists() and stage.name.startswith(".incoming-" + expected_id + "-"):
            shutil.rmtree(stage)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--destination-root", type=Path, required=True)
    parser.add_argument("--expected-bundle-id", required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.source_bundle, args.destination_root, args.expected_bundle_id), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, PublishError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
