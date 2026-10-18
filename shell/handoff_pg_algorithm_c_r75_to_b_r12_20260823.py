#!/usr/bin/env python3
"""Audit Algorithm C r75 and launch fresh Algorithm B r12 exactly once."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple


PROJECT = Path("/home/anonymous/program/resource_adaptive")
C_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260823_algorithm_c_training_boundary_clean_r75"
)
B_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260823_algorithm_b_training_boundary_clean_r12"
)
SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
STATUS = C_ROOT / "cohort_support" / "handoff_r75_to_b_r12_status.json"
LAUNCH_B = PROJECT / "shell/launch_pg_algorithm_b_training_boundary_r12_20260823.sh"
C_SESSIONS = tuple(
    f"pgc_trainbound_r75_{name}"
    for name in ("job", "tpch", "xp_cpu", "xp_io", "support")
)
B_SESSIONS = tuple(
    f"pgb_trainbound_r12_{name}"
    for name in ("job", "tpch", "xp_cpu", "xp_io", "support")
)
QPS_PROGRESS_CONTRACT = (
    "candidate_qps_strictly_above_previous_accepted_round_v1"
)
TERMINAL_PHASES = {"complete", "converged_on_incumbent_clean"}
VALID_TIERS = {"strict", "secondary", "fallback"}


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_status(payload: Mapping[str, Any]) -> None:
    temporary = STATUS.with_name(f".{STATUS.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, STATUS)


def session_exists(name: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def accepted_events(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"invalid JSONL {path}:{line_number}: {exc}"
                ) from exc
            if row.get("event") == "algorithm_c_round_accepted":
                rows.append(row)
    return rows


def audit_accepted_chain(
    state: Mapping[str, Any],
    history_path: Path,
) -> Tuple[bool, List[str], List[Dict[str, Any]]]:
    errors: List[str] = []
    audit_rows: List[Dict[str, Any]] = []
    rows = accepted_events(history_path)
    state_accepted = int(state.get("accepted") or 0)
    if len(rows) != state_accepted:
        errors.append(
            f"accepted_event_count={len(rows)} != state.accepted={state_accepted}"
        )
    previous_qps: float | None = None
    for index, row in enumerate(rows, start=1):
        decision = row.get("decision") or {}
        qps = float(decision.get("db_qps") or 0.0)
        reference = float(decision.get("qps_gate_reference_db_qps") or 0.0)
        role = str(decision.get("qps_gate_reference_role") or "")
        tier = str(decision.get("acceptance_tier") or "")
        row_errors: List[str] = []
        if decision.get("accepted") is not True:
            row_errors.append("decision_not_accepted")
        if decision.get("qps_up") is not True or not qps > reference:
            row_errors.append("qps_not_strictly_above_reference")
        if decision.get("non_target_safe") is not True:
            row_errors.append("non_target_not_safe")
        if tier not in VALID_TIERS:
            row_errors.append("invalid_acceptance_tier")
        if tier == "strict" and not (
            decision.get("target_down") is True
            and decision.get("other_resource_preference_met") is True
        ):
            row_errors.append("strict_tier_components_missing")
        if tier == "secondary" and decision.get("target_down") is not True:
            row_errors.append("secondary_target_down_missing")
        if index == 1:
            if role != "canonical_baseline_first_round":
                row_errors.append("first_reference_is_not_canonical_baseline")
        else:
            if role != "previous_accepted_round":
                row_errors.append("later_reference_is_not_previous_accepted")
            if previous_qps is None or not math.isclose(
                reference,
                previous_qps,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                row_errors.append("reference_qps_differs_from_previous_accepted")
            if previous_qps is not None and not qps > previous_qps:
                row_errors.append("accepted_qps_chain_not_monotonic")
        errors.extend(f"accepted[{index}]:{error}" for error in row_errors)
        audit_rows.append(
            {
                "accepted_index": index,
                "round_index": int(row.get("round_index") or 0),
                "tier": tier,
                "qps": qps,
                "reference_qps": reference,
                "reference_role": role,
                "errors": row_errors,
            }
        )
        previous_qps = qps
    return not errors, errors, audit_rows


def audit_scene(scene: str) -> Dict[str, Any]:
    scene_dir = C_ROOT / scene
    state_path = scene_dir / "algorithm_c_state.json"
    history_path = scene_dir / "algorithm_c.jsonl"
    authority_path = scene_dir / "authority_manifest.json"
    missing = [
        str(path)
        for path in (state_path, history_path, authority_path)
        if not path.is_file()
    ]
    if missing:
        return {"ready": False, "scene": scene, "missing": missing}
    state = read_json(state_path)
    authority = read_json(authority_path)
    chain_passed, chain_errors, chain = audit_accepted_chain(
        state,
        history_path,
    )
    contract = state.get("outer_qps_progress_contract") or {}
    errors: List[str] = []
    if state.get("phase") not in TERMINAL_PHASES:
        errors.append(f"nonterminal_phase:{state.get('phase')}")
    if not isinstance(state.get("final_champion"), Mapping):
        errors.append("final_champion_missing")
    if state.get("manual_mutation_after_formal_start") is not False:
        errors.append("manual_mutation_flag_not_false")
    if not (
        contract.get("passed") is True
        and contract.get("contract") == QPS_PROGRESS_CONTRACT
        and contract.get("qps_reference_mode") == "last_accepted"
    ):
        errors.append("qps_progress_contract_failed")
    if authority.get("technical_failures_do_not_mutate_state") is not True:
        errors.append("technical_failure_isolation_not_authoritative")
    if authority.get("manual_mutation_after_formal_start") is not False:
        errors.append("authority_manual_mutation_flag_not_false")
    if authority.get("state_sha256") != sha256(state_path):
        errors.append("authority_state_sha256_mismatch")
    if authority.get("history_sha256") != sha256(history_path):
        errors.append("authority_history_sha256_mismatch")
    errors.extend(chain_errors)
    return {
        "ready": not errors and chain_passed,
        "scene": scene,
        "phase": state.get("phase"),
        "accepted": int(state.get("accepted") or 0),
        "accepted_target": int(state.get("accepted_target") or 0),
        "round_index": int(state.get("round_index") or 0),
        "valid_replays": int(state.get("valid_replays") or 0),
        "technical_failures": int(state.get("technical_failures") or 0),
        "accepted_chain": chain,
        "errors": errors,
    }


def audit_c() -> Dict[str, Any]:
    scenes = {scene: audit_scene(scene) for scene in SCENES}
    return {
        "ready": all(row.get("ready") is True for row in scenes.values()),
        "algorithm_c_root": str(C_ROOT),
        "algorithm_b_root": str(B_ROOT),
        "scenes": scenes,
    }


def b_prepared() -> bool:
    manifest = B_ROOT / "fresh_campaign_manifest.json"
    if not manifest.is_file():
        return False
    for scene in SCENES:
        scene_dir = B_ROOT / scene
        if not (
            (scene_dir / "optimizer_config.json").is_file()
            and (scene_dir / "experience_table_T_abc_c_holdout_admitted.json").is_file()
            and not (scene_dir / "algorithm_b_state.json").exists()
            and not (scene_dir / "algorithm_b.jsonl").exists()
        ):
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-once", action="store_true")
    parser.add_argument("--poll-s", type=float, default=30.0)
    args = parser.parse_args()
    if not b_prepared():
        raise RuntimeError(f"fresh Algorithm B campaign is not prepared: {B_ROOT}")
    if args.check_once:
        print(json.dumps(audit_c(), ensure_ascii=False, indent=2), flush=True)
        return 0

    started = time.time()
    while True:
        audit = audit_c()
        if audit["ready"]:
            break
        write_status(
            {
                "phase": "waiting_for_algorithm_c_authorities",
                "algorithm_b_started": False,
                "audit": audit,
                "updated_at_epoch_s": time.time(),
            }
        )
        time.sleep(max(5.0, args.poll_s))

    # The cohort-support process owns the same PostgreSQL workers during its
    # final baseline restore.  Waiting only for lane controllers can therefore
    # race Algorithm B's fresh baseline even after every C authority exists.
    while any(session_exists(name) for name in C_SESSIONS):
        write_status(
            {
                "phase": "waiting_for_algorithm_c_execution_domain_exit",
                "algorithm_b_started": False,
                "audit": audit,
                "active_c_sessions": [
                    name for name in C_SESSIONS if session_exists(name)
                ],
                "updated_at_epoch_s": time.time(),
            }
        )
        time.sleep(10.0)

    if any(session_exists(name) for name in B_SESSIONS):
        raise RuntimeError("Algorithm B session exists before audited handoff")
    write_status(
        {
            "phase": "launching_algorithm_b",
            "algorithm_b_started": False,
            "audit": audit,
            "algorithm_c_wait_seconds": time.time() - started,
            "updated_at_epoch_s": time.time(),
        }
    )
    launch = subprocess.run(
        [str(LAUNCH_B)],
        cwd=PROJECT,
        text=True,
        capture_output=True,
        timeout=90.0,
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
            "algorithm_b_started": True,
            "audit": audit,
            "algorithm_c_wait_seconds": time.time() - started,
            "launcher_stdout": launch.stdout.splitlines(),
            "updated_at_epoch_s": time.time(),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
