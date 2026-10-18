#!/usr/bin/env python3
"""Verify a staged immutable bundle and atomically publish it without overwrite."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys


HEX64 = re.compile(r"^[0-9a-f]{64}$")


class PublishError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(root, expected_id, require_directory_name=False):
    if (
        root.is_symlink()
        or not root.is_dir()
        or (require_directory_name and root.name != expected_id)
    ):
        raise PublishError("staged bundle directory identity differs")
    manifest_path = root / "bundle_manifest.json"
    sidecar = root / "bundle_manifest.json.sha256"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or sidecar.is_symlink()
        or not sidecar.is_file()
    ):
        raise PublishError("bundle manifest/sidecar is absent or symlinked")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PublishError("bundle manifest JSON differs: %s" % exc)
    if not isinstance(manifest, dict) or manifest.get("bundle_id") != expected_id:
        raise PublishError("bundle manifest ID differs")
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    if hashlib.sha256(canonical(identity)).hexdigest() != expected_id:
        raise PublishError("bundle content address differs")
    manifest_hash = sha256(manifest_path)
    if sidecar.read_text(encoding="ascii") != (
        manifest_hash + "  bundle_manifest.json\n"
    ):
        raise PublishError("bundle manifest sidecar differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise PublishError("bundle file closure is absent")
    declared = set(files) | {"bundle_manifest.json", "bundle_manifest.json.sha256"}
    actual = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    symlinks = [str(path.relative_to(root)) for path in root.rglob("*") if path.is_symlink()]
    if symlinks or actual != declared:
        raise PublishError("bundle exact file closure differs")
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, str) or HEX64.fullmatch(expected) is None:
            raise PublishError("bundle file declaration differs")
        path = root / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise PublishError("bundle file hash differs: %s" % relative)
    return manifest_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--expected-bundle-id", required=True)
    args = parser.parse_args()
    if HEX64.fullmatch(args.expected_bundle_id) is None:
        raise PublishError("expected bundle ID differs")
    stage = args.stage.resolve(strict=True)
    target_root = args.target_root.resolve()
    manifest_hash = verify(stage, args.expected_bundle_id)
    target_root.mkdir(parents=True, exist_ok=True, mode=0o755)
    target = target_root / args.expected_bundle_id
    if target.exists():
        if target.is_symlink() or not target.is_dir():
            raise PublishError("existing target is not an immutable directory")
        existing_hash = verify(target, args.expected_bundle_id, True)
        if existing_hash != manifest_hash:
            raise PublishError("existing target differs")
        status = "ALREADY_PUBLISHED_IDENTICAL"
    else:
        if stage.parent.stat().st_dev != target_root.stat().st_dev:
            raise PublishError("stage and target root must share a filesystem for atomic publish")
        os.rename(str(stage), str(target))
        if verify(target, args.expected_bundle_id, True) != manifest_hash:
            raise PublishError("post-publish verification differs")
        status = "PUBLISHED_ATOMIC_NO_OVERWRITE"
    print(
        json.dumps(
            {
                "status": status,
                "bundle": str(target),
                "bundle_id": args.expected_bundle_id,
                "manifest_sha256": manifest_hash,
                "overwrite_performed": False,
                "deletion_performed": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (PublishError, OSError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
