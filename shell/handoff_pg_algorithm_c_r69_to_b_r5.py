#!/usr/bin/env python3
"""Launch fresh Algorithm B after all monotonic-QPS Algorithm C lanes finish."""

from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple


PROJECT = Path("/home/anonymous/program/resource_adaptive")
C_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260822_algorithm_c_monotonic_qps_top10_current_interval_clean_r74"
)
B_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260822_algorithm_b_monotonic_qps_top10_current_interval_clean_r10"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
STATUS = C_ROOT / "algorithm_c_then_b_handoff_status.json"
LAUNCHER = PROJECT / "shell/launch_pg_algorithm_bc_monotonic_top10_20260822.sh"
QPS_CONTRACT = "candidate_qps_strictly_above_previous_accepted_round_v1"
CONVERGENCE_CONTRACT = (
    "accepted_target_or_two_consecutive_budgeted_no_improvement_v3"
)
C_SESSIONS = tuple(
    f"pgc_monotop10_r74_{lane}"
    for lane in ("job", "tpch", "xp_cpu", "xp_io", "support")
)


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def monotonic_archive(state: Mapping[str, Any]) -> Tuple[bool, List[float]]:
    archive = state.get("accepted_candidate_archive")
    archive = archive if isinstance(archive, list) else []
    qps_values: List[float] = []
    previous = None
    for row in archive:
        normalized = row.get("normalized") if isinstance(row, dict) else None
        decision = row.get("decision") if isinstance(row, dict) else None
        if not isinstance(normalized, dict) or not isinstance(decision, dict):
            return False, qps_values
        qps = float(normalized.get("db_qps") or math.nan)
        reference = float(decision.get("qps_gate_reference_db_qps") or math.nan)
        if not math.isfinite(qps) or not math.isfinite(reference):
            return False, qps_values
        if decision.get("accepted") is not True or decision.get("qps_up") is not True:
            return False, qps_values
        if qps <= reference or (previous is not None and qps <= previous):
            return False, qps_values
        qps_values.append(qps)
        previous = qps
    return len(qps_values) == int(state.get("accepted") or 0), qps_values


def lane_terminal(scene: str) -> Tuple[bool, Dict[str, Any]]:
    state_path = C_ROOT / scene / "algorithm_c_state.json"
    if not state_path.is_file():
        return False, {"scene": scene, "reason": "state_missing"}
    state = read_json(state_path)
    phase = str(state.get("phase") or "")
    accepted = int(state.get("accepted") or 0)
    contract = state.get("outer_qps_progress_contract")
    contract = contract if isinstance(contract, dict) else {}
    monotonic, qps_values = monotonic_archive(state)
    detail: Dict[str, Any] = {
        "scene": scene,
        "phase": phase,
        "accepted_improvements": accepted,
        "outer_rounds": int(state.get("round_index") or 0),
        "technical_failures": int(state.get("technical_failures") or 0),
        "accepted_qps": qps_values,
        "accepted_qps_strictly_monotonic": monotonic,
    }
    if not (
        contract.get("passed") is True
        and contract.get("contract") == QPS_CONTRACT
        and monotonic
    ):
        detail["reason"] = "qps_contract_or_monotonic_archive_invalid"
        return False, detail
    authority = C_ROOT / scene / "authority_manifest.json"
    if phase == "complete":
        passed = bool(accepted == 7 and authority.is_file())
        detail["reason"] = "accepted_target_complete" if passed else "complete_evidence_missing"
        return passed, detail
    if phase == "converged_on_incumbent_clean":
        convergence = state.get("incumbent_convergence")
        convergence = convergence if isinstance(convergence, dict) else {}
        receipt = convergence.get("clean_restore_receipt")
        receipt = receipt if isinstance(receipt, dict) else {}
        passed = bool(
            state.get("completion_contract") == CONVERGENCE_CONTRACT
            and convergence.get("contract") == CONVERGENCE_CONTRACT
            and convergence.get("counts_as_accepted_improvement") is False
            and convergence.get(
                "accepted_counter_unchanged_by_baseline_selection"
            )
            is True
            and int(convergence.get("consecutive_no_improvement_rounds") or 0)
            >= 2
            and isinstance(state.get("final_champion"), dict)
            and receipt.get("passed") is True
            and authority.is_file()
        )
        detail["reason"] = (
            "two_round_incumbent_convergence"
            if passed
            else "incumbent_convergence_evidence_missing"
        )
        return passed, detail
    detail["reason"] = "not_clean_terminal"
    return False, detail


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
        details: List[Dict[str, Any]] = []
        all_terminal = True
        for scene in SCENES:
            terminal, detail = lane_terminal(scene)
            all_terminal = all_terminal and terminal
            details.append(detail)
        atomic_json(
            STATUS,
            {
                "phase": (
                    "waiting_for_algorithm_c_clean_terminals"
                    if not all_terminal
                    else "waiting_for_algorithm_c_sessions_to_exit"
                ),
                "algorithm_c_root": str(C_ROOT),
                "algorithm_b_root": str(B_ROOT),
                "algorithm_b_started": False,
                "lanes": details,
                "updated_at_epoch_s": time.time(),
            },
        )
        if all_terminal:
            break
        time.sleep(30.0)
    deadline = time.time() + 1800.0
    while any(session_exists(name) for name in C_SESSIONS):
        if time.time() >= deadline:
            raise TimeoutError("Algorithm C sessions did not exit after terminal")
        time.sleep(10.0)
    launch = subprocess.run(
        [str(LAUNCHER), "B"],
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
    atomic_json(
        STATUS,
        {
            "phase": "algorithm_b_launched",
            "algorithm_c_root": str(C_ROOT),
            "algorithm_b_root": str(B_ROOT),
            "algorithm_b_started": True,
            "algorithm_c_elapsed_s": time.time() - started,
            "lanes": details,
            "launcher_stdout": launch.stdout.splitlines(),
            "updated_at_epoch_s": time.time(),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
