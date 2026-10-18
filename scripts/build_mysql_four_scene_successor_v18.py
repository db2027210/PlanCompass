#!/usr/bin/env python3
"""Publish v18 with XP/I/O headroom for repeatable bottleneck classification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v17/four_scene_effective_resource_successor.json"
WAVE1 = ROOT / "history/mysql_four_scene_20260809/xp_io_v17_stability_pilot_wave1_20260813T1450p0800/pilot_result.json"
WAVE2 = ROOT / "history/mysql_four_scene_20260809/xp_io_v17_stability_pilot_wave2_20260813T1506p0800/pilot_result.json"
OUTPUT = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"


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
    predecessor, wave1, wave2 = load(PREDECESSOR), load(WAVE1), load(WAVE2)
    gates1, gates2 = wave1.get("gates") or {}, wave2.get("gates") or {}
    lane_ids = {"xp_io_w1", "xp_io_w2"}
    if (
        predecessor.get("schema_version") != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v17"
        or wave1.get("status") != "PASS"
        or wave2.get("status") != "PILOT_FAILED_CLOSED"
        or any(wave.get("formal_evidence_eligible") is not False for wave in (wave1, wave2))
        or any(wave.get("accepted_rounds_credit") != 0 for wave in (wave1, wave2))
        or set(gates1) != lane_ids
        or set(gates2) != lane_ids
        or any(row.get("coverage_complete") is not True for row in [*gates1.values(), *gates2.values()])
        or any(row.get("container_pid_unchanged") is not True for row in [*gates1.values(), *gates2.values()])
        or any(row.get("running_after") is not True for row in [*gates1.values(), *gates2.values()])
        or wave1.get("failures")
        or wave2.get("failures")
        or not all(float(row.get("io_fraction") or 0) >= 0.8 for row in gates1.values())
        or sum(float(row.get("io_fraction") or 0) < 0.8 for row in gates2.values()) != 1
        or min(float(row.get("io_fraction") or 0) for row in gates2.values()) < 0.78
    ):
        raise SystemExit("v18 predecessor pilot authority differs")
    workers = []
    for original in predecessor["workers"]:
        row = dict(original)
        if row["scene"] == "hybench_xp_io":
            if int(row["io_write_bps"]) != 800000:
                raise SystemExit("v17 XP/I/O cap differs")
            row["v17_io_write_bps"] = int(row["io_write_bps"])
            row["io_write_bps"] = 750000
        workers.append(row)
    successor = dict(predecessor)
    successor.update({
        "schema_version": "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18",
        "status": "FROZEN_RUNTIME_READBACK_PENDING",
        "predecessor": {"path": str(PREDECESSOR.relative_to(ROOT)), "sha256": sha(PREDECESSOR)},
        "successor_reason": "v17_stable_but_one_of_four_lane_waves_missed_0.8_io_fraction_by_0.0098",
        "successor_resource_envelope_change": {
            "cpu_memory_swap_process_cpu_numa_and_read_io_fields_unchanged": True,
            "xp_io_write_bps": {"from": 800000, "to": 750000, "applies_to_workers": sorted(lane_ids)},
            "native_xp_workload_unchanged": True,
            "job_tpch_and_xp_cpu_envelopes_unchanged": True,
        },
        "predecessor_stability_pilots": [
            {
                "path": str(path.relative_to(ROOT)), "sha256": sha(path),
                "status": wave["status"], "elapsed_seconds": wave["elapsed_seconds"],
                "io_fractions": {key: gates[key]["io_fraction"] for key in sorted(gates)},
                "coverage_complete": True, "container_identity_stable": True,
                "accepted_rounds_credit": 0,
            }
            for path, wave, gates in ((WAVE1, wave1, gates1), (WAVE2, wave2, gates2))
        ],
        "version_transition_credit_policy": {
            "job_memory_v15_rounds_may_be_migrated_with_version_provenance": True,
            "tpch_sf4_memory_v15_rounds_may_be_migrated_with_version_provenance": True,
            "hybench_xp_cpu_v15_rounds_may_be_migrated_with_version_provenance": True,
            "hybench_xp_io_pre_v18_rounds_may_be_migrated": False,
            "hybench_xp_io_v18_r0_required": True,
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
    print(json.dumps({"path": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
