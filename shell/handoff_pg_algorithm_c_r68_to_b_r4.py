#!/usr/bin/env python3
"""Launch fresh Algorithm B after every repaired Algorithm C lane terminates."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROJECT = Path("/home/anonymous/program/resource_adaptive")
C_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260822_algorithm_c_last_accepted_qps_basin_escape_clean_r68"
)
B_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260822_algorithm_b_last_accepted_qps_basin_escape_clean_r4"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
STATUS = C_ROOT / "algorithm_c_then_b_handoff_status.json"
LAUNCH_B = PROJECT / "shell/launch_pg_algorithm_b_qps_basin_escape_r4.sh"
C_SESSIONS = tuple(
    f"pgc_qpsbasin_r68_{name}"
    for name in ("job", "tpch", "xp_cpu", "xp_io", "support")
)
QPS_PROGRESS_CONTRACT = (
    "candidate_qps_strictly_above_previous_accepted_round_v1"
)
QPS_EXHAUSTION_CONTRACT = (
    "cached_valid_replay_qps_stagnation_full_range_plan_escape_v1"
)
CLEAN_TERMINAL_PHASES = {
    "complete",
    "qps_progress_candidate_space_exhausted_clean",
    "candidate_space_exhausted_without_accepted_target_clean",
}


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


def lane_terminal(scene: str) -> Tuple[bool, Dict[str, Any]]:
    state_path = C_ROOT / scene / "algorithm_c_state.json"
    if not state_path.is_file():
        return False, {"scene": scene, "reason": "state_missing"}
    state = read_json(state_path)
    phase = str(state.get("phase") or "")
    contract = state.get("outer_qps_progress_contract") or {}
    detail: Dict[str, Any] = {
        "scene": scene,
        "phase": phase,
        "accepted": int(state.get("accepted") or 0),
        "accepted_target": int(state.get("accepted_target") or 7),
        "technical_failures": int(state.get("technical_failures") or 0),
    }
    if not (
        contract.get("passed") is True
        and contract.get("contract") == QPS_PROGRESS_CONTRACT
    ):
        detail["reason"] = "qps_progress_contract_invalid"
        return False, detail
    if phase not in CLEAN_TERMINAL_PHASES:
        detail["reason"] = "not_terminal"
        return False, detail
    if phase == "complete":
        authority_path = C_ROOT / scene / "authority_manifest.json"
        passed = bool(
            int(state.get("accepted") or 0) == 7
            and isinstance(state.get("final_champion"), dict)
            and authority_path.is_file()
        )
        detail["reason"] = "complete" if passed else "complete_evidence_missing"
        return passed, detail
    if phase == "qps_progress_candidate_space_exhausted_clean":
        exhaustion = state.get("qps_progress_exhaustion") or {}
        receipt = exhaustion.get("clean_restore_receipt") or {}
        passed = bool(
            exhaustion.get("contract") == QPS_EXHAUSTION_CONTRACT
            and exhaustion.get("technical_failure") is False
            and exhaustion.get("accepted_unchanged") is True
            and exhaustion.get("incumbent_unchanged") is True
            and exhaustion.get("ranges_unchanged") is True
            and exhaustion.get("gradient_state_unchanged") is True
            and receipt.get("passed") is True
        )
        detail["reason"] = (
            "qps_plan_space_exhausted"
            if passed
            else "qps_exhaustion_evidence_invalid"
        )
        return passed, detail
    if phase == "candidate_space_exhausted_without_accepted_target_clean":
        exhaustion = state.get("candidate_space_exhaustion") or {}
        passed = bool(
            exhaustion.get("technical_failure") is False
            and exhaustion.get("accepted_unchanged") is True
            and exhaustion.get("incumbent_unchanged") is True
            and exhaustion.get("ranges_unchanged") is True
            and exhaustion.get("gradient_state_unchanged") is True
            and exhaustion.get("exhaustive_offline_search_completed") is True
            and exhaustion.get("ordinary_gradient_search_completed") is True
            and exhaustion.get("larger_eta_search_completed") is True
            and exhaustion.get("current_range_boundary_search_completed") is True
            and exhaustion.get("baseline_padding_allowed") is False
            and exhaustion.get("duplicate_plan_padding_allowed") is False
            and exhaustion.get("workers_already_restored_by_empty_replay_batch")
            is True
            and int(state.get("restore_technical_failures") or 0) == 0
        )
        detail["reason"] = (
            "current_interval_plan_space_exhausted"
            if passed
            else "candidate_space_exhaustion_evidence_invalid"
        )
        return passed, detail
    detail["reason"] = "unsupported_terminal"
    return False, detail


def c_terminal() -> Tuple[bool, List[Dict[str, Any]]]:
    details: List[Dict[str, Any]] = []
    passed = True
    for scene in SCENES:
        lane_passed, detail = lane_terminal(scene)
        passed = passed and lane_passed
        details.append(detail)
    return passed, details


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
    while True:
        terminal, details = c_terminal()
        if terminal:
            break
        write_status(
            {
                "phase": "waiting_for_algorithm_c_clean_terminals",
                "algorithm_c_root": str(C_ROOT),
                "algorithm_b_root": str(B_ROOT),
                "algorithm_b_started": False,
                "lanes": details,
                "updated_at_epoch_s": time.time(),
            }
        )
        time.sleep(60.0)
    exit_deadline = time.time() + 1800.0
    while any(session_exists(name) for name in C_SESSIONS):
        if time.time() >= exit_deadline:
            raise TimeoutError("Algorithm C sessions did not exit after terminal")
        time.sleep(10.0)
    write_status(
        {
            "phase": "launching_algorithm_b",
            "algorithm_c_root": str(C_ROOT),
            "algorithm_b_root": str(B_ROOT),
            "algorithm_b_started": False,
            "algorithm_c_wait_seconds": time.time() - started,
            "lanes": details,
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
            "lanes": details,
            "launcher_stdout": launch.stdout.splitlines(),
            "updated_at_epoch_s": time.time(),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
