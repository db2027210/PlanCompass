#!/usr/bin/env python3
"""After fresh B terminates, prepare fresh authority and launch fresh C."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination
from scripts import wait_and_run_mysql_four_scene_r0_v18 as pipeline

B_RUN_ID = "v34_formal_pg_outer_adaptive_20260819_attempt033"
B_UNIT = f"mysql-four-scene-algb-{B_RUN_ID}.service"
B_AFFINITY_UNIT = f"mysql-four-scene-algb-affinity-{B_RUN_ID}.service"
B_STATE = ROOT / "history/mysql_four_scene_20260809" / B_RUN_ID / "run_state.json"
FREEZE_ROOT = ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v34"
COORDINATION_CONTRACT = FREEZE_ROOT / "coordination_contract.json"
HANDOFF_CONTRACT = FREEZE_ROOT / "serial_b_then_c_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_contract() -> dict:
    contract = json.loads(HANDOFF_CONTRACT.read_text(encoding="utf-8"))
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm_b_run_id") != B_RUN_ID
        or contract.get("algorithm_c_fresh_search") is not True
        or contract.get("algorithm_b_failed_closed_handoff_prohibited") is not True
        or contract.get("concurrent_b_and_c_controllers_prohibited") is not True
    ):
        raise RuntimeError("v34 serial B-then-C contract differs")
    for name, binding in (contract.get("source_bindings") or {}).items():
        path = ROOT / str(binding["path"])
        if not path.is_file() or path.is_symlink() or sha(path) != binding["sha256"]:
            raise RuntimeError(f"v34 serial handoff source binding differs: {name}")
    return contract


def unit_active_state(unit: str) -> str:
    completed = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState", "--value"],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=15,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-run-id", required=True)
    parser.add_argument("--maximum-wait-seconds", type=float, default=14 * 86400)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()
    validate_contract()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,140}", args.handoff_run_id) is None:
        parser.error("unsafe handoff run id")
    if args.poll_seconds < 5 or args.maximum_wait_seconds <= 0:
        parser.error("invalid wait bounds")

    started = time.monotonic()
    while time.monotonic() - started < args.maximum_wait_seconds:
        if unit_active_state(B_UNIT) in {"inactive", "failed"}:
            break
        time.sleep(args.poll_seconds)
    else:
        raise RuntimeError("fresh Algorithm B did not reach a terminal service state")

    state = json.loads(B_STATE.read_text(encoding="utf-8"))
    if state.get("status") not in {"COMPLETE", "NON_CONVERGED"}:
        raise RuntimeError(
            "fresh Algorithm B did not complete its search; FAILED_CLOSED cannot launch C"
        )
    subprocess.run(
        ["systemctl", "--user", "stop", B_AFFINITY_UNIT],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )

    coordination.CONTRACT_PATH = COORDINATION_CONTRACT
    pipeline.PREFLIGHT_SCRIPT = (
        ROOT / "scripts/preflight_mysql_four_scene_live_pg_outer_v34.py"
    )
    pipeline.R0_SCRIPT = ROOT / "scripts/run_mysql_four_scene_r0_pg_outer_v34.py"
    pipeline.ALGORITHM_LAUNCHER = (
        ROOT / "scripts/launch_mysql_algorithm_c_pg_outer_fresh_v45.py"
    )
    pipeline.CHECKPOINT = (
        ROOT / "history/mysql_four_scene_20260809"
        / "algorithm_c_v79_outer17_checkpoint_v20_20260817T1425p0800"
        / "prepared_binding.json"
    )
    pipeline.REPLAY_QUARANTINE = (
        ROOT / "history/mysql_four_scene_20260809"
        / "algorithm_c_v79_replay_quarantine_v15_outer17_20260817T1427p0800"
        / "replay_quarantine.json"
    )
    pipeline.ALGORITHM_FRESH_SEARCH = True
    sys.argv = [
        str(Path(__file__).resolve()),
        "--run-id", args.handoff_run_id,
        "--poll-seconds", str(max(10, int(args.poll_seconds))),
        "--max-gate-attempts", "20",
        "--lease-timeout-seconds", "10",
    ]
    return pipeline.main()


if __name__ == "__main__":
    raise SystemExit(main())
