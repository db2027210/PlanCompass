#!/usr/bin/env python3
"""Validate the live cross-project advisory-lock handoff without a workload."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=1200.0)
    parser.add_argument("--hold-seconds", type=float, default=2.0)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh validation output required: {output}")
    if args.timeout_seconds < 60 or not 1.0 <= args.hold_seconds <= 5.0:
        parser.error("unsafe validation timing")

    lease = coordination.acquire_exclusive(
        timeout_seconds=args.timeout_seconds,
    )
    lock_inode = lease.inode
    samples = []
    held_seconds = 0.0
    try:
        for label in ("acquired", "held"):
            kernel_visible = coordination.lock_held_exclusively_by(os.getpid())
            inventory = coordination.external_process_inventory(
                "cross_project_wave_coordination_validation", {0},
            )
            samples.append({
                "captured_at": now(),
                "label": label,
                "kernel_exclusive_lock_visible": kernel_visible,
                "external_controllers": coordination.sanitized_rows(
                    inventory["controllers"]
                ),
                "external_physical_workloads": coordination.sanitized_rows(
                    inventory["workloads"]
                ),
            })
            if not kernel_visible or inventory["workloads"]:
                raise RuntimeError(
                    "exclusive lease did not exclude external physical workloads"
                )
            if label == "acquired":
                time.sleep(args.hold_seconds)
    finally:
        held_seconds = coordination.release(lease)

    document = {
        "schema_version": "mysql_cross_project_wave_coordination_validation_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_state_mutation": False,
        "workload_started": False,
        "accepted_rounds_mutation": False,
        "lock_path": str(coordination.LOCK_PATH),
        "lock_inode": lock_inode,
        "lease_wait_seconds": lease.wait_seconds,
        "lease_held_seconds": held_seconds,
        "samples": samples,
        "completed_at": now(),
        "validator_source_path": str(Path(__file__).resolve()),
        "validator_source_sha256": sha(Path(__file__).resolve()),
        "coordination_source_path": str(Path(coordination.__file__).resolve()),
        "coordination_source_sha256": sha(Path(coordination.__file__).resolve()),
    }
    atomic(output, document)
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
