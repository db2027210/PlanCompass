#!/usr/bin/env python3
"""Collect a read-only, content-addressed tree manifest over direct SSH.

The remote helper is Python 3.6 compatible.  It never writes remotely and
rejects special files.  The local publication is atomic and does not grant
experiment launch authority.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "preflight/runtime_manifests_20260729"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_tree_content_identity_v3 import (  # noqa: E402
    validate_inventory,
)
REMOTE_HELPER = r'''
from __future__ import print_function
import hashlib
import json
import os
import stat
import sys

root = os.path.abspath(sys.argv[1])
if not os.path.isdir(root) or os.path.islink(root):
    raise SystemExit("runtime root missing, not a directory, or a symlink")

rows = []
total_bytes = 0
for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
    dirs.sort()
    files.sort()
    for name in list(dirs):
        path = os.path.join(base, name)
        mode = os.lstat(path).st_mode
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if stat.S_ISLNK(mode):
            rows.append({
                "path": rel,
                "type": "symlink",
                "target": os.readlink(path),
            })
            dirs.remove(name)
        elif not stat.S_ISDIR(mode):
            raise SystemExit("special directory entry: " + rel)
        else:
            rows.append({
                "path": rel,
                "type": "directory",
                "mode": stat.S_IMODE(mode),
            })
    for name in files:
        path = os.path.join(base, name)
        info = os.lstat(path)
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if stat.S_ISLNK(info.st_mode):
            rows.append({
                "path": rel,
                "type": "symlink",
                "target": os.readlink(path),
            })
            continue
        if not stat.S_ISREG(info.st_mode):
            raise SystemExit("special file entry: " + rel)
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        rows.append({
            "path": rel,
            "type": "file",
            "size": info.st_size,
            "mode": stat.S_IMODE(info.st_mode),
            "sha256": digest.hexdigest(),
        })
        total_bytes += info.st_size

payload = {
    "schema_version": "remote_runtime_tree_inventory_v2",
    "root": root,
    "file_count": sum(1 for row in rows if row["type"] == "file"),
    "symlink_count": sum(1 for row in rows if row["type"] == "symlink"),
    "directory_count": sum(
        1 for row in rows if row["type"] == "directory"
    ),
    "total_file_bytes": total_bytes,
    "entries": rows,
}
print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
'''


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def collect(host: str, remote_root: str, label: str, output: Path) -> dict[str, Any]:
    if not label or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in label):
        raise ValueError("label must contain only lowercase letters, digits, '_' or '-'")
    if not remote_root.startswith("/") or remote_root == "/":
        raise ValueError("remote root must be a specific absolute path")
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            host,
            "python3",
            "-",
            remote_root,
        ],
        input=REMOTE_HELPER.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=1800,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"remote manifest failed rc={result.returncode}: "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    try:
        inventory = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("remote manifest returned invalid JSON") from exc
    if (
        not isinstance(inventory, dict)
        or inventory.get("schema_version") != "remote_runtime_tree_inventory_v2"
        or inventory.get("root") != remote_root
    ):
        raise RuntimeError("remote runtime inventory identity mismatch")

    tree_bytes = canonical_bytes(inventory)
    tree_sha256 = hashlib.sha256(tree_bytes).hexdigest()
    portable = validate_inventory(inventory)
    publication = {
        "schema_version": "mysql_algorithm_c_remote_runtime_manifest_v1",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "launch_authorized": False,
        "remote_mutation_performed": False,
        "host": host,
        "label": label,
        "remote_root": remote_root,
        # ``tree_sha256`` is retained as the legacy source-location digest.
        # Only this relative-entry identity is valid after an exact copy to a
        # different installation root.
        "tree_sha256_semantics": "legacy_location_bound_inventory_digest",
        "tree_sha256": tree_sha256,
        "tree_content_identity_algorithm": portable["algorithm"],
        "tree_content_sha256": portable["tree_content_sha256"],
        "inventory": inventory,
    }
    data = canonical_bytes(publication)
    digest = hashlib.sha256(data).hexdigest()
    path = output / f"{label}-{digest}.json"
    atomic_write(path, data)
    atomic_write(
        path.with_name(path.name + ".sha256"),
        f"{digest}  {path.name}\n".encode("ascii"),
    )
    return {
        "path": str(path),
        "sha256": digest,
        "tree_sha256": tree_sha256,
        "tree_content_sha256": portable["tree_content_sha256"],
        "file_count": inventory.get("file_count"),
        "symlink_count": inventory.get("symlink_count"),
        "total_file_bytes": inventory.get("total_file_bytes"),
        "remote_mutation_performed": False,
        "launch_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = collect(args.host, args.remote_root, args.label, args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
