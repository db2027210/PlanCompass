#!/usr/bin/env python3
"""Capture one-shot live blockers authorizing diagnostic, non-formal replay."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg
from scripts.preflight_mysql_four_scene_live_v8 import host_pressure


FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh output required: {output}")
    inventory = coordination.external_process_inventory("pilot_nonformal", {0, 1})
    pressure = host_pressure()
    blockers = coordination.sanitized_rows(inventory["workloads"])
    if pressure["blocking_d_state_pids"]:
        blockers.append({
            "reason": "host_uninterruptible_sleep_present",
            "d_state_pids": pressure["blocking_d_state_pids"],
        })
    if float(pressure["load1"]) > 16.0:
        blockers.append({
            "reason": "host_idle_load1_exceeds_16",
            "load1": pressure["load1"],
        })
    if not blockers:
        raise RuntimeError("live interference blocker required for override pilot")
    document = {
        "schema_version": "mysql_interference_override_pilot_evidence_v1",
        "status": "BLOCKED_FAIL_CLOSED",
        "captured_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_launch_authorized": False,
        "pilot_launch_authorized": True,
        "formal_evidence_eligible": False,
        "accepted_state_credit_prohibited": True,
        "external_process_mutation": False,
        "docker_mutation": False,
        "freeze_path": str(FREEZE),
        "freeze_sha256": alg.sha(FREEZE),
        "external_controllers": coordination.sanitized_rows(inventory["controllers"]),
        "blockers": blockers,
        "host_pressure": pressure,
    }
    alg.atomic(output, document)
    print(json.dumps({
        "status": document["status"], "output": str(output),
        "blocker_count": len(blockers),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
