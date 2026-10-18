#!/usr/bin/env python3
"""Verify one content-addressed bundle and publish it by same-FS rename."""
from __future__ import print_function

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys


class PublishError(RuntimeError):
    pass


def sha(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def verify(root, expected_id, expected_manifest_sha):
    root = Path(root)
    manifest_path = root / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (root.is_symlink() or not root.is_dir()
            or manifest.get("bundle_id") != expected_id
            or sha(manifest_path) != expected_manifest_sha
            or hashlib.sha256(canonical(unsigned)).hexdigest() != expected_id):
        raise PublishError("bundle content identity differs")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        raise PublishError("bundle closure is absent")
    observed = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PublishError("bundle closure contains symlink")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in {
                "bundle_manifest.json", "bundle_manifest.json.sha256"}:
            observed[relative] = sha(path)
        elif not path.is_file() and not path.is_dir():
            raise PublishError("bundle closure contains special entry")
    if observed != expected:
        raise PublishError("bundle file closure differs")
    sidecar = root / "bundle_manifest.json.sha256"
    if (not sidecar.is_file() or sidecar.is_symlink()
            or sidecar.read_text(encoding="ascii")
            != expected_manifest_sha + "  bundle_manifest.json\n"):
        raise PublishError("manifest sidecar differs")
    return manifest


def freeze(root):
    root = Path(root)
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(str(path), 0o555 if path.is_dir() else 0o444)
    os.chmod(str(root), 0o555)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingress", type=Path, required=True)
    parser.add_argument("--destination-parent", type=Path, required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    if (len(args.bundle_id) != 64 or len(args.manifest_sha256) != 64
            or any(c not in "0123456789abcdef"
                   for c in args.bundle_id + args.manifest_sha256)):
        raise PublishError("expected identity is malformed")
    verify(args.ingress, args.bundle_id, args.manifest_sha256)
    args.destination_parent.mkdir(parents=True, exist_ok=True)
    destination = args.destination_parent / args.bundle_id
    if destination.exists():
        verify(destination, args.bundle_id, args.manifest_sha256)
        print(json.dumps({"status":"EXISTING_IMMUTABLE_BUNDLE_REVERIFIED",
                          "destination":str(destination),
                          "bundle_id":args.bundle_id},sort_keys=True))
        return
    stage = args.destination_parent / (
        ".stage-" + args.bundle_id + "-" + secrets.token_hex(8)
    )
    if stage.exists():
        raise PublishError("fresh stage collision")
    try:
        shutil.copytree(str(args.ingress), str(stage), symlinks=True)
        freeze(stage)
        verify(stage, args.bundle_id, args.manifest_sha256)
        os.rename(str(stage), str(destination))
    finally:
        if stage.exists():
            for path in [stage, *stage.rglob("*")]:
                try:
                    os.chmod(str(path), 0o700 if path.is_dir() else 0o600)
                except OSError:
                    pass
            shutil.rmtree(str(stage))
    verify(destination, args.bundle_id, args.manifest_sha256)
    print(json.dumps({"status":"PASS_ATOMIC_CONTENT_ADDRESSED_PUBLICATION",
                      "destination":str(destination),
                      "bundle_id":args.bundle_id,
                      "formal_launch_authorized":False,
                      "accepted_round_authorized":False},sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (PublishError, OSError, ValueError, KeyError) as exc:
        print("NOT_READY: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
