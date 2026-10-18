#!/usr/bin/env python3
"""Wait for repaired B/C campaigns and emit a terminal-aware final audit."""

from __future__ import annotations

import json
import subprocess
import sys
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
SCENES: Tuple[str, ...] = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
STATUS_PATH = C_ROOT / "algorithm_bc_qps_basin_escape_final_audit_watch_status.json"
AUDIT_PATH = C_ROOT / "algorithm_bc_qps_basin_escape_final_audit.json"
BASE_AUDIT_PATH = C_ROOT / "algorithm_bc_qps_basin_escape_base_audit.json"
AUDITOR = PROJECT / "shell/audit_pg_algorithm_bc_last_accepted_20260822.py"
QPS_PROGRESS_CONTRACT = "candidate_qps_strictly_above_previous_accepted_round_v1"
QPS_EXHAUSTION_CONTRACT = (
    "cached_valid_replay_qps_stagnation_full_range_plan_escape_v1"
)


def read_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def lane_terminal(root: Path, scene: str, state_name: str) -> Dict[str, Any]:
    state_path = root / scene / state_name
    if not state_path.is_file():
        return {"scene": scene, "terminal": False, "passed": False, "reason": "state_missing"}
    state = read_json(state_path)
    phase = str(state.get("phase") or "")
    accepted = int(state.get("accepted") or 0)
    target = int(state.get("accepted_target") or 7)
    contract = state.get("outer_qps_progress_contract") or {}
    row: Dict[str, Any] = {
        "scene": scene,
        "phase": phase,
        "accepted": accepted,
        "accepted_target": target,
        "technical_failures": int(state.get("technical_failures") or 0),
        "terminal": False,
        "passed": False,
    }
    if not (
        isinstance(contract, dict)
        and contract.get("passed") is True
        and contract.get("contract") == QPS_PROGRESS_CONTRACT
    ):
        row["reason"] = "qps_progress_contract_invalid"
        return row
    if phase == "complete":
        row["terminal"] = True
        row["passed"] = bool(
            accepted == target == 7
            and isinstance(state.get("final_champion"), dict)
            and (root / scene / "authority_manifest.json").is_file()
        )
        row["reason"] = "accepted_target_complete" if row["passed"] else "complete_evidence_missing"
        return row
    if phase == "qps_progress_candidate_space_exhausted_clean":
        exhaustion = state.get("qps_progress_exhaustion") or {}
        receipt = exhaustion.get("clean_restore_receipt") or {}
        row["terminal"] = True
        row["passed"] = bool(
            isinstance(exhaustion, dict)
            and exhaustion.get("contract") == QPS_EXHAUSTION_CONTRACT
            and exhaustion.get("technical_failure") is False
            and exhaustion.get("accepted_unchanged") is True
            and exhaustion.get("incumbent_unchanged") is True
            and exhaustion.get("ranges_unchanged") is True
            and exhaustion.get("gradient_state_unchanged") is True
            and isinstance(receipt, dict)
            and receipt.get("passed") is True
        )
        row["reason"] = "qps_plan_space_exhausted" if row["passed"] else "qps_exhaustion_evidence_invalid"
        return row
    if phase == "candidate_space_exhausted_without_accepted_target_clean":
        exhaustion = state.get("candidate_space_exhaustion") or {}
        row["terminal"] = True
        row["passed"] = bool(
            isinstance(exhaustion, dict)
            and exhaustion.get("technical_failure") is False
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
        row["reason"] = (
            "current_interval_plan_space_exhausted"
            if row["passed"]
            else "candidate_space_exhaustion_evidence_invalid"
        )
        return row
    if phase.endswith("_clean") or phase in {"stopped_by_signal_clean", "nonconvergence_max_rounds_clean"}:
        row["terminal"] = True
        row["reason"] = "unexpected_clean_terminal"
        return row
    row["reason"] = "running"
    return row


def campaign_status(root: Path, state_name: str) -> List[Dict[str, Any]]:
    return [lane_terminal(root, scene, state_name) for scene in SCENES]


def main() -> int:
    started = time.time()
    while True:
        c_lanes = campaign_status(C_ROOT, "algorithm_c_state.json")
        b_lanes = campaign_status(B_ROOT, "algorithm_b_state.json")
        all_terminal = all(row["terminal"] for row in c_lanes + b_lanes)
        unexpected_terminal = any(row["terminal"] and not row["passed"] for row in c_lanes + b_lanes)
        atomic_json(
            STATUS_PATH,
            {
                "phase": "unexpected_terminal" if unexpected_terminal else "waiting_for_clean_terminals",
                "algorithm_c_lanes": c_lanes,
                "algorithm_b_lanes": b_lanes,
                "elapsed_s": time.time() - started,
                "updated_at_epoch_s": time.time(),
            },
        )
        if unexpected_terminal:
            return 2
        if all_terminal:
            break
        time.sleep(60.0)

    audit = subprocess.run(
        [
            sys.executable,
            str(AUDITOR),
            "--allow-incomplete",
            "--output",
            str(BASE_AUDIT_PATH),
            str(C_ROOT),
            str(B_ROOT),
        ],
        cwd=PROJECT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=600.0,
        check=False,
    )
    base_payload = read_json(BASE_AUDIT_PATH) if BASE_AUDIT_PATH.is_file() else {}
    c_lanes = campaign_status(C_ROOT, "algorithm_c_state.json")
    b_lanes = campaign_status(B_ROOT, "algorithm_b_state.json")
    passed = bool(
        audit.returncode == 0
        and base_payload.get("passed") is True
        and all(row["passed"] for row in c_lanes + b_lanes)
    )
    payload = {
        "contract": "pg_algorithm_bc_qps_basin_escape_final_audit_v1",
        "algorithm_c_root": str(C_ROOT),
        "algorithm_b_root": str(B_ROOT),
        "algorithm_c_lanes": c_lanes,
        "algorithm_b_lanes": b_lanes,
        "base_audit_path": str(BASE_AUDIT_PATH),
        "base_audit_passed": base_payload.get("passed") is True,
        "base_audit_returncode": audit.returncode,
        "base_audit_stdout_tail": audit.stdout.splitlines()[-40:],
        "passed": passed,
        "completed_at_epoch_s": time.time(),
    }
    atomic_json(AUDIT_PATH, payload)
    atomic_json(
        STATUS_PATH,
        {
            "phase": "final_audit_passed" if passed else "final_audit_failed",
            "audit_path": str(AUDIT_PATH),
            "elapsed_s": time.time() - started,
            "updated_at_epoch_s": time.time(),
        },
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
