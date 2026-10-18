#!/usr/bin/env python3
"""Atomically publish one verified node02 tree stage on node04, no overwrite."""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import socket


ALLOWED = {
    "template": {
        "source": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/template",
        "final": "/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/template",
    },
    "runtime": {
        "source": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89",
        "final": "/mnt/nvme2n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89",
    },
}
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{15,127}$")


class PublishError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise PublishError(label + " is absent or symlinked")
    sidecar = path.with_name(path.name + ".sha256")
    required = sha256(path) + "  " + path.name + "\n"
    if (sidecar.is_symlink() or not sidecar.is_file()
            or sidecar.read_text(encoding="ascii") != required):
        raise PublishError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PublishError(label + " must be an object")
    return value


def rename_noreplace(source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise PublishError("renameat2(RENAME_NOREPLACE) is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                          ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result != 0:
        number = ctypes.get_errno()
        if number == errno.EEXIST:
            raise PublishError("final target appeared; no overwrite performed")
        raise PublishError("renameat2 failed: " + os.strerror(number))


def publish(args):
    if socket.gethostname() != "node04":
        raise PublishError("atomic publication is node04-only")
    if args.kind not in ALLOWED or not SAFE_ID.fullmatch(args.transaction_id):
        raise PublishError("transaction identity differs")
    expected = ALLOWED[args.kind]
    final = Path(expected["final"])
    if Path(args.final) != final:
        raise PublishError("final target differs from sealed authority")
    if final.parent.is_symlink() or not final.parent.is_dir():
        raise PublishError("final parent is absent or symlinked")
    if os.path.lexists(final):
        raise PublishError("final target already exists; no overwrite allowed")
    stage = Path(args.stage)
    if stage.is_symlink() or not stage.is_dir():
        raise PublishError("stage is absent, symlinked, or not a directory")
    stage = stage.resolve(strict=True)
    expected_stage_name = ".%s.stage-%s" % (final.name, args.transaction_id)
    if stage.parent != final.parent.resolve(strict=True) or stage.name != expected_stage_name:
        raise PublishError("stage is not the sealed same-filesystem direct sibling")
    if stage.stat().st_dev != final.parent.stat().st_dev:
        raise PublishError("stage and final parent are not on one filesystem")
    source_pre = load(args.source_pre_inventory, "source pre-copy inventory")
    source = load(args.source_inventory, "source post-copy inventory")
    destination = load(args.stage_inventory, "stage inventory")
    stability = load(args.source_stability_comparison, "source stability comparison")
    comparison = load(args.comparison, "comparison")
    if (source_pre.get("schema_version") != "mysql_job_portable_tree_inventory_v1"
            or source.get("schema_version") != "mysql_job_portable_tree_inventory_v1"
            or destination.get("schema_version") != "mysql_job_portable_tree_inventory_v1"
            or source_pre.get("hostname") != "node02"
            or source.get("hostname") != "node02"
            or destination.get("hostname") != "node04"
            or source_pre.get("root") != expected["source"]
            or source.get("root") != expected["source"]
            or destination.get("root") != str(stage)
            or stability.get("schema_version") != "mysql_job_portable_tree_copy_comparison_v1"
            or stability.get("status") != "PASS_SOURCE_DESTINATION_EXACT_TREE_IDENTITY"
            or stability.get("source_inventory_sha256") != sha256(args.source_pre_inventory)
            or stability.get("destination_inventory_sha256") != sha256(args.source_inventory)
            or stability.get("content_identity_sha256") != source.get("content_identity_sha256")
            or comparison.get("schema_version") != "mysql_job_portable_tree_copy_comparison_v1"
            or comparison.get("status") != "PASS_SOURCE_DESTINATION_EXACT_TREE_IDENTITY"
            or comparison.get("source_inventory_sha256") != sha256(args.source_inventory)
            or comparison.get("destination_inventory_sha256") != sha256(args.stage_inventory)
            or comparison.get("content_identity_sha256") != source.get("content_identity_sha256")
            or source.get("content_identity_sha256") != destination.get("content_identity_sha256")):
        raise PublishError("source/stage comparison authority differs")
    parent_fd = os.open(final.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_fd)
        rename_noreplace(stage, final)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    return {
        "schema_version": "mysql_job_v17_atomic_tree_publication_receipt_v1",
        "status": "PASS_ATOMIC_RENAME_NOREPLACE_PUBLISHED",
        "kind": args.kind, "transaction_id": args.transaction_id,
        "source": expected["source"], "final": str(final),
        "source_stability_comparison_sha256": sha256(args.source_stability_comparison),
        "comparison_sha256": sha256(args.comparison),
        "content_identity_sha256": source["content_identity_sha256"],
        "published_at_utc": datetime.now(timezone.utc).isoformat(),
        "formal_launch_authorized": False, "accepted_round_authorized": False,
        "qualification_credit": 0, "r0_credit": 0,
    }


def write_receipt(path, value):
    path = Path(path)
    sidecar = path.with_name(path.name + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise PublishError("publication receipt output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    path.write_bytes(payload); path.chmod(0o400)
    sidecar.write_text(hashlib.sha256(payload).hexdigest() + "  " + path.name + "\n",
                       encoding="ascii")
    sidecar.chmod(0o400)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=sorted(ALLOWED), required=True)
    parser.add_argument("--transaction-id", required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--final", type=Path, required=True)
    parser.add_argument("--source-pre-inventory", type=Path, required=True)
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--stage-inventory", type=Path, required=True)
    parser.add_argument("--source-stability-comparison", type=Path, required=True)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise PublishError("publication receipt output already exists")
    result = publish(args)
    write_receipt(args.output, result)
    print(json.dumps({"status": result["status"], "final": result["final"],
                      "receipt": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (PublishError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
