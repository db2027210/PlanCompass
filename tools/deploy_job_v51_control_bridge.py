#!/usr/bin/env python3
"""Atomically deploy the zero-workload JOB v51 preclaim bridge fix."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil


SOURCE = Path("/tmp/formal_job_outer_replay_v51.py")
TARGET = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02/"
    "launch_outer_attempt4_formal_dbqps_v1_retry3/formal_job_outer_replay.py"
)
AUDIT = TARGET.parent / "job_v51_control_bridge_deployment.json"
OLD_SHA256 = "e5f872489090a5585df59ccae11f5965423836629489bee834e9250db773a6cd"
NEW_SHA256 = "c53fbb20b401941ec83555bfd64211162bd873f0919558ae5394405af54a7b1b"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    if not SOURCE.is_file() or sha256(SOURCE) != NEW_SHA256:
        raise RuntimeError("JOB v51 bridge source SHA mismatch")
    if not TARGET.is_file():
        raise RuntimeError("JOB workload bridge target is missing")
    before = sha256(TARGET)
    backup_path = None
    if before == OLD_SHA256:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = TARGET.with_name(f"{TARGET.name}.pre_v51.{stamp}.{OLD_SHA256[:12]}")
        if backup_path.exists():
            raise RuntimeError("JOB v51 bridge backup path already exists")
        shutil.copy2(TARGET, backup_path)
        if sha256(backup_path) != OLD_SHA256:
            raise RuntimeError("JOB v51 bridge backup SHA mismatch")
        temporary = TARGET.with_name(f".{TARGET.name}.v51.{os.getpid()}")
        shutil.copy2(SOURCE, temporary)
        if sha256(temporary) != NEW_SHA256:
            raise RuntimeError("JOB v51 temporary bridge SHA mismatch")
        os.replace(temporary, TARGET)
    elif before != NEW_SHA256:
        raise RuntimeError(f"unexpected JOB bridge SHA before deployment: {before}")
    if sha256(TARGET) != NEW_SHA256:
        raise RuntimeError("JOB v51 deployed bridge SHA mismatch")
    payload = {
        "schema_version": "job_v51_control_bridge_deployment_v1",
        "status": "complete",
        "target": str(TARGET),
        "source_sha256": NEW_SHA256,
        "target_sha256_before": before,
        "target_sha256_after": sha256(TARGET),
        "backup_path": str(backup_path) if backup_path is not None else None,
        "backup_sha256": OLD_SHA256 if backup_path is not None else None,
        "workload_calls": 0,
        "ordinal_claims": 0,
        "mysql_touched": False,
    }
    if AUDIT.exists():
        previous = json.loads(AUDIT.read_text(encoding="utf-8"))
        if (
            previous.get("status") != "complete"
            or previous.get("target_sha256_after") != NEW_SHA256
            or int(previous.get("workload_calls", -1)) != 0
        ):
            raise RuntimeError("existing JOB v51 deployment audit is invalid")
    else:
        atomic_json(AUDIT, payload)
    print(json.dumps(payload, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
