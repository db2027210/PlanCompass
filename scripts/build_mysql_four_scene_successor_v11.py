#!/usr/bin/env python3
"""Publish v11 with the final XP/CPU bottleneck calibration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V10 = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v10"
    / "four_scene_effective_resource_successor.json"
)
FAILED_R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v10_20260809T221000p0800/r0_binding.json"
)
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v11"
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
    predecessor = load(V10)
    failed = load(FAILED_R0)
    gate = failed.get("target_bottleneck_gate", {})
    observed = gate.get("workers", {})
    if (
        predecessor.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v10"
        or failed.get("status") != "R0_FAILED_CLOSED"
        or failed.get("failures") != []
        or failed.get("client_isolation_failures") != []
        or any((failed.get("accepted_rounds") or {}).values())
        or observed.get("xp_cpu_w1", {}).get("passed") is not False
        or observed.get("xp_cpu_w2", {}).get("passed") is not False
        or any(
            row.get("passed") is not True
            for key, row in observed.items() if key not in {"xp_cpu_w1", "xp_cpu_w2"}
        )
    ):
        raise SystemExit("v11 predecessor authority differs")
    successor = dict(predecessor)
    workers = []
    for worker in predecessor["workers"]:
        row = dict(worker)
        if row["scene"] == "hybench_xp_cpu":
            row["v10_cpu_quota_cores"] = row["cpu_quota_cores"]
            row["cpu_quota_cores"] = 1.7
        workers.append(row)
    successor.update({
        "schema_version": "mysql_disk2_four_scene_two_worker_effective_resource_successor_v11",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "predecessor": {"path": str(V10.relative_to(ROOT)), "sha256": sha(V10)},
        "successor_reason": "XP_CPU_observed_0.777_to_0.780_below_0.8",
        "successor_resource_envelope_change": {
            "hybench_xp_cpu_quota_cores": {"from": 1.8, "to": 1.7},
            "all_other_worker_fields_unchanged": True,
            "native_xp_workload_unchanged": True,
        },
        "predecessor_r0_failure": {
            "path": str(FAILED_R0.relative_to(ROOT)),
            "sha256": sha(FAILED_R0),
            "xp_cpu_observed_fraction": {
                key: observed[key]["observed_fraction"]
                for key in ("xp_cpu_w1", "xp_cpu_w2")
            },
            "all_eight_workload_tasks_passed": True,
            "accepted_rounds_credit": 0,
        },
        "workers": workers,
        "formal_launch_authorized": False,
        "launch_performed": False,
        "accepted_rounds": {scene: 0 for scene in predecessor["active_scenes"]},
    })
    for old, new in zip(predecessor["workers"], workers, strict=True):
        if old["scene"] != "hybench_xp_cpu" and old != new:
            raise SystemExit("v11 unexpectedly changed a non-XP/CPU worker")
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
