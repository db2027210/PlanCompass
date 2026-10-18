#!/usr/bin/env python3
"""Materialize the v2 resource readback from a verified campaign readback."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    state = load(run_dir / "campaign_runtime.json")
    raw = load(run_dir / "resource_readback.json")
    if raw.get("verified") is not True:
        raise RuntimeError("source resource readback is not verified")
    if state.get("phase") != "R0_COMPLETE" or state.get("r0_complete") is not True:
        raise RuntimeError("campaign is not a completed R0")
    requested = state.get("resource_envelope")
    cgroup = raw.get("effective_cgroup")
    mysql = raw.get("effective_mysql")
    if not all(isinstance(value, dict) for value in (requested, cgroup, mysql)):
        raise RuntimeError("resource readback fields are incomplete")
    device = str(cgroup["device_major_minor"])
    read_bps = int(cgroup["io_read_bps"])
    write_bps = int(cgroup["io_write_bps"])
    value = {
        "schema_version": "mysql_sgd_effective_resource_readback_v2",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scenario": state["scenario"],
        "benchmark": state["benchmark"],
        "run_id": state["run_id"],
        "host": state["host"],
        "container": state["container"],
        "verified": True,
        "requested": requested,
        "effective": {
            "container_id": raw["container_id"],
            "cpu_quota_us": int(cgroup["cpu_quota_us"]),
            "cpu_period_us": int(cgroup["cpu_period_us"]),
            "cpuset_cpus": str(cgroup["cpuset_cpus"]),
            "cpuset_mems": str(cgroup["cpuset_mems"]),
            "memory_bytes": int(cgroup["memory_bytes"]),
            "memory_swap_bytes": int(cgroup["memory_swap_bytes"]),
            "pids_max": int(cgroup["pids_max"]),
            "io_device_major_minor": device,
            "io_read": f"{device} {read_bps}",
            "io_read_bps": read_bps,
            "io_write": f"{device} {write_bps}",
            "io_write_bps": write_bps,
            "buffer_pool_bytes": int(mysql["innodb_buffer_pool_bytes"]),
        },
    }
    atomic_json(run_dir / "effective_resource_readback_v2.json", value)
    print(run_dir / "effective_resource_readback_v2.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
