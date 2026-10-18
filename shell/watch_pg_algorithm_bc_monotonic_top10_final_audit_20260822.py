#!/usr/bin/env python3
"""Wait for fresh B/C campaigns and audit monotonic QPS and Top-10 budgets."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Mapping, Sequence, Tuple

from audit_pg_algorithm_bc_last_accepted_20260822 import (
    audit_lane,
    read_events,
)


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
STATUS = C_ROOT / "algorithm_bc_monotonic_top10_final_audit_status.json"
OUTPUT = C_ROOT / "algorithm_bc_monotonic_top10_final_audit.json"
MANIFEST_CONTRACT = "pg_algorithm_bc_monotonic_qps_top10_fresh_campaign_v1"
QPS_CONTRACT = "candidate_qps_strictly_above_previous_accepted_round_v1"
CONVERGENCE_CONTRACT = (
    "accepted_target_or_two_consecutive_budgeted_no_improvement_v3"
)
LOCAL_CONTRACT = "candidate_score_distinct_full_plan_local_top10_v1"
COVERAGE_CONTRACT = (
    "deterministic_current_interval_scored_top10_coverage_rescue_v3"
)


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


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def terminal_state(root: Path, scene: str, algorithm: str) -> Dict[str, Any]:
    state_path = root / scene / f"algorithm_{algorithm.lower()}_state.json"
    if not state_path.is_file():
        return {
            "scene": scene,
            "terminal": False,
            "passed": False,
            "reason": "state_missing",
        }
    state = read_json(state_path)
    phase = str(state.get("phase") or "")
    accepted = int(state.get("accepted") or 0)
    row: Dict[str, Any] = {
        "scene": scene,
        "phase": phase,
        "accepted_improvements": accepted,
        "outer_rounds": int(state.get("round_index") or 0),
        "physical_workload_invocations": int(
            state.get("physical_workload_invocations") or 0
        ),
        "valid_replays": int(state.get("valid_replays") or 0),
        "technical_failures": int(state.get("technical_failures") or 0),
        "terminal": False,
        "passed": False,
    }
    contract = state.get("outer_qps_progress_contract")
    contract = contract if isinstance(contract, dict) else {}
    if not (
        contract.get("passed") is True
        and contract.get("contract") == QPS_CONTRACT
        and contract.get("qps_reference_mode") == "last_accepted"
    ):
        row["reason"] = "qps_progress_contract_invalid"
        return row
    authority = root / scene / "authority_manifest.json"
    if phase == "complete":
        row["terminal"] = True
        row["passed"] = bool(
            accepted == 7
            and isinstance(state.get("final_champion"), dict)
            and authority.is_file()
        )
        row["reason"] = (
            "accepted_target_complete"
            if row["passed"]
            else "complete_evidence_missing"
        )
        return row
    if phase == "converged_on_incumbent_clean":
        convergence = state.get("incumbent_convergence")
        convergence = convergence if isinstance(convergence, dict) else {}
        receipt = convergence.get("clean_restore_receipt")
        receipt = receipt if isinstance(receipt, dict) else {}
        row["terminal"] = True
        row["passed"] = bool(
            state.get("completion_contract") == CONVERGENCE_CONTRACT
            and convergence.get("contract") == CONVERGENCE_CONTRACT
            and convergence.get("counts_as_accepted_improvement") is False
            and int(convergence.get("consecutive_no_improvement_rounds") or 0)
            >= 2
            and isinstance(state.get("final_champion"), dict)
            and receipt.get("passed") is True
            and authority.is_file()
        )
        row["reason"] = (
            "two_round_incumbent_convergence"
            if row["passed"]
            else "incumbent_convergence_evidence_invalid"
        )
        return row
    if phase.endswith("_clean"):
        row["terminal"] = True
        row["reason"] = "unexpected_clean_terminal"
        return row
    row["reason"] = "running"
    return row


def audit_top10_budgets(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    local_counts: DefaultDict[int, int] = defaultdict(int)
    coverage_counts: DefaultDict[int, int] = defaultdict(int)
    batch_violations: List[Dict[str, Any]] = []
    local_budget_events: Dict[int, Mapping[str, Any]] = {}
    coverage_start_events: DefaultDict[int, List[Mapping[str, Any]]] = defaultdict(list)
    coverage_budget_events: Dict[int, Mapping[str, Any]] = {}
    for event in events:
        event_name = str(event.get("event") or "")
        round_index = int(event.get("round_index") or 0)
        if event_name == "algorithm_c_physical_batch_start":
            count = int(event.get("candidate_count") or 0)
            if count > 5:
                batch_violations.append(
                    {"round_index": round_index, "candidate_count": count}
                )
        if event_name == "algorithm_c_candidate_replay":
            batch_kind = str(event.get("batch_kind") or "")
            if batch_kind == "base_top5" or batch_kind.startswith(
                "local_plan_tail_"
            ):
                local_counts[round_index] += 1
            if batch_kind.startswith("current_interval_plan_coverage_"):
                coverage_counts[round_index] += 1
        elif event_name == "algorithm_c_local_candidate_budget_complete":
            local_budget_events[round_index] = event
        elif event_name == (
            "algorithm_c_current_interval_coverage_after_local_exhaustion"
        ):
            coverage_start_events[round_index].append(event)
        elif event_name == (
            "algorithm_c_current_interval_coverage_budget_complete"
        ):
            coverage_budget_events[round_index] = event
    errors: List[str] = []
    if batch_violations:
        errors.append("physical batch exceeded five candidates")
    if any(count > 10 for count in local_counts.values()):
        errors.append("local candidate replay exceeded Top-10")
    if any(count > 10 for count in coverage_counts.values()):
        errors.append("current-interval coverage replay exceeded Top-10")
    ordering: List[Dict[str, Any]] = []
    for round_index, starts in coverage_start_events.items():
        local_event = local_budget_events.get(round_index)
        passed = bool(
            local_event
            and all(
                float(start.get("time") or math.inf)
                > float(local_event.get("time") or -math.inf)
                for start in starts
            )
        )
        ordering.append(
            {
                "round_index": round_index,
                "local_budget_event_present_before_coverage": passed,
            }
        )
        if not passed:
            errors.append(
                f"round {round_index}: coverage started before local Top-10 completion"
            )
    for round_index, event in local_budget_events.items():
        if int(event.get("physical_replay_count") or 0) > 10:
            errors.append(f"round {round_index}: local budget log exceeds ten")
    for round_index, event in coverage_budget_events.items():
        if int(event.get("physical_replay_count") or 0) > 10:
            errors.append(f"round {round_index}: coverage budget log exceeds ten")
    return {
        "local_physical_replays_by_round": dict(sorted(local_counts.items())),
        "coverage_physical_replays_by_round": dict(
            sorted(coverage_counts.items())
        ),
        "physical_batch_violations": batch_violations,
        "coverage_after_local_budget_checks": ordering,
        "local_budget_event_rounds": sorted(local_budget_events),
        "coverage_budget_event_rounds": sorted(coverage_budget_events),
        "passed": not errors,
        "errors": errors,
    }


def audit_manifest(root: Path, algorithm: str) -> Dict[str, Any]:
    path = root / "fresh_campaign_manifest.json"
    manifest = read_json(path)
    errors: List[str] = []
    if manifest.get("contract") != MANIFEST_CONTRACT:
        errors.append("manifest contract mismatch")
    if manifest.get("algorithm") != algorithm:
        errors.append("manifest algorithm mismatch")
    if manifest.get("qps_progress_contract") != QPS_CONTRACT:
        errors.append("manifest QPS contract mismatch")
    if manifest.get("local_candidate_contract") != LOCAL_CONTRACT:
        errors.append("manifest local Top-10 contract mismatch")
    if manifest.get("current_interval_coverage_contract") != COVERAGE_CONTRACT:
        errors.append("manifest coverage contract mismatch")
    if manifest.get("incumbent_convergence_contract") != CONVERGENCE_CONTRACT:
        errors.append("manifest convergence contract mismatch")
    for role in ("algorithm_controller", "shared_outer_controller"):
        artifact = Path(str(manifest.get(role) or ""))
        expected = str(manifest.get(f"{role}_sha256") or "")
        if not artifact.is_file() or not expected or sha256(artifact) != expected:
            errors.append(f"{role} hash mismatch")
    scenes = manifest.get("scenes")
    scenes = scenes if isinstance(scenes, dict) else {}
    for scene in SCENES:
        row = scenes.get(scene) if isinstance(scenes.get(scene), dict) else {}
        config = Path(str(row.get("new_config") or ""))
        expected = str(row.get("new_config_sha256") or "")
        if not config.is_file() or not expected or sha256(config) != expected:
            errors.append(f"{scene}: config hash mismatch")
    return {
        "path": str(path),
        "sha256": sha256(path),
        "passed": not errors,
        "errors": errors,
        "manifest": manifest,
    }


def audit_preexisting_hashes(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    protected = manifest.get("preexisting_campaign_authority")
    protected = protected if isinstance(protected, dict) else {}
    roots = protected.get("roots")
    roots = roots if isinstance(roots, dict) else {}
    checks: List[Dict[str, Any]] = []
    errors: List[str] = []
    for root_name, root_row in roots.items():
        root_row = root_row if isinstance(root_row, dict) else {}
        files = root_row.get("authority_files")
        files = files if isinstance(files, dict) else {}
        for relative, expected in files.items():
            path = Path(root_name) / str(relative)
            actual = sha256(path) if path.is_file() else ""
            passed = bool(actual and actual == str(expected))
            checks.append(
                {
                    "path": str(path),
                    "expected_sha256": str(expected),
                    "actual_sha256": actual,
                    "passed": passed,
                }
            )
            if not passed:
                errors.append(f"preexisting authority changed: {path}")
    return {"checks": checks, "passed": not errors, "errors": errors}


def main() -> int:
    started = time.time()
    while True:
        c_terminals = [terminal_state(C_ROOT, scene, "C") for scene in SCENES]
        b_terminals = [terminal_state(B_ROOT, scene, "B") for scene in SCENES]
        unexpected = any(
            row["terminal"] and not row["passed"]
            for row in c_terminals + b_terminals
        )
        all_terminal = all(row["terminal"] for row in c_terminals + b_terminals)
        atomic_json(
            STATUS,
            {
                "phase": (
                    "unexpected_terminal"
                    if unexpected
                    else (
                        "running_final_audit"
                        if all_terminal
                        else "waiting_for_both_campaigns"
                    )
                ),
                "algorithm_c_lanes": c_terminals,
                "algorithm_b_lanes": b_terminals,
                "elapsed_s": time.time() - started,
                "updated_at_epoch_s": time.time(),
            },
        )
        if unexpected:
            return 2
        if all_terminal:
            break
        time.sleep(60.0)

    c_manifest = audit_manifest(C_ROOT, "C")
    b_manifest = audit_manifest(B_ROOT, "B")
    history_hashes = audit_preexisting_hashes(c_manifest["manifest"])
    campaigns: List[Dict[str, Any]] = []
    for algorithm, root in (("C", C_ROOT), ("B", B_ROOT)):
        lane_rows: List[Dict[str, Any]] = []
        for scene in SCENES:
            base = audit_lane(algorithm, root, scene, require_complete=False)
            history = root / scene / f"algorithm_{algorithm.lower()}.jsonl"
            top10 = audit_top10_budgets(read_events(history))
            terminal = terminal_state(root, scene, algorithm)
            lane_rows.append(
                {
                    "scene": scene,
                    "terminal": terminal,
                    "base_outer_audit": base,
                    "top10_budget_audit": top10,
                    "passed": bool(
                        terminal["passed"]
                        and base.get("passed") is True
                        and top10["passed"]
                    ),
                }
            )
        campaigns.append(
            {
                "algorithm": algorithm,
                "root": str(root),
                "lanes": lane_rows,
                "passed": all(row["passed"] for row in lane_rows),
            }
        )
    passed = bool(
        c_manifest["passed"]
        and b_manifest["passed"]
        and history_hashes["passed"]
        and all(campaign["passed"] for campaign in campaigns)
    )
    payload = {
        "contract": "pg_algorithm_bc_monotonic_qps_top10_final_audit_v1",
        "algorithm_c_root": str(C_ROOT),
        "algorithm_b_root": str(B_ROOT),
        "algorithm_c_manifest_audit": c_manifest,
        "algorithm_b_manifest_audit": b_manifest,
        "preexisting_authority_hash_audit": history_hashes,
        "campaigns": campaigns,
        "passed": passed,
        "completed_at_epoch_s": time.time(),
    }
    atomic_json(OUTPUT, payload)
    atomic_json(
        STATUS,
        {
            "phase": "final_audit_passed" if passed else "final_audit_failed",
            "audit_path": str(OUTPUT),
            "elapsed_s": time.time() - started,
            "updated_at_epoch_s": time.time(),
        },
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
