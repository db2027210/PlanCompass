#!/usr/bin/env python3
"""Atomically fix the pre-launch JOB technical-failure accounting flag."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


FORMAL = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02/history/job/outer/attempt_7_cap5_contract_20260726/"
    "formal10_after_round10_gate_20260729T1055"
)
EXPECTED_CONFIG_SHA = "e43cfccf361ac2caf3ea695ddf78a44fcacc3a620875c91686fa6d41d4e4ee1e"


def atomic_write(path: Path, payload: dict) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def main() -> int:
    config_path = FORMAL / "config.json"
    manifest_path = FORMAL / "entry_manifest.json"
    if hashlib.sha256(config_path.read_bytes()).hexdigest() != EXPECTED_CONFIG_SHA:
        raise RuntimeError("FAIL_CLOSED: pre-launch config SHA mismatch")
    if (FORMAL / "optimizer.pid").exists():
        raise RuntimeError("FAIL_CLOSED: optimizer pid file already exists")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    protocol = config["formal_outer_protocol"]
    if protocol.get("candidate_failure_consumes_budget") is not False:
        raise RuntimeError("FAIL_CLOSED: observed failure accounting state mismatch")
    protocol["candidate_failure_consumes_budget"] = True
    atomic_write(config_path, config)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["candidate_failure_consumes_physical_budget"] = True
    manifest["config_sha256"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    atomic_write(manifest_path, manifest)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(2)
