#!/usr/bin/env python3
"""Launch CPU-B continuation that replays only failed calibration rank 13."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_xp_top4_algorithm_b_scene_v2 as base

HISTORY = base.HISTORY
SOURCE = HISTORY / "v234_to_b_top4_fresh_20260826_attempt021_xp_cpu"
AUTHORITY = HISTORY / "v233_to_b_top4_live_authority_20260826/hybench_xp_cpu/live_authority.json"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v16.py"
CONTRACT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v25/algorithm_b_top4_contract.json"
RUN_ID = "v237_to_b_top4_fresh_20260826_attempt022_xp_cpu"
OUTPUT = HISTORY / RUN_ID
RESULT = HISTORY / "v237_to_b_top4_pipeline_20260826/xp_cpu.launch.json"
UNIT = "mysql-algb-top4-v237-xp_cpu.service"


def main() -> int:
    live = base.load(AUTHORITY)
    source = base.load(SOURCE / "run_state.json")
    if not (
        live.get("status") == "PASS"
        and live.get("scene") == "hybench_xp_cpu"
        and live.get("formal_scope") == "mysql_only"
        and source.get("status") == "FAILED_CLOSED"
        and source.get("error")
        == "RuntimeError: Algorithm B calibration wave 13 failed closed"
    ):
        raise RuntimeError("CPU B rank-13 continuation authority differs")
    if OUTPUT.exists() or OUTPUT.is_symlink() or RESULT.exists():
        raise RuntimeError("fresh CPU B rank-13 continuation output required")
    stdout = base.LOG_ROOT / f"{RUN_ID}.stdout.log"
    stderr = base.LOG_ROOT / f"{RUN_ID}.stderr.log"
    command = [
        "systemd-run", "--user", f"--unit={UNIT.removesuffix('.service')}",
        "--description=MySQL CPU Top4 B rank13-only continuation",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", base.MANAGEMENT_CPUSET,
        "/usr/bin/env", f"MYSQL_XP4_AUTHORITY_ROOT={AUTHORITY.parent / 'authority'}",
        str(base.PYTHON), str(CONTROLLER), "--run-id", RUN_ID,
        "--resume-from", str(SOURCE), "--scene", "hybench_xp_cpu",
    ]
    launched = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
    )
    if launched.returncode != 0:
        raise RuntimeError("CPU B rank-13 continuation launch failed: " + launched.stderr[-2000:])
    state_path = OUTPUT / "run_state.json"
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        if state_path.is_file():
            state = base.load(state_path)
            if state.get("status") == "FAILED_CLOSED":
                raise RuntimeError(f"CPU B rank-13 continuation exited: {state.get('error')}")
            if (
                state.get("algorithm") == "B"
                and state.get("async_scene") == "hybench_xp_cpu"
                and state.get("status") == "RUNNING"
                and state.get("four_scene_wave_barrier") is False
                and state.get("other_scene_completion_required") is False
            ):
                result = {
                    "schema_version": "mysql_algorithm_b_cpu_rank13_continuation_launch_v1",
                    "status": "LAUNCHED", "launched_at": base.now(),
                    "scene": "hybench_xp_cpu", "formal_scope": "mysql_only",
                    "cross_engine_comparison_prohibited": True,
                    "source_failure_path": str(SOURCE / "run_state.json"),
                    "source_failure_sha256": base.sha(SOURCE / "run_state.json"),
                    "completed_calibration_points_reused": list(range(1, 13)) + [14, 15],
                    "failed_calibration_points_replayed": [13],
                    "candidate_measurement_replayed": True,
                    "candidate_measurement_replay_scope": "failed_rank_13_only",
                    "accepted_or_rejected_outer_credit_reused": False,
                    "authority_reused_path": str(AUTHORITY),
                    "authority_reused_sha256": base.sha(AUTHORITY),
                    "run_id": RUN_ID, "unit": UNIT, "output": str(OUTPUT),
                    "state": str(state_path), "stdout": str(stdout), "stderr": str(stderr),
                    "contract_path": str(CONTRACT), "contract_sha256": base.sha(CONTRACT),
                }
                base.atomic(RESULT, result)
                print(json.dumps(result, sort_keys=True))
                return 0
        time.sleep(1.0)
    raise TimeoutError("CPU B rank-13 continuation startup evidence timed out")


if __name__ == "__main__":
    raise SystemExit(main())
