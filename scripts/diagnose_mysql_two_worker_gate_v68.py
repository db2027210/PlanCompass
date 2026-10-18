#!/usr/bin/env python3
"""Print compact v68 non-interference blockers for one current scene."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v12 as gate
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg

FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19/"
    "four_scene_effective_resource_successor.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", required=True)
    args = parser.parse_args()
    freeze = alg.load(FREEZE)
    target: set[int] = set()
    for worker in freeze["workers"]:
        if worker["scene"] == args.scene:
            target.update(alg.r0mod._cpus(str(worker["database_cpuset"])))
            target.update(alg.r0mod._cpus(str(worker["client_cpuset"])))
    result = gate.rolling_quiet_gate(
        run_id=f"mysql-v68-diagnostic-{args.scene}", required_seconds=15.0,
        maximum_wait_seconds=20.0, maximum_load1=16.0, target_cpus=target,
    )
    samples = result.get("samples") or []
    last = samples[-1] if samples else {}
    print(json.dumps({
        "scene": args.scene,
        "status": result.get("status"),
        "observed_seconds": result.get("observed_seconds"),
        "terminal_errors": result.get("errors"),
        "unique_sample_errors": sorted({
            str(error) for row in samples for error in (row.get("errors") or [])
        }),
        "last_external_workloads": last.get("external_workloads"),
        "last_allowed_sibling_mysql": last.get(
            "allowed_cpu_disjoint_sibling_mysql_workloads"
        ),
        "last_persistent_d_state_pids": last.get("persistent_d_state_pids"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
