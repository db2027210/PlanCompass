#!/usr/bin/env python3
"""Publish v17 after v16 was stable but missed the XP/I/O bottleneck gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v16/four_scene_effective_resource_successor.json"
PILOT = ROOT / "history/mysql_four_scene_20260809/xp_io_v16_stability_pilot_wave1_20260813T1420p0800/pilot_result.json"
OUTPUT = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v17/four_scene_effective_resource_successor.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"JSON object required: {path}")
    return value


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit(f"refusing to replace frozen successor: {OUTPUT}")
    predecessor, pilot = load(PREDECESSOR), load(PILOT)
    gates = pilot.get("gates") or {}
    if (
        predecessor.get("schema_version") != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v16"
        or pilot.get("status") != "PILOT_FAILED_CLOSED"
        or pilot.get("formal_evidence_eligible") is not False
        or pilot.get("accepted_rounds_credit") != 0
        or set(gates) != {"xp_io_w1", "xp_io_w2"}
        or any(row.get("coverage_complete") is not True for row in gates.values())
        or any(row.get("container_pid_unchanged") is not True for row in gates.values())
        or not all(0 < float(row.get("io_fraction") or 0) < 0.8 for row in gates.values())
    ):
        raise SystemExit("v17 predecessor pilot authority differs")
    successor = dict(predecessor)
    workers = []
    for original in predecessor["workers"]:
        row = dict(original)
        if row["scene"] == "hybench_xp_io":
            if int(row["io_write_bps"]) != 1048576:
                raise SystemExit("v16 XP/I/O cap differs")
            row["v16_io_write_bps"] = int(row["io_write_bps"])
            row["io_write_bps"] = 800000
        workers.append(row)
    successor.update({
        "schema_version": "mysql_disk2_four_scene_two_worker_effective_resource_successor_v17",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "predecessor": {"path": str(PREDECESSOR.relative_to(ROOT)), "sha256": sha(PREDECESSOR)},
        "successor_reason": "v16_1mib_stable_but_both_xp_io_lanes_below_0.8_target_bottleneck",
        "successor_resource_envelope_change": {
            "cpu_memory_swap_process_cpu_numa_and_read_io_fields_unchanged": True,
            "xp_io_write_bps": {"from": 1048576, "to": 800000, "applies_to_workers": ["xp_io_w1", "xp_io_w2"]},
            "native_xp_workload_unchanged": True,
            "job_tpch_and_xp_cpu_envelopes_unchanged": True,
        },
        "predecessor_stability_pilot": {
            "path": str(PILOT.relative_to(ROOT)), "sha256": sha(PILOT),
            "elapsed_seconds": pilot["elapsed_seconds"],
            "io_fractions": {key: gates[key]["io_fraction"] for key in sorted(gates)},
            "coverage_complete": True,
            "container_identity_stable": True,
            "accepted_rounds_credit": 0,
        },
        "version_transition_credit_policy": {
            "job_memory_v15_rounds_may_be_migrated_with_version_provenance": True,
            "tpch_sf4_memory_v15_rounds_may_be_migrated_with_version_provenance": True,
            "hybench_xp_cpu_v15_rounds_may_be_migrated_with_version_provenance": True,
            "hybench_xp_io_pre_v17_rounds_may_be_migrated": False,
            "hybench_xp_io_v17_r0_required": True,
            "pre_and_post_change_xp_io_results_must_never_be_combined": True,
        },
        "workers": workers,
        "formal_launch_authorized": False,
        "launch_performed": False,
        "accepted_rounds": {scene: 0 for scene in predecessor["active_scenes"]},
    })
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    OUTPUT.write_text(json.dumps(successor, ensure_ascii=False, indent=2) + "\n")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
