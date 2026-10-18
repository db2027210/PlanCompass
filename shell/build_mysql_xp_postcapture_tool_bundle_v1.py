#!/usr/bin/env python3
"""Publish the immutable offline XP post-capture promotion tool bundle."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_postcapture_tool_v1"
SOURCE_RUNTIME_ID = (
    "a7d2386cd2a703b6bb73e5269cb8cace807b5fb0eb5fb3d5ccb6e71c2b9caeed"
)
SOURCE_RUNTIME = ROOT / "artifacts/mysql_xp_v8" / SOURCE_RUNTIME_ID
FILES = {
    "shell/mysql_xp_postcapture_promotion_v1.py":
        ROOT / "shell/mysql_xp_postcapture_promotion_v1.py",
    "tests/test_mysql_xp_postcapture_promotion_v1.py":
        ROOT / "tests/test_mysql_xp_postcapture_promotion_v1.py",
    "docs/mysql_xp_postcapture_promotion_v1.md":
        ROOT / "docs/mysql_xp_postcapture_promotion_v1.md",
    "preflight/mysql_algorithm_c_formal10_contract_20260729.json":
        ROOT / "preflight/mysql_algorithm_c_formal10_contract_20260729.json",
}
EXPECTED_CONTRACT_SHA256 = (
    "2f3d7775e005a31dc5b950277ec5153c2d4b76b67ca7bb42a4f52b5026ae5641"
)


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def remove_staging(path: Path) -> None:
    """Remove only this builder's private staging tree, including 0444 input."""
    if not path.exists():
        return
    if (
        path.parent != OUTPUT_ROOT
        or not path.name.startswith(".mysql_xp_postcapture_tool.")
    ):
        raise RuntimeError(f"refusing unsafe staging cleanup: {path}")
    for candidate in sorted(path.rglob("*"), reverse=True):
        try:
            os.chmod(candidate, 0o700 if candidate.is_dir() else 0o600)
        except FileNotFoundError:
            pass
    os.chmod(path, 0o700)
    shutil.rmtree(path)


def verify(path: Path, expected_id: str | None = None) -> dict[str, Any]:
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"regular tool bundle directory required: {path}")
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version")
        != "mysql_xp_postcapture_promotion_tool_v1"
        or path.name != manifest.get("bundle_id")
        or (expected_id is not None and path.name != expected_id)
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("execution_authorized") is not False
        or manifest.get("data_plane_operations_authorized") is not False
        or manifest.get("formal10_contract_sha256")
        != EXPECTED_CONTRACT_SHA256
    ):
        raise RuntimeError("XP promotion tool bundle identity/scope differs")
    for relative, expected in manifest["payload_files"].items():
        candidate = path / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError(f"tool payload absent: {relative}")
        if sha256(candidate) != expected:
            raise RuntimeError(f"tool payload differs: {relative}")
    return manifest


def main() -> int:
    if sha256(FILES[
        "preflight/mysql_algorithm_c_formal10_contract_20260729.json"
    ]) != EXPECTED_CONTRACT_SHA256:
        raise RuntimeError("shared formal10 contract hash differs")
    source_manifest = SOURCE_RUNTIME / "manifest.json"
    if (
        SOURCE_RUNTIME.is_symlink()
        or not SOURCE_RUNTIME.is_dir()
        or not source_manifest.is_file()
    ):
        raise RuntimeError("XP a7d238 source runtime is absent")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    parent = Path(
        tempfile.mkdtemp(prefix=".mysql_xp_postcapture_tool.", dir=OUTPUT_ROOT)
    )
    staging = parent / "payload"
    staging.mkdir()
    try:
        for relative, source in FILES.items():
            if source.is_symlink() or not source.is_file():
                raise RuntimeError(f"required tool input absent: {source}")
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        runtime_destination = (
            staging / "artifacts/mysql_xp_v8" / SOURCE_RUNTIME_ID
        )
        shutil.copytree(
            SOURCE_RUNTIME,
            runtime_destination,
            symlinks=False,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
        )
        payload = {
            str(path.relative_to(staging)): sha256(path)
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        }
        identity = {
            "schema_version": "mysql_xp_postcapture_promotion_tool_v1",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "supersedes_tool_bundle_ids": [
                "823a28fd8c2f9b29b449f7f3ee685d9179e7a023ae8a341517d1c9bd6c223166",
                "89757cefc7119637da852c2a1e59aaa9f9607a4d201b6330f44beef22dfb78d6",
            ],
            "supersession_reason":
                "self_contained_source_runtime_and_contract_closure",
            "source_runtime_bundle_id":
                SOURCE_RUNTIME_ID,
            "source_runtime_manifest_sha256": sha256(source_manifest),
            "formal10_contract_sha256": EXPECTED_CONTRACT_SHA256,
            "execution_authorized": False,
            "data_plane_operations_authorized": False,
            "synthetic_evidence_may_authorize": False,
            "publication_outputs": {
                "content_addressed": True,
                "transactional": True,
                "idempotent": True,
                "immutable_after_publish": True,
            },
            "sealed_test_contract": {
                "clean_cwd": True,
                "pythonpath_tool_bundle_only": True,
                "pytest_cache_disabled": True,
                "expected_test_count": 20,
                "isolated_cli_verify_source": True,
            },
            "payload_files": payload,
        }
        bundle_id = hashlib.sha256(canonical(identity)).hexdigest()
        manifest = dict(identity)
        manifest["bundle_id"] = bundle_id
        (staging / "manifest.json").write_bytes(canonical(manifest))
        destination = OUTPUT_ROOT / bundle_id
        if destination.exists():
            verify(destination, bundle_id)
            print(destination)
            return 0
        publish = OUTPUT_ROOT / f".{bundle_id}.{os.getpid()}"
        os.rename(staging, publish)
        for path in sorted(publish.rglob("*"), reverse=True):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        os.chmod(publish, 0o555)
        try:
            os.rename(publish, destination)
        except FileExistsError:
            verify(destination, bundle_id)
        verify(destination, bundle_id)
        print(destination)
        return 0
    finally:
        if parent.exists():
            remove_staging(parent)


if __name__ == "__main__":
    raise SystemExit(main())
