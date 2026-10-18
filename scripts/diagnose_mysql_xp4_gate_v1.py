#!/usr/bin/env python3
"""Print a compact rolling-gate diagnosis for one frozen XP4 scene."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v4 as gate
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4"
    / "resource_successor.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scene", choices=("hybench_xp_cpu", "hybench_xp_io"), required=True
    )
    args = parser.parse_args()
    freeze = alg.load(FREEZE)
    target_cpus: set[int] = set()
    for worker in freeze["workers"]:
        if worker["scene"] == args.scene:
            target_cpus.update(alg.r0mod._cpus(str(worker["database_cpuset"])))
            target_cpus.update(alg.r0mod._cpus(str(worker["client_cpuset"])))
    result = gate.rolling_quiet_gate(
        run_id=f"mysql-xp4-gate-diagnostic-{args.scene}",
        required_seconds=15.0,
        maximum_wait_seconds=20.0,
        maximum_load1=16.0,
        target_cpus=target_cpus,
    )
    samples = result.get("samples") or []
    unique_errors = sorted({
        str(error) for row in samples for error in (row.get("errors") or [])
    })
    last = samples[-1] if samples else {}
    isolation = last.get("co_resident_pg_isolation") or {}
    print(json.dumps({
        "scene": args.scene,
        "status": result.get("status"),
        "observed_seconds": result.get("observed_seconds"),
        "terminal_errors": result.get("errors"),
        "sample_count": len(samples),
        "maximum_clean_elapsed_seconds": max(
            (float(row.get("clean_elapsed_seconds") or 0.0) for row in samples),
            default=0.0,
        ),
        "unique_sample_errors": unique_errors,
        "last_dynamic_errors": (
            isolation.get("dynamic_noninterference_successor") or {}
        ).get("dynamic_errors"),
        "last_external_workloads": len(last.get("external_workloads") or []),
        "last_allowed_sibling_mysql": len(
            last.get("allowed_cpu_disjoint_sibling_mysql_workloads") or []
        ),
        "last_persistent_d_state_pids": last.get("persistent_d_state_pids"),
        "last_host_mem_available_bytes": isolation.get("host_mem_available_bytes"),
        "last_host_swap_total_bytes": isolation.get("host_swap_total_bytes"),
        "last_memory_full_psi_avg10": isolation.get("host_memory_full_psi_avg10"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
