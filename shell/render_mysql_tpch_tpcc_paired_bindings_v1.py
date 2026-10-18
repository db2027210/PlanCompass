#!/usr/bin/env python3
"""Render reciprocal sealed peer workload bindings for one paired debug flow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets


SCHEMA = "mysql_algorithm_c_paired_peer_workload_binding_v1"


class RenderError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def validate_attempt(value):
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{31,159}", value or "") is None:
        raise RenderError("qualification attempt ID differs")
    return value


def validate_remote_path(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise RenderError("peer metric path differs")
    return str(path)


def write_sealed(path, value):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise RenderError("refusing to overwrite paired binding")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    temporary = path.with_name(".%s.%s" % (path.name, secrets.token_hex(6)))
    descriptor = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(str(temporary), str(path))
    sidecar.write_text(
        hashlib.sha256(payload).hexdigest() + "  " + path.name + "\n",
        encoding="ascii",
    )
    os.chmod(str(sidecar), 0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tpch-attempt-id", required=True)
    parser.add_argument("--tpch-metric-path", required=True)
    parser.add_argument("--tpcc-attempt-id", required=True)
    parser.add_argument("--tpcc-metric-path", required=True)
    parser.add_argument("--tpch-peer-output", type=Path, required=True)
    parser.add_argument("--tpcc-peer-output", type=Path, required=True)
    args = parser.parse_args()
    tpch_attempt = validate_attempt(args.tpch_attempt_id)
    tpcc_attempt = validate_attempt(args.tpcc_attempt_id)
    if tpch_attempt == tpcc_attempt:
        raise RenderError("paired attempt IDs must differ")
    tpch_metric = validate_remote_path(args.tpch_metric_path)
    tpcc_metric = validate_remote_path(args.tpcc_metric_path)
    write_sealed(args.tpch_peer_output, {
        "schema_version": SCHEMA,
        "scenario": "tpcc_cpu",
        "qualification_attempt_id": tpcc_attempt,
        "workload_metric_stream_path": tpcc_metric,
    })
    write_sealed(args.tpcc_peer_output, {
        "schema_version": SCHEMA,
        "scenario": "tpch_memory",
        "qualification_attempt_id": tpch_attempt,
        "workload_metric_stream_path": tpch_metric,
    })
    print(json.dumps({
        "status": "SEALED_RECIPROCAL_PAIRED_BINDINGS_NOT_LAUNCHED",
        "tpch_peer_output": str(args.tpch_peer_output),
        "tpcc_peer_output": str(args.tpcc_peer_output),
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RenderError, ValueError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
