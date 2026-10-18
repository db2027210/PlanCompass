#!/usr/bin/env python3
"""Publish the v6 envelope after the native-IQ1-IQ6 R0 calibration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v5"
    / "four_scene_effective_resource_successor.json"
)
FAILED_R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq6_r0_v5_20260809T174100p0800/r0_binding.json"
)
XP_WORKLOAD_FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_native_xp_iq1_iq6_20260809_v1"
    / "workload_successor.json"
)
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v6"
    / "four_scene_effective_resource_successor.json"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit(f"refusing to replace frozen successor: {OUTPUT}")
    predecessor = json.loads(PREDECESSOR.read_text(encoding="utf-8"))
    failed = json.loads(FAILED_R0.read_text(encoding="utf-8"))
    if (
        predecessor.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v5"
        or failed.get("status") != "R0_FAILED_CLOSED"
        or failed.get("fresh_r0_complete") is not False
        or failed.get("formal_outer_launch_authorized") is not False
        or any((failed.get("accepted_rounds") or {}).values())
    ):
        raise SystemExit("v6 predecessor authority differs")

    successor = dict(predecessor)
    successor.update({
        "schema_version": "mysql_disk2_four_scene_two_worker_effective_resource_successor_v6",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "predecessor": {
            "path": str(PREDECESSOR.relative_to(ROOT)),
            "sha256": sha256(PREDECESSOR),
        },
        "successor_reason": (
            "fresh_native_iq1_iq6_r0_requires_job_memory_and_xp_cpu_"
            "bottleneck_calibration_plus_two_round_acceptance_downgrade"
        ),
        "predecessor_r0_failure": {
            "path": str(FAILED_R0.relative_to(ROOT)),
            "sha256": sha256(FAILED_R0),
            "wall_time_seconds": failed["wall_time_seconds"],
            "failure_causes": [
                "job_w1_and_job_w2_memory_fraction_below_0.8",
                "xp_cpu_w1_and_xp_cpu_w2_cpu_fraction_below_0.8",
                "job_tpch_pair_1_wall_time_exceeded_900_seconds",
            ],
            "workload_coverage_complete": True,
            "client_isolation_failures": 0,
            "accepted_rounds_credit": 0,
        },
        "native_xp_contract": {
            "ap_selection": "IQ1-IQ6_uniform_random",
            "at1_at6_percent": "35/25/15/15/7/3",
            "xap_xtp_ratio": "1:1",
            "cpu_and_io_workload_identical": True,
            "workload_mix_override_prohibited": True,
            "workload_freeze": {
                "path": str(XP_WORKLOAD_FREEZE.relative_to(ROOT)),
                "sha256": sha256(XP_WORKLOAD_FREEZE),
            },
        },
        "algorithm_c_acceptance": {
            **dict(predecessor["algorithm_c_acceptance"]),
            "downgrade_after_consecutive_failed_outer_rounds": 2,
        },
        "formal_launch_authorized": False,
        "launch_performed": False,
        "accepted_rounds": {
            scene: 0 for scene in predecessor["active_scenes"]
        },
    })
    workers = []
    for original in predecessor["workers"]:
        worker = dict(original)
        if worker["scene"] == "job_memory":
            worker["cpu_quota_cores"] = 3.5
            worker["memory_bytes"] = 6174015488
            worker["v5_cpu_quota_cores"] = original["cpu_quota_cores"]
            worker["v5_memory_bytes"] = original["memory_bytes"]
        elif worker["scene"] == "hybench_xp_cpu":
            worker["cpu_quota_cores"] = 2.3
            worker["v5_cpu_quota_cores"] = original["cpu_quota_cores"]
        workers.append(worker)
    successor["workers"] = workers

    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(
        json.dumps(successor, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    checksum = OUTPUT.with_name(OUTPUT.name + ".sha256")
    checksum.write_text(f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
