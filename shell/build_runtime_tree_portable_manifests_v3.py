#!/usr/bin/env python3
"""Convert fresh directory-aware source publications to portable v3 manifests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

try:
    from . import runtime_tree_content_identity_v3 as tree
except ImportError:
    import runtime_tree_content_identity_v3 as tree


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "preflight/runtime_manifests_v3_20260729"
OUTPUT_ROOT = ROOT / "preflight/runtime_portable_manifests_v3_20260729"
SOURCES = {
    "node04_jdk23_v3": {
        "name": (
            "node04_jdk23_v3-"
            "bf3b427e9510ac77fe6618a60f99f51e1562884d3e81f0cec3cd9f47bfa4852e.json"
        ),
        "sha256":
            "bf3b427e9510ac77fe6618a60f99f51e1562884d3e81f0cec3cd9f47bfa4852e",
        "tree_content_sha256":
            "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c",
        "artifacts": ("bin/java", "release"),
    },
    "node04_python311_v3": {
        "name": (
            "node04_python311_v3-"
            "0db72f3e4e14b3902ff94d17eca9e5c8af97577efdf6ab8b3941fbe189da8c33.json"
        ),
        "sha256":
            "0db72f3e4e14b3902ff94d17eca9e5c8af97577efdf6ab8b3941fbe189da8c33",
        "tree_content_sha256":
            "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89",
        "artifacts": ("bin/python3.11",),
    },
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_one(label: str, spec: dict[str, Any]) -> dict[str, Any]:
    source = SOURCE_ROOT / spec["name"]
    if source.is_symlink() or not source.is_file() or sha256(source) != spec["sha256"]:
        raise RuntimeError(f"fresh source publication differs: {label}")
    publication = json.loads(source.read_text(encoding="utf-8"))
    manifest = tree.portable_manifest_from_publication(
        publication, spec["sha256"]
    )
    entries = {
        row["path"]: row
        for row in manifest["content_identity"]["entries"]
    }
    manifest["artifacts"] = {
        relative: entries[relative]["sha256"]
        for relative in spec["artifacts"]
    }
    tree.validate_portable_manifest(manifest)
    if manifest["tree_content_sha256"] != spec["tree_content_sha256"]:
        raise RuntimeError(f"fresh portable content identity differs: {label}")
    payload = tree.canonical(manifest)
    digest = hashlib.sha256(payload).hexdigest()
    path = OUTPUT_ROOT / f"{label}-{digest}.json"
    atomic(path, payload)
    atomic(
        path.with_name(path.name + ".sha256"),
        f"{digest}  {path.name}\n".encode("ascii"),
    )
    return {
        "path": str(path),
        "manifest_sha256": digest,
        "tree_content_sha256": manifest["tree_content_sha256"],
        "file_count": manifest["content_identity"]["file_count"],
        "symlink_count": manifest["content_identity"]["symlink_count"],
        "directory_count": manifest["content_identity"]["directory_count"],
    }


def main() -> int:
    result = {
        label: build_one(label, spec)
        for label, spec in SOURCES.items()
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
