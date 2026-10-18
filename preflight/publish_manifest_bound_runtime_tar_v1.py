#!/usr/bin/env python3
"""Publish a trusted runtime tar as an immutable manifest-bound directory.

This helper is Python 3.6 compatible.  It never replaces an existing target:
an existing directory is accepted only after exact manifest verification.
"""

from __future__ import print_function

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tarfile
import tempfile

# Importing a sealed verifier must not mutate its content-addressed bundle.
sys.dont_write_bytecode = True


class PublishError(RuntimeError):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value):
    return (
        json.dumps(
            value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def atomic_json(path, value):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise PublishError("refusing to overwrite publication evidence")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    temporary = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    temporary.write_bytes(payload)
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.rename(str(temporary), str(path))
    sidecar.write_text(
        "%s  %s\n" % (hashlib.sha256(payload).hexdigest(), path.name),
        encoding="ascii",
    )


def load_identity_helper(path):
    module_spec = importlib.util.spec_from_file_location(
        "sealed_runtime_tree_identity", str(path)
    )
    if module_spec is None or module_spec.loader is None:
        raise PublishError("sealed runtime identity helper cannot be loaded")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def normalized_member_name(name):
    path = PurePosixPath(name)
    if path.is_absolute():
        raise PublishError("archive contains an absolute member path")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts:
        return PurePosixPath(".")
    if ".." in parts:
        raise PublishError("archive contains parent traversal")
    return PurePosixPath(*parts)


def validate_archive(archive):
    symlink_paths = set()
    member_paths = []
    member_kinds = {}
    hard_links = []
    with tarfile.open(str(archive), "r:*") as stream:
        for member in stream:
            member_path = normalized_member_name(member.name)
            if member_path in member_kinds and member_path != PurePosixPath("."):
                raise PublishError("archive contains a duplicate member path")
            if member.ischr() or member.isblk() or member.isfifo():
                raise PublishError("archive contains a special entry")
            if member.islnk():
                link_path = normalized_member_name(member.linkname)
                hard_links.append((member_path, link_path))
                member_kinds[member_path] = "hardlink"
                member_paths.append(member_path)
                continue
            if not (
                member.isdir()
                or member.isreg()
                or member.issym()
            ):
                raise PublishError("archive contains an unsupported entry type")
            if member.issym():
                symlink_paths.add(member_path)
                member_kinds[member_path] = "symlink"
            elif member.isdir():
                member_kinds[member_path] = "directory"
            else:
                member_kinds[member_path] = "regular"
            member_paths.append(member_path)
    for member_path in member_paths:
        parents = tuple(member_path.parents)
        if any(parent in symlink_paths for parent in parents):
            raise PublishError("archive member descends through a symlink")
    for member_path, link_path in hard_links:
        if member_kinds.get(link_path) != "regular":
            raise PublishError("archive hard link target is not a regular member")
        if any(parent in symlink_paths for parent in link_path.parents):
            raise PublishError("archive hard link target descends through a symlink")


def verify_tree(helper, manifest, root, expected_tree_sha256):
    document = json.loads(Path(manifest).read_text(encoding="utf-8"))
    value = helper.verify_tree_manifest(Path(root), document)
    if value.get("tree_content_sha256") != expected_tree_sha256:
        raise PublishError("runtime tree content identity differs")
    if value.get("status") != "PASS_EXACT_PATH_INDEPENDENT_TREE_READBACK":
        raise PublishError("runtime tree readback did not pass")
    return value


def fsync_directory(path):
    descriptor = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish(args):
    archive = args.archive.resolve(strict=True)
    helper_path = args.identity_helper.resolve(strict=True)
    manifest = args.authority_manifest.resolve(strict=True)
    target = args.target
    if target.is_symlink():
        raise PublishError("runtime target must not be a symlink")
    if sha256(archive) != args.archive_sha256:
        raise PublishError("runtime archive sha256 differs")
    helper = load_identity_helper(helper_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    lock = target.parent / (".%s.publish.lock" % target.name)
    try:
        os.mkdir(str(lock), 0o700)
    except FileExistsError:
        raise PublishError("runtime publication lock already exists")
    stage = None
    try:
        if target.exists():
            if not target.is_dir():
                raise PublishError("runtime target exists with unsafe type")
            readback = verify_tree(
                helper, manifest, target, args.tree_content_sha256
            )
            mode = "ADOPTED_IDENTICAL"
        else:
            validate_archive(archive)
            stage = Path(
                tempfile.mkdtemp(
                    prefix=".%s.stage." % target.name,
                    dir=str(target.parent),
                )
            )
            with tarfile.open(str(archive), "r:*") as stream:
                stream.extractall(str(stage))
            readback = verify_tree(
                helper, manifest, stage, args.tree_content_sha256
            )
            os.rename(str(stage), str(target))
            stage = None
            fsync_directory(target.parent)
            readback = verify_tree(
                helper, manifest, target, args.tree_content_sha256
            )
            mode = "PUBLISHED_NEW"
        result = {
            "schema_version": "manifest_bound_runtime_tar_publication_v1",
            "status": "PUBLISHED_EXACT_MANIFEST_BOUND",
            "publish_mode": mode,
            "archive": str(archive),
            "archive_sha256": args.archive_sha256,
            "target": str(target),
            "tree_content_sha256": args.tree_content_sha256,
            "identity_helper_sha256": sha256(helper_path),
            "authority_manifest_sha256": sha256(manifest),
            "exact_readback": readback,
            "atomic_non_overwriting": True,
            "accepted_round_authorized": False,
        }
        atomic_json(args.output, result)
        return result
    finally:
        if stage is not None and stage.exists():
            shutil.rmtree(str(stage))
        try:
            os.rmdir(str(lock))
        except FileNotFoundError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--identity-helper", type=Path, required=True)
    parser.add_argument("--authority-manifest", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--tree-content-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = publish(args)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishError as error:
        print("RUNTIME_PUBLICATION_FAILED: %s" % error)
        raise SystemExit(2)
