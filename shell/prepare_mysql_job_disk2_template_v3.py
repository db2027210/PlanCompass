#!/usr/bin/env python3
"""Stage the sealed JOB template onto disk2 without reflinks or overlays.

The default mode is a read-only audit.  ``--execute`` performs a full physical
copy into a private sibling staging directory, verifies a deterministic tree
digest and regular-file count, seals the tree read-only, atomically renames the
tree, then atomically publishes its manifest and SHA-256 sidecar.  A crash
after the tree rename but before manifest publication is safely resumable:
without the manifest no successor builder can authorize execution.

This tool never starts MySQL, a container, a benchmark, or PostgreSQL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import uuid
from typing import Any, Mapping


SOURCE_TEMPLATE = Path("/disk1/ra_mysql_isolated_v2/job-base")
SOURCE_MANIFEST = Path("/disk1/ra_mysql_isolated_v2/job-storage-v2.manifest.json")
TARGET_TEMPLATE = Path("/disk2/ra_mysql_isolated_v2/job-base")
TARGET_MANIFEST = Path(
    "/disk2/ra_mysql_isolated_v2/job-storage-v3.manifest.json"
)
SOURCE_DEVICE = "259:5"
TARGET_DEVICE = "259:6"
COPY_MODE = "full_physical_copy_no_overlay_no_reflink"


class PrepareError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def major_minor(path: Path) -> str:
    device = path.stat().st_dev
    return f"{os.major(device)}:{os.minor(device)}"


def load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise PrepareError(f"{label} absent or symlinked: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PrepareError(f"{label} must be a JSON object")
    return value


def tree_identity(root: Path) -> tuple[str, int]:
    """Match sha256sum_regular_files_relative_v1 deterministically."""
    if not root.is_dir() or root.is_symlink():
        raise PrepareError(f"template must be a regular directory: {root}")
    rows: list[bytes] = []
    count = 0
    paths = sorted(
        root.rglob("*"),
        key=lambda item: os.fsencode(item.relative_to(root).as_posix()),
    )
    for path in paths:
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise PrepareError(f"template symlink prohibited: {relative}")
        if stat.S_ISREG(metadata.st_mode):
            # The frozen runtime verifier runs:
            # find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum
            # and hashes that exact sha256sum byte stream, whose names start ./.
            rows.append(f"{sha256(path)}  ./{relative}\n".encode("utf-8"))
            count += 1
        elif not stat.S_ISDIR(metadata.st_mode):
            raise PrepareError(f"special template entry prohibited: {relative}")
    digest = hashlib.sha256(b"".join(rows)).hexdigest()
    return digest, count


def validate_source(
    source_template: Path,
    source_manifest: Path,
    *,
    expected_device: str,
) -> tuple[dict[str, Any], str, int]:
    document = load_object(source_manifest, "source storage manifest")
    expected = {
        "schema_version": "mysql_job_local_storage_v2",
        "profile_id": "job_local_storage_v2_nvme_fullcopy",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "template_path": str(source_template),
        "device_major_minor": expected_device,
        "filesystem": "ext4",
        "fresh_copy_mode": COPY_MODE,
        "sealed_read_only": True,
        "clean_shutdown": True,
        "overlay": False,
        "reflink": False,
    }
    for key, wanted in expected.items():
        if document.get(key) != wanted:
            raise PrepareError(f"source manifest {key} differs")
    if major_minor(source_template) != expected_device:
        raise PrepareError("source template is not on the declared device")
    tree_hash, count = tree_identity(source_template)
    if (
        document.get("template_tree_sha256") != tree_hash
        or document.get("regular_file_count") != count
    ):
        raise PrepareError("source manifest tree identity differs")
    return document, tree_hash, count


def target_manifest_document(
    source: Mapping[str, Any],
    source_manifest: Path,
    source_tree_sha256: str,
    regular_file_count: int,
    target_template: Path,
) -> dict[str, Any]:
    return {
        "schema_version": "mysql_job_local_storage_v3",
        "profile_id": "job_local_storage_v3_disk2_nvme_fullcopy",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "template_path": str(target_template),
        "source_template_path": str(SOURCE_TEMPLATE),
        "source_manifest_path": str(source_manifest),
        "source_manifest_sha256": sha256(source_manifest),
        "source_template_tree_sha256": source_tree_sha256,
        "device": "/dev/nvme2n1",
        "device_major_minor": TARGET_DEVICE,
        "filesystem": "ext4",
        "fresh_copy_mode": COPY_MODE,
        "tree_hash_algorithm": "sha256sum_regular_files_relative_v1",
        "template_tree_sha256": source_tree_sha256,
        "regular_file_count": regular_file_count,
        "sealed_read_only": True,
        "seal_permission_policy":
            "regular_files_and_directories_no_owner_group_or_other_write",
        "clean_shutdown": True,
        "clean_shutdown_utc": source.get("clean_shutdown_utc"),
        "seal_container_image": source.get("seal_container_image"),
        "source_seal_container_exit_code": source.get("seal_container_exit_code"),
        "copy_tool": "cp",
        "copy_flags": ["-a", "--reflink=never"],
        "publication": "staged_tree_then_atomic_rename_then_atomic_manifest",
        "idempotency": "existing_target_tree_and_manifest_verified",
        "overlay": False,
        "reflink": False,
    }


def verify_existing(
    target_template: Path,
    target_manifest: Path,
    expected_manifest: Mapping[str, Any],
) -> bool:
    if major_minor(target_template) != TARGET_DEVICE:
        raise PrepareError("existing target template is not on disk2")
    tree_hash, count = tree_identity(target_template)
    if (
        tree_hash != expected_manifest["template_tree_sha256"]
        or count != expected_manifest["regular_file_count"]
    ):
        raise PrepareError("existing target template identity differs")
    sidecar_ok = False
    if target_manifest.exists():
        if load_object(target_manifest, "target storage manifest") != dict(
            expected_manifest
        ):
            raise PrepareError("existing target manifest differs")
        sidecar = target_manifest.with_name(target_manifest.name + ".sha256")
        expected_sidecar = (
            f"{sha256(target_manifest)}  {target_manifest.name}\n"
        )
        sidecar_ok = (
            sidecar.is_file()
            and not sidecar.is_symlink()
            and sidecar.read_text(encoding="ascii") == expected_sidecar
        )
    return sidecar_ok


def seal_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        mode = path.lstat().st_mode
        path.chmod(mode & ~0o222)
    root.chmod(root.lstat().st_mode & ~0o222)


def atomic_write(path: Path, payload: bytes, mode: int = 0o444) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    temporary.chmod(mode)
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def prepare(
    *,
    source_template: Path = SOURCE_TEMPLATE,
    source_manifest: Path = SOURCE_MANIFEST,
    target_template: Path = TARGET_TEMPLATE,
    target_manifest: Path = TARGET_MANIFEST,
    execute: bool = False,
) -> dict[str, Any]:
    source, tree_hash, count = validate_source(
        source_template, source_manifest, expected_device=SOURCE_DEVICE
    )
    if (
        not target_template.parent.is_dir()
        or target_template.parent.is_symlink()
        or major_minor(target_template.parent) != TARGET_DEVICE
    ):
        raise PrepareError("target parent is not the real disk2 authority")
    expected = target_manifest_document(
        source, source_manifest, tree_hash, count, target_template
    )
    if target_template.exists():
        sidecar_ok = verify_existing(target_template, target_manifest, expected)
        if execute and (not target_manifest.exists() or not sidecar_ok):
            payload = canonical(expected)
            if not target_manifest.exists():
                atomic_write(target_manifest, payload)
            atomic_write(
                target_manifest.with_name(target_manifest.name + ".sha256"),
                f"{hashlib.sha256(payload).hexdigest()}  {target_manifest.name}\n"
                .encode("ascii"),
            )
        status = "PUBLISHED_VERIFIED" if (
            target_manifest.exists()
            and target_manifest.with_name(
                target_manifest.name + ".sha256"
            ).exists()
        ) else "TARGET_VERIFIED_MANIFEST_PENDING"
    elif not execute:
        status = "READY_TO_STAGE"
    else:
        stage = target_template.with_name(
            f".{target_template.name}.stage.{os.getpid()}.{uuid.uuid4().hex}"
        )
        try:
            subprocess.run(
                [
                    "cp",
                    "-a",
                    "--reflink=never",
                    f"{source_template}/.",
                    str(stage),
                ],
                check=True,
            )
            if major_minor(stage) != TARGET_DEVICE:
                raise PrepareError("staged tree is not on disk2")
            staged_hash, staged_count = tree_identity(stage)
            if staged_hash != tree_hash or staged_count != count:
                raise PrepareError("staged tree identity differs")
            seal_tree(stage)
            stage.rename(target_template)
            verify_existing(target_template, target_manifest, expected)
            payload = canonical(expected)
            atomic_write(target_manifest, payload)
            atomic_write(
                target_manifest.with_name(target_manifest.name + ".sha256"),
                f"{hashlib.sha256(payload).hexdigest()}  {target_manifest.name}\n"
                .encode("ascii"),
            )
            status = "PUBLISHED_VERIFIED"
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise
    return {
        "schema_version": "mysql_job_disk2_template_prepare_result_v1",
        "status": status,
        "executed": execute,
        "source_template": str(source_template),
        "target_template": str(target_template),
        "target_manifest": str(target_manifest),
        "template_tree_sha256": tree_hash,
        "regular_file_count": count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        result = prepare(execute=args.execute)
    except (PrepareError, OSError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, indent=2))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
