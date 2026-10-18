#!/usr/bin/env python3
"""Publish v9 with a reliable cold-cache full-JOB CPU envelope."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V8 = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v8"
    / "four_scene_effective_resource_successor.json"
)
FAILED_R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v8_20260809T202300p0800/r0_binding.json"
)
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v9"
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
    predecessor = load(V8)
    failed = load(FAILED_R0)
    if (
        predecessor.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v8"
        or failed.get("status") != "R0_FAILED_CLOSED"
        or any((failed.get("accepted_rounds") or {}).values())
    ):
        raise SystemExit("v9 predecessor authority differs")
    successor = dict(predecessor)
    workers = []
    for worker in predecessor["workers"]:
        row = dict(worker)
        if row["scene"] == "job_memory":
            row["v8_cpu_quota_cores"] = row["cpu_quota_cores"]
            row["cpu_quota_cores"] = 6.0
        workers.append(row)
    successor.update({
        "schema_version": "mysql_disk2_four_scene_two_worker_effective_resource_successor_v9",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "predecessor": {"path": str(V8.relative_to(ROOT)), "sha256": sha(V8)},
        "successor_reason": "full_JOB_cold_cache_exceeded_900_seconds_at_4_cores",
        "resource_envelope_unchanged_from_v7": False,
        "resource_envelope_change": {
            "job_memory_cpu_quota_cores": {"from": 4.0, "to": 6.0},
            "all_other_worker_fields_unchanged": True,
            "database_cpuset_unchanged_and_explicit": True,
        },
        "predecessor_r0_failure": {
            "path": str(FAILED_R0.relative_to(ROOT)),
            "sha256": sha(FAILED_R0),
            "accepted_rounds_credit": 0,
        },
        "workers": workers,
        "formal_launch_authorized": False,
        "launch_performed": False,
        "accepted_rounds": {scene: 0 for scene in predecessor["active_scenes"]},
    })
    non_job_old = [row for row in predecessor["workers"] if row["scene"] != "job_memory"]
    non_job_new = [row for row in workers if row["scene"] != "job_memory"]
    if non_job_old != non_job_new:
        raise SystemExit("v9 unexpectedly changed a non-JOB worker")
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
