#!/usr/bin/env python3
"""Publish v12 with a larger JOB buffer pool under a memory-bottleneck envelope."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V11 = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v11"
    / "four_scene_effective_resource_successor.json"
)
FAILED_R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v14_20260810T015633p0800/r0_binding.json"
)
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260810_v12"
    / "four_scene_effective_resource_successor.json"
)
JOB_MEMORY_BYTES = 10 * 1024**3
JOB_BUFFER_POOL_BYTES = 8 * 1024**3


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"JSON object required: {path}")
    return value


def main() -> int:
    predecessor = load(V11)
    failed = load(FAILED_R0)
    resources = failed.get("resource_summary") or {}
    if (
        predecessor.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v11"
        or failed.get("status") != "R0_FAILED_CLOSED"
        or failed.get("failures") != []
        or failed.get("wall_time_seconds", 0) <= 900
        or failed.get("formal_outer_launch_authorized") is not False
        or any((failed.get("accepted_rounds") or {}).values())
        or not all(
            float((resources.get(worker) or {}).get("memory_peak_fraction", 0)) >= 0.8
            for worker in ("job_w1", "job_w2")
        )
    ):
        raise SystemExit("v12 predecessor authority differs")
    workers = []
    for worker in predecessor["workers"]:
        row = dict(worker)
        if row["scene"] == "job_memory":
            row["v11_memory_bytes"] = row["memory_bytes"]
            row["v11_innodb_buffer_pool_bytes"] = row["innodb_buffer_pool_bytes"]
            row["memory_bytes"] = JOB_MEMORY_BYTES
            row["innodb_buffer_pool_bytes"] = JOB_BUFFER_POOL_BYTES
        workers.append(row)
    successor = dict(predecessor)
    successor.update({
        "schema_version": "mysql_disk2_four_scene_two_worker_effective_resource_successor_v12",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "predecessor": {"path": str(V11.relative_to(ROOT)), "sha256": sha(V11)},
        "successor_reason": "JOB_113_query_wall_1075_to_1085_exceeds_900_with_4GiB_buffer_pool",
        "successor_resource_envelope_change": {
            "job_memory_bytes": {
                "from": 6174015488,
                "to": JOB_MEMORY_BYTES,
            },
            "job_innodb_buffer_pool_bytes": {
                "from": 4294967296,
                "to": JOB_BUFFER_POOL_BYTES,
            },
            "all_other_worker_fields_unchanged": True,
            "workload_unchanged": True,
            "long_query_concurrency_cap_unchanged": 4,
        },
        "predecessor_r0_failure": {
            "path": str(FAILED_R0.relative_to(ROOT)),
            "sha256": sha(FAILED_R0),
            "wall_time_seconds": failed["wall_time_seconds"],
            "job_memory_peak_fraction": {
                worker: resources[worker]["memory_peak_fraction"]
                for worker in ("job_w1", "job_w2")
            },
            "accepted_rounds_credit": 0,
        },
        "workers": workers,
        "formal_launch_authorized": False,
        "launch_performed": False,
        "accepted_rounds": {scene: 0 for scene in predecessor["active_scenes"]},
    })
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
