#!/usr/bin/env python3
"""Run C v87 from the exact post-fallback/pre-coverage CPU boundary."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_v73 as partial_runtime
from scripts import run_mysql_algorithm_c_async_scene_v86 as predecessor


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v43"
)
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"
SOURCE_ROOT = predecessor.SOURCE_ROOT
SOURCE_STATE = predecessor.SOURCE_STATE
SOURCE_RANKING = SOURCE_ROOT / "outer_018/predicted_rankings.json"
SOURCE_TOP5_WAVE1 = SOURCE_ROOT / "outer_018/top5_batch_1/batch_result.json"
SOURCE_TOP5_WAVE2 = SOURCE_ROOT / "outer_018/top5_batch_2/batch_result.json"
SOURCE_COVERAGE_GATE = (
    SOURCE_ROOT
    / "outer_018/current_interval_plan_coverage/"
    "wave_1_cross_project_wave_quiet_gate_attempt_001.json"
)
SCENE = predecessor.SCENE


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    v85 = predecessor.predecessor
    v84 = v85.predecessor
    v83 = v84.predecessor
    v82 = v83.predecessor
    v81 = v82.predecessor
    v80 = v81.predecessor
    alg = v80.coverage_runtime.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    boundary = mapping(
        contract.get("post_fallback_partial_boundary"), "partial boundary"
    )
    a_boundary = mapping(
        contract.get("algorithm_a_independent_check_boundary"), "A boundary"
    )
    if (
        parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v87"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("sha256") != alg.sha(PARENT_CONTRACT)
        or boundary.get("scene") != SCENE
        or boundary.get("outer_attempt") != 18
        or boundary.get("completed_top5_real_candidate_count") != 3
        or boundary.get("completed_fallback_real_candidate_count") != 1
        or boundary.get("coverage_gate_passed") is not True
        or boundary.get("coverage_measurement_started") is not False
        or boundary.get("accepted_or_rejected_credit_for_recovery") != 0
        or boundary.get("candidate_or_outer_policy_changed") is not False
        or a_boundary.get("algorithm_a_role") != "independent_posthoc_check_only"
        or a_boundary.get("algorithm_a_feedback_to_b_or_c") is not False
    ):
        raise RuntimeError("Algorithm C v87 post-fallback boundary contract differs")
    for document, label in ((parent, "parent"), (contract, "successor")):
        for name, raw in mapping(document.get("source_bindings"), f"{label} bindings").items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if not path.is_file() or path.is_symlink() or alg.sha(path) != row.get("sha256"):
                raise RuntimeError(f"Algorithm C v87 source binding differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def validate_post_fallback_partial(
    *, source_root: Path, checkpoint: Mapping[str, Any],
    reconstructed: Mapping[str, Any], start_rank: int,
    completed_rank5_root: Path | None = None,
) -> dict[str, Any] | None:
    source_root = Path(source_root).resolve()
    if (
        source_root != SOURCE_ROOT.resolve()
        or start_rank != 5
        or completed_rank5_root is not None
    ):
        return None
    alg = partial_runtime.alg
    expected_outer = int(reconstructed.get("outer_attempt") or 0) + 1
    state = alg.load(SOURCE_STATE)
    ranking = alg.load(SOURCE_RANKING)
    batch1 = alg.load(SOURCE_TOP5_WAVE1)
    batch2 = alg.load(SOURCE_TOP5_WAVE2)
    gate = alg.load(SOURCE_COVERAGE_GATE)
    selected = mapping(ranking.get("selected_top_k"), "selected Top-K")
    selected_rows = list(selected.get(SCENE) or [])
    rows1 = mapping(batch1.get("measurements"), "top5 measurements")
    real = {
        int(row["rank"]): copy.deepcopy(dict(row))
        for row in rows1.values() if int(row.get("rank") or 0) > 0
    }
    companions = [row for row in rows1.values() if int(row.get("rank") or 0) == 0]
    rows2 = mapping(batch2.get("measurements"), "empty Top-K measurements")
    state_fields = (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged",
    )
    if (
        expected_outer != 18
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error")
        != "RuntimeError: xp_cpu_w3 quarantine target already exists"
        or state.get("formal_evidence_eligible") is not True
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("async_scene") != SCENE
        or int(state.get("outer_attempt") or -1)
        != int(reconstructed.get("outer_attempt") or -2)
        or any(state.get(field) != reconstructed.get(field) for field in state_fields)
        or (state.get("full_plan_replay_history_v2") or {}).get(SCENE)
        != (reconstructed.get("full_plan_replay_history_v2") or {}).get(SCENE)
        or list((SOURCE_ROOT / "outer_018").glob("*.decision.json"))
        or ranking.get("schema_version")
        != "mysql_four_scene_algorithm_c_rankings_boundary_resume_v2"
        or int(ranking.get("outer_attempt") or 0) != 18
        or ranking.get("active_decision_scenes") != [SCENE]
        or set(selected) != {SCENE}
        or len(selected_rows) != 3
        or ranking.get("top_k_is_upper_bound") is not True
        or ranking.get("partial_top_k_allowed") is not True
        or batch1.get("status") != "PASS"
        or batch1.get("failures") not in ([], None)
        or set(real) != {1, 2, 3}
        or len(companions) != 1
        or any(
            row.get("scene") != SCENE
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in rows1.values()
        )
        or batch2.get("status") != "PASS"
        or batch2.get("empty_candidate_wave") is not True
        or batch2.get("candidate_count") != 0
        or batch2.get("failures") not in ([], None)
        or len(rows2) != 1
        or any(
            int(row.get("rank") or 0) != 0
            or row.get("scene") != SCENE
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in rows2.values()
        )
        or gate.get("status") != "PASS"
        or gate.get("errors") not in ([], None)
    ):
        raise RuntimeError("Algorithm C v87 post-fallback partial evidence differs")
    for rank, predicted in enumerate(selected_rows, 1):
        if alg.optimizer_params_key(real[rank].get("params") or {}) != alg.optimizer_params_key(
            mapping(predicted.get("optimized_params"), "predicted params")
        ):
            raise RuntimeError(f"Algorithm C v87 top5 rank {rank} differs")
    batch1_binding = {
        "batch_index": 1,
        "candidate_ranks": [1, 2, 3],
        "path": str(SOURCE_TOP5_WAVE1),
        "sha256": alg.sha(SOURCE_TOP5_WAVE1),
    }
    batch2_binding = {
        "batch_index": 2,
        "candidate_ranks": [],
        "path": str(SOURCE_TOP5_WAVE2),
        "sha256": alg.sha(SOURCE_TOP5_WAVE2),
        "semantic_empty_wave_excluded": True,
    }
    evaluations = {SCENE: []}
    for rank in (1, 2, 3):
        row = copy.deepcopy(real[rank])
        row["candidate_ranking"] = copy.deepcopy(selected_rows[rank - 1])
        row["formal_partial_replay_source"] = copy.deepcopy(batch1_binding)
        evaluations[SCENE].append(row)
    baseline = mapping(state.get("formal_incumbent_baseline"), "incumbent baseline")
    baseline_path = Path(str(baseline.get("path") or "")).resolve()
    if (
        not baseline_path.is_file()
        or baseline_path.is_symlink()
        or baseline.get("sha256") != alg.sha(baseline_path)
        or baseline.get("status") != "PASS"
    ):
        raise RuntimeError("Algorithm C v87 checkpoint incumbent differs")
    return {
        "source_root": str(source_root),
        "source_state": {"path": str(SOURCE_STATE), "sha256": alg.sha(SOURCE_STATE)},
        "baseline": {"path": str(baseline_path), "sha256": alg.sha(baseline_path)},
        "predicted_rankings": {"path": str(SOURCE_RANKING), "sha256": alg.sha(SOURCE_RANKING)},
        "blocked_gate": {"path": str(SOURCE_COVERAGE_GATE), "sha256": alg.sha(SOURCE_COVERAGE_GATE)},
        "continuation_reason": "completed_top5_and_fallback_then_coverage_reset_collision",
        "runtime_interruption_watchdog": None,
        "completed_batches": [batch1_binding, batch2_binding],
        "start_rank": 6,
        "outer_attempt": 18,
        "ranking_document": ranking,
        "evaluations": evaluations,
        "technical_batch_exclusions": [],
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "completed_rank_release_race": False,
        "completed_rank5_recovery": None,
        "empty_candidate_ranks": [4, 5],
        "empty_candidate_gate_and_measurement_skipped": True,
        "legacy_empty_wave_incumbent_measurement_excluded_from_decision": True,
        "completed_fallback_reused_by_v86": True,
        "new_physical_work_starts_at": "current_interval_plan_coverage",
    }


def main() -> int:
    validate_contract()
    old = {
        "root": predecessor.CONTRACT_ROOT,
        "parent": predecessor.PARENT_CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "contract": predecessor.CONTRACT,
        "validate": predecessor.validate_contract,
        "partial": partial_runtime._validate_partial_topk_settlement,
    }
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.PARENT_CONTRACT = PARENT_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.CONTRACT = CONTRACT
    predecessor.validate_contract = validate_contract
    partial_runtime._validate_partial_topk_settlement = validate_post_fallback_partial
    try:
        return int(predecessor.main())
    finally:
        partial_runtime._validate_partial_topk_settlement = old["partial"]
        predecessor.CONTRACT_ROOT = old["root"]
        predecessor.PARENT_CONTRACT = old["parent"]
        predecessor.OPTIMIZATION_CONTRACT = old["optimization"]
        predecessor.CONTRACT = old["contract"]
        predecessor.validate_contract = old["validate"]


if __name__ == "__main__":
    raise SystemExit(main())
