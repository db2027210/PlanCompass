#!/usr/bin/env python3
"""Launch fresh Algorithm B after all four last-accepted Algorithm C lanes."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict


PROJECT = Path("/home/anonymous/program/resource_adaptive")
C_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260822_algorithm_c_last_accepted_qps_clean_r67"
)
B_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260822_algorithm_b_last_accepted_qps_clean_r3"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
STATUS = C_ROOT / "algorithm_c_then_b_handoff_status.json"
LAUNCH_B = PROJECT / "shell/launch_pg_algorithm_b_last_accepted_r3.sh"
C_SESSIONS = tuple(
    f"pgc_qpsprev_r67_{name}"
    for name in ("job", "tpch", "xp_cpu", "xp_io", "support")
)
QPS_PROGRESS_CONTRACT = (
    "candidate_qps_strictly_above_previous_accepted_round_v1"
)


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def write_status(payload: Dict[str, Any]) -> None:
    temporary = STATUS.with_name(f".{STATUS.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(STATUS)


def c_complete() -> bool:
    for scene in SCENES:
        state_path = C_ROOT / scene / "algorithm_c_state.json"
        authority_path = C_ROOT / scene / "authority_manifest.json"
        if not state_path.is_file() or not authority_path.is_file():
            return False
        state = read_json(state_path)
        contract = state.get("outer_qps_progress_contract") or {}
        if not (
            state.get("phase") == "complete"
            and int(state.get("accepted") or 0) == 7
            and isinstance(state.get("final_champion"), dict)
            and contract.get("passed") is True
            and contract.get("contract") == QPS_PROGRESS_CONTRACT
        ):
            return False
    return True


def session_exists(name: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def main() -> int:
    started = time.time()
    while not c_complete():
        write_status(
            {
                "phase": "waiting_for_algorithm_c_authorities",
                "algorithm_c_root": str(C_ROOT),
                "algorithm_b_root": str(B_ROOT),
                "algorithm_b_started": False,
                "updated_at_epoch_s": time.time(),
            }
        )
        time.sleep(60.0)
    exit_deadline = time.time() + 1800.0
    while any(session_exists(name) for name in C_SESSIONS):
        if time.time() >= exit_deadline:
            raise TimeoutError("Algorithm C sessions did not exit after completion")
        time.sleep(10.0)
    write_status(
        {
            "phase": "launching_algorithm_b",
            "algorithm_c_root": str(C_ROOT),
            "algorithm_b_root": str(B_ROOT),
            "algorithm_b_started": False,
            "algorithm_c_wait_seconds": time.time() - started,
            "updated_at_epoch_s": time.time(),
        }
    )
    launch = subprocess.run(
        [str(LAUNCH_B)],
        cwd=PROJECT,
        text=True,
        capture_output=True,
        timeout=60.0,
        check=False,
    )
    if launch.returncode != 0:
        raise RuntimeError(
            f"Algorithm B launch failed rc={launch.returncode}: "
            f"{launch.stderr.strip()}"
        )
    write_status(
        {
            "phase": "algorithm_b_launched",
            "algorithm_c_root": str(C_ROOT),
            "algorithm_b_root": str(B_ROOT),
            "algorithm_b_started": True,
            "algorithm_c_wait_seconds": time.time() - started,
            "launcher_stdout": launch.stdout.splitlines(),
            "updated_at_epoch_s": time.time(),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
