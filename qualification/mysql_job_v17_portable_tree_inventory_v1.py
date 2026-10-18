#!/usr/bin/env python3
"""Create or compare an exact portable read-only tree inventory."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
from datetime import datetime, timezone


class InventoryError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def xattrs(path):
    result = {}
    for raw_name in sorted(os.listxattr(path, follow_symlinks=False)):
        raw_value = os.getxattr(path, raw_name, follow_symlinks=False)
        result[raw_name] = base64.b64encode(raw_value).decode("ascii")
    return result


def inventory(root, label):
    root = Path(root).resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise InventoryError("inventory root is absent, symlinked, or not a directory")
    root_stat = root.lstat(); entries = []; hardlinks = {}
    logical = 0; allocated_inodes = {(root_stat.st_dev, root_stat.st_ino)}
    allocated = root_stat.st_blocks * 512
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix(); row_stat = path.lstat()
        allocation_key = (row_stat.st_dev, row_stat.st_ino)
        if allocation_key not in allocated_inodes:
            allocated_inodes.add(allocation_key)
            allocated += row_stat.st_blocks * 512
        common = {"path": relative, "mode": "%04o" % stat.S_IMODE(row_stat.st_mode),
                  "uid": row_stat.st_uid, "gid": row_stat.st_gid,
                  "mtime_ns": row_stat.st_mtime_ns, "xattrs_base64": xattrs(path)}
        if stat.S_ISDIR(row_stat.st_mode):
            row = dict(common, type="directory")
        elif stat.S_ISLNK(row_stat.st_mode):
            row = dict(common, type="symlink", target=os.readlink(path))
        elif stat.S_ISREG(row_stat.st_mode):
            key = (row_stat.st_dev, row_stat.st_ino)
            leader = hardlinks.get(key)
            if leader is None:
                hardlinks[key] = relative
                row = dict(common, type="file", size=row_stat.st_size,
                           sha256=file_sha(path), hardlink_to=None)
                logical += row_stat.st_size
            else:
                row = dict(common, type="file", size=row_stat.st_size,
                           sha256=file_sha(path), hardlink_to=leader)
        else:
            raise InventoryError("special tree entry is prohibited: " + relative)
        entries.append(row)
    identity = {
        "algorithm": "mysql_job_portable_tree_identity_v1",
        "root_mode": "%04o" % stat.S_IMODE(root_stat.st_mode),
        "root_uid": root_stat.st_uid, "root_gid": root_stat.st_gid,
        "root_mtime_ns": root_stat.st_mtime_ns,
        "root_xattrs_base64": xattrs(root),
        "entries": entries,
    }
    return {
        "schema_version": "mysql_job_portable_tree_inventory_v1",
        "status": "PASS_EXACT_READ_ONLY_INVENTORY_NOT_COPY_AUTHORITY",
        "label": label, "root": str(root),
        "hostname": socket.gethostname(),
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "content_identity_sha256": sha_bytes(canonical(identity)),
        "identity": identity, "entry_count": len(entries),
        "logical_unique_file_bytes": logical,
        "observed_allocated_bytes_unique_inodes": allocated,
        "remote_mutation_performed": False, "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def load(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise InventoryError(label + " inventory is absent or symlinked")
    sidecar = path.with_name(path.name + ".sha256")
    required = file_sha(path) + "  " + path.name + "\n"
    if (sidecar.is_symlink() or not sidecar.is_file()
            or sidecar.read_text(encoding="ascii") != required):
        raise InventoryError(label + " inventory sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    identity = value.get("identity") or {}
    if (value.get("schema_version") != "mysql_job_portable_tree_inventory_v1"
            or value.get("status") != "PASS_EXACT_READ_ONLY_INVENTORY_NOT_COPY_AUTHORITY"
            or value.get("content_identity_sha256") != sha_bytes(canonical(identity))
            or value.get("remote_mutation_performed") is not False):
        raise InventoryError(label + " inventory authority differs")
    return value


def compare(source_path, destination_path):
    source = load(source_path, "source"); destination = load(destination_path, "destination")
    if (source["content_identity_sha256"] != destination["content_identity_sha256"]
            or source["identity"] != destination["identity"]
            or source["entry_count"] != destination["entry_count"]
            or source["logical_unique_file_bytes"] != destination["logical_unique_file_bytes"]):
        raise InventoryError("source/destination portable tree identity differs")
    return {
        "schema_version": "mysql_job_portable_tree_copy_comparison_v1",
        "status": "PASS_SOURCE_DESTINATION_EXACT_TREE_IDENTITY",
        "source_inventory": str(Path(source_path).resolve()),
        "source_inventory_sha256": file_sha(source_path),
        "destination_inventory": str(Path(destination_path).resolve()),
        "destination_inventory_sha256": file_sha(destination_path),
        "content_identity_sha256": source["content_identity_sha256"],
        "entry_count": source["entry_count"],
        "logical_unique_file_bytes": source["logical_unique_file_bytes"],
        "atomic_publication_still_required": True,
        "formal_launch_authorized": False, "accepted_round_authorized": False,
    }


def write(path, value):
    path = Path(path)
    if path.exists() or path.is_symlink() or path.with_name(path.name + ".sha256").exists():
        raise InventoryError("inventory output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value); path.write_bytes(payload); path.chmod(0o400)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(sha_bytes(payload) + "  " + path.name + "\n", encoding="ascii"); sidecar.chmod(0o400)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--root", type=Path)
    modes.add_argument("--compare-source", type=Path)
    parser.add_argument("--compare-destination", type=Path)
    parser.add_argument("--label", default="tree")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.root is not None:
        if args.compare_destination is not None: parser.error("inventory mode rejects --compare-destination")
        value = inventory(args.root, args.label)
    else:
        if args.compare_destination is None: parser.error("compare mode requires --compare-destination")
        value = compare(args.compare_source, args.compare_destination)
    write(args.output, value); print(json.dumps({key: value[key] for key in ("status", "content_identity_sha256", "entry_count", "logical_unique_file_bytes")}, sort_keys=True))


if __name__ == "__main__":
    try: main()
    except (InventoryError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
