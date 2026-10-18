#!/usr/bin/env python3
"""Wait for all asynchronous B scenes, then prepare and launch asynchronous C."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination
from scripts import wait_and_run_mysql_four_scene_r0_v18 as pipeline


B_CAMPAIGN_ID = "v36_formal_async_outer_20260819_attempt044"
HISTORY = ROOT / "history/mysql_four_scene_20260809"
B_MANIFEST = (
    HISTORY / "formal_launch_logs"
    / f"{B_CAMPAIGN_ID}.async_b.launch_manifest.json"
)
FREEZE_ROOT = ROOT / "config/formal/frozen_mysql_async_outer_20260819_v36"
HANDOFF_CONTRACT = FREEZE_ROOT / "serial_async_b_then_c_contract.json"
COORDINATION_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260819_v64"
    / "coordination_contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    contract = load(HANDOFF_CONTRACT)
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm_b_campaign_id") != B_CAMPAIGN_ID
        or contract.get("algorithm_c_fresh_search") is not True
        or contract.get("all_b_scenes_must_legally_terminate") is not True
        or contract.get("concurrent_b_and_c_controllers_prohibited") is not True
        or contract.get("recalibration_required") is not False
    ):
        raise RuntimeError("asynchronous serial B-then-C contract differs")
    for name, binding in (contract.get("source_bindings") or {}).items():
        path = ROOT / str(binding["path"])
        if (
            not path.is_file() or path.is_symlink()
            or sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"serial asynchronous source binding differs: {name}")
    return contract


def unit_state(unit: str) -> str:
    try:
        completed = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "ActiveState", "--value"],
            cwd=ROOT, text=True, capture_output=True, check=False, timeout=15,
        )
    except subprocess.TimeoutExpired:
        # A transient user-systemd D-Bus stall must not destroy a handoff that
        # may wait for Algorithm B for hours.  Treat it as an inconclusive poll
        # and retry on the next cycle.
        return "unknown"
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-run-id", required=True)
    parser.add_argument("--maximum-wait-seconds", type=float, default=14 * 86400)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()
    validate_contract()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,90}", args.handoff_run_id) is None:
        parser.error("unsafe handoff run id")
    if args.poll_seconds < 5 or args.maximum_wait_seconds <= 0:
        parser.error("invalid wait bounds")
    manifest = load(B_MANIFEST)
    if (
        manifest.get("status") != "LAUNCHED"
        or manifest.get("campaign_id") != B_CAMPAIGN_ID
        or manifest.get("four_scene_wave_barrier") is not False
        or manifest.get("recalibration_performed") is not False
        or set(manifest.get("scenes") or {}) != {
            "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
        }
    ):
        raise RuntimeError("Algorithm B asynchronous launch manifest differs")

    started = time.monotonic()
    while time.monotonic() - started < args.maximum_wait_seconds:
        active = {}
        for scene, row in manifest["scenes"].items():
            state = unit_state(str(row["unit"]))
            active[scene] = state
            if state in {"inactive", "failed"}:
                run_state = load(Path(str(row["run_state"])))
                if run_state.get("status") not in {"COMPLETE", "NON_CONVERGED"}:
                    raise RuntimeError(
                        f"Algorithm B scene {scene} terminated without legal completion"
                    )
        if all(value in {"inactive", "failed"} for value in active.values()):
            break
        time.sleep(args.poll_seconds)
    else:
        raise RuntimeError("asynchronous Algorithm B did not terminate within bound")

    coordination.CONTRACT_PATH = COORDINATION_CONTRACT
    pipeline.PREFLIGHT_SCRIPT = (
        ROOT / "scripts/preflight_mysql_four_scene_live_pg_outer_v35.py"
    )
    pipeline.R0_SCRIPT = ROOT / "scripts/run_mysql_four_scene_r0_pg_outer_v35.py"
    pipeline.ALGORITHM_LAUNCHER = (
        ROOT / "scripts/launch_mysql_algorithm_c_async_v48.py"
    )
    pipeline.CHECKPOINT = (
        HISTORY / "algorithm_c_v79_outer17_checkpoint_v20_20260817T1425p0800"
        / "prepared_binding.json"
    )
    pipeline.REPLAY_QUARANTINE = (
        HISTORY / "algorithm_c_v79_replay_quarantine_v15_outer17_20260817T1427p0800"
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
