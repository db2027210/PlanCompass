#!/usr/bin/env python3
"""Publish v10 after v9 exposed XP CPU/IO envelope misses."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V9 = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v9"
    / "four_scene_effective_resource_successor.json"
)
FAILED_R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v9_20260809T212500p0800/r0_binding.json"
)
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v10"
    / "four_scene_effective_resource_successor.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"JSON object required: {path}")
    return value


def main() -> int:
    predecessor = load(V9)
    failed = load(FAILED_R0)
    bottlenecks = failed.get("target_bottleneck_gate", {}).get("workers", {})
    if (
        predecessor.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v9"
        or failed.get("status") != "R0_FAILED_CLOSED"
        or any((failed.get("accepted_rounds") or {}).values())
        or bottlenecks.get("xp_cpu_w1", {}).get("passed") is not False
        or bottlenecks.get("xp_cpu_w2", {}).get("passed") is not False
        or bottlenecks.get("xp_io_w1", {}).get("passed") is not True
        or bottlenecks.get("xp_io_w2", {}).get("passed") is not True
    ):
        raise SystemExit("v10 predecessor authority differs")
    successor = dict(predecessor)
    workers = []
    for worker in predecessor["workers"]:
        row = dict(worker)
        if row["scene"] == "hybench_xp_cpu":
            row["v9_cpu_quota_cores"] = row["cpu_quota_cores"]
            row["cpu_quota_cores"] = 1.8
        elif row["scene"] == "hybench_xp_io":
            row["v9_io_write_bps"] = row["io_write_bps"]
            row["io_write_bps"] = 1048576
        workers.append(row)
    successor.update({
        "schema_version": "mysql_disk2_four_scene_two_worker_effective_resource_successor_v10",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "predecessor": {"path": str(V9.relative_to(ROOT)), "sha256": sha(V9)},
        "successor_reason": "XP_CPU_target_below_0.8_and_XP_IO_tail_exceeded_900s",
        "successor_resource_envelope_change": {
            "hybench_xp_cpu_quota_cores": {"from": 2.3, "to": 1.8},
            "hybench_xp_io_write_bps": {"from": 524288, "to": 1048576},
            "cpusets_memory_swap_pids_read_caps_unchanged": True,
            "native_xp_workload_unchanged": True,
        },
        "predecessor_r0_failure": {
            "path": str(FAILED_R0.relative_to(ROOT)),
            "sha256": sha(FAILED_R0),
            "xp_cpu_observed_fraction": {
                key: bottlenecks[key]["observed_fraction"]
                for key in ("xp_cpu_w1", "xp_cpu_w2")
            },
            "xp_io_timeout_worker": "xp_io_w2",
            "accepted_rounds_credit": 0,
        },
        "workers": workers,
        "formal_launch_authorized": False,
        "launch_performed": False,
        "accepted_rounds": {scene: 0 for scene in predecessor["active_scenes"]},
    })
    unchanged_scenes = {"job_memory", "tpch_sf4_memory"}
    old = [row for row in predecessor["workers"] if row["scene"] in unchanged_scenes]
    new = [row for row in workers if row["scene"] in unchanged_scenes]
    if old != new:
        raise SystemExit("v10 unexpectedly changed JOB/TPC-H")
    if OUTPUT.exists():
        raise SystemExit(f"refusing to replace frozen output: {OUTPUT}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(
        json.dumps(successor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
