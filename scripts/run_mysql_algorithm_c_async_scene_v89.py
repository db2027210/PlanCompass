#!/usr/bin/env python3
"""Run IO C v89 from its exact post-fallback/pre-coverage boundary."""

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


CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v45"
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"
SOURCE_ROOT = ROOT / "history/mysql_four_scene_20260809/v1023_to_c_io_outer12_v84_reset_serial_resume_20260828_xp_io_xp_io"
SOURCE_STATE = SOURCE_ROOT / "run_state.json"
SOURCE_RANKING = SOURCE_ROOT / "outer_013/predicted_rankings.json"
SOURCE_TOP5_WAVE1 = SOURCE_ROOT / "outer_013/top5_batch_1/batch_result.json"
SOURCE_TOP5_WAVE2 = SOURCE_ROOT / "outer_013/top5_batch_2/batch_result.json"
SOURCE_FALLBACK_WAVE1 = SOURCE_ROOT / "outer_013/fallback_plan_change/wave_1/batch_result.json"
SOURCE_FALLBACK_WAVE2 = SOURCE_ROOT / "outer_013/fallback_plan_change/wave_2/batch_result.json"
SOURCE_COVERAGE_GATE = SOURCE_ROOT / "outer_013/current_interval_plan_coverage/wave_1_cross_project_wave_quiet_gate_attempt_001.json"
BASELINE = ROOT / "history/mysql_four_scene_20260809/v1033_c_io_outer12_checkpoint_incumbent_baseline_20260828/checkpoint_incumbent_baseline.json"
SCENE = "hybench_xp_io"


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    alg = partial_runtime.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    boundary = mapping(contract.get("post_fallback_partial_boundary"), "partial boundary")
    carry = mapping(contract.get("current_checkpoint_incumbent_baseline"), "baseline carry")
    fallback = mapping(contract.get("completed_fallback_measurement_continuation"), "fallback continuation")
    a_boundary = mapping(contract.get("algorithm_a_independent_check_boundary"), "A boundary")
    if (
        parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("release_version") != "v89"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("sha256") != alg.sha(PARENT_CONTRACT)
        or boundary.get("scene") != SCENE
        or boundary.get("outer_attempt") != 13
        or boundary.get("completed_top5_real_candidate_count") != 1
        or boundary.get("completed_fallback_real_candidate_count") != 1
        or boundary.get("coverage_measurement_started") is not False
        or carry.get("scene") != SCENE
        or carry.get("worker_count") != 4
        or carry.get("physical_incumbent_remeasurement") is not False
        or fallback.get("new_physical_work_starts_at") != "current_interval_plan_coverage"
        or fallback.get("completed_top5_physically_replayed") is not False
        or fallback.get("completed_fallback_physically_replayed") is not False
        or a_boundary.get("algorithm_a_role") != "independent_posthoc_check_only"
        or a_boundary.get("algorithm_a_feedback_to_b_or_c") is not False
    ):
        raise RuntimeError("Algorithm C v89 IO continuation contract differs")
    for document, label in ((parent, "parent"), (contract, "successor")):
        for name, raw in mapping(document.get("source_bindings"), f"{label} bindings").items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if not path.is_file() or path.is_symlink() or alg.sha(path) != row.get("sha256"):
                raise RuntimeError(f"Algorithm C v89 source binding differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def validate_source(alg: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    state = alg.load(SOURCE_STATE)
    wave1 = alg.load(SOURCE_FALLBACK_WAVE1)
    wave2 = alg.load(SOURCE_FALLBACK_WAVE2)
    rows = mapping(wave1.get("measurements"), "fallback measurements")
    real = [row for row in rows.values() if int(row.get("rank") or 0) > 0]
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("error") != "RuntimeError: xp_io_w3 quarantine target already exists"
        or int(state.get("outer_attempt") or 0) != 12
        or int((state.get("accepted_rounds") or {}).get(SCENE) or 0) != 5
        or int((state.get("consecutive_failed_rounds") or {}).get(SCENE) or 0) != 1
        or wave1.get("status") != "PASS"
        or wave1.get("failures") not in ([], None)
        or len(rows) != 4
        or len(real) != 1
        or int(real[0].get("rank") or 0) != 1
        or real[0].get("hard_gate_passed") is not True
        or real[0].get("coverage_complete") is not True
        or wave2.get("status") != "PASS"
        or wave2.get("empty_candidate_wave") is not True
        or wave2.get("candidate_count") != 0
        or wave2.get("failures") not in ([], None)
    ):
        raise RuntimeError("Algorithm C v89 IO fallback source differs")
    return state, wave1, wave2


def validate_post_fallback_partial(
    *, source_root: Path, checkpoint: Mapping[str, Any],
    reconstructed: Mapping[str, Any], start_rank: int,
    completed_rank5_root: Path | None = None,
) -> dict[str, Any] | None:
    del checkpoint
    source_root = Path(source_root).resolve()
    if source_root != SOURCE_ROOT.resolve() or start_rank != 5 or completed_rank5_root is not None:
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
    real = [copy.deepcopy(row) for row in rows1.values() if int(row.get("rank") or 0) > 0]
    rows2 = mapping(batch2.get("measurements"), "empty Top-K measurements")
    baseline = alg.load(BASELINE)
    baseline_rows = mapping(baseline.get("measurements"), "baseline measurements")
    incumbent = mapping(mapping(reconstructed.get("incumbents"), "incumbents").get(SCENE), "incumbent")
    params = mapping(mapping(reconstructed.get("params_by_scene"), "params").get(SCENE), "params")
    state_fields = (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds", "stable_intervals", "converged",
    )
    if (
        expected_outer != 13
        or state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != "RuntimeError: xp_io_w3 quarantine target already exists"
        or state.get("formal_evidence_eligible") is not True
        or state.get("async_scene") != SCENE
        or int(state.get("outer_attempt") or -1) != int(reconstructed.get("outer_attempt") or -2)
        or any(state.get(field) != reconstructed.get(field) for field in state_fields)
        or list((SOURCE_ROOT / "outer_013").glob("*.decision.json"))
        or ranking.get("schema_version") != "mysql_four_scene_algorithm_c_rankings_boundary_resume_v2"
        or int(ranking.get("outer_attempt") or 0) != 13
        or ranking.get("active_decision_scenes") != [SCENE]
        or set(selected) != {SCENE}
        or len(selected_rows) != 1
        or batch1.get("status") != "PASS"
        or batch1.get("failures") not in ([], None)
        or len(real) != 1
        or int(real[0].get("rank") or 0) != 1
        or any(row.get("hard_gate_passed") is not True or row.get("coverage_complete") is not True for row in rows1.values())
        or batch2.get("status") != "PASS"
        or batch2.get("empty_candidate_wave") is not True
        or batch2.get("candidate_count") != 0
        or len(rows2) != 1
        or gate.get("status") != "PASS"
        or gate.get("errors") not in ([], None)
        or baseline.get("schema_version") != "mysql_algorithm_c_checkpoint_incumbent_baseline_v2"
        or set(baseline_rows) != {"xp_io_w1", "xp_io_w2", "xp_io_w3", "xp_io_w4"}
        or any(
            float(row.get("db_qps") or 0) != float(incumbent["db_qps"])
            or alg.optimizer_params_key(row.get("params") or {}) != alg.optimizer_params_key(params)
            or row.get("checkpoint_incumbent_carry_forward") is not True
            for row in baseline_rows.values()
        )
        or alg.optimizer_params_key(real[0].get("params") or {})
        != alg.optimizer_params_key(mapping(selected_rows[0].get("optimized_params"), "predicted params"))
    ):
        raise RuntimeError("Algorithm C v89 IO partial evidence differs")
    real[0]["candidate_ranking"] = copy.deepcopy(selected_rows[0])
    batch1_binding = {"batch_index": 1, "candidate_ranks": [1], "path": str(SOURCE_TOP5_WAVE1), "sha256": alg.sha(SOURCE_TOP5_WAVE1)}
    batch2_binding = {"batch_index": 2, "candidate_ranks": [], "path": str(SOURCE_TOP5_WAVE2), "sha256": alg.sha(SOURCE_TOP5_WAVE2), "semantic_empty_wave_excluded": True}
    return {
        "source_root": str(source_root),
        "source_state": {"path": str(SOURCE_STATE), "sha256": alg.sha(SOURCE_STATE)},
        "baseline": {"path": str(BASELINE), "sha256": alg.sha(BASELINE)},
        "predicted_rankings": {"path": str(SOURCE_RANKING), "sha256": alg.sha(SOURCE_RANKING)},
        "blocked_gate": {"path": str(SOURCE_COVERAGE_GATE), "sha256": alg.sha(SOURCE_COVERAGE_GATE)},
        "continuation_reason": "completed_top5_and_fallback_then_coverage_reset_collision",
        "runtime_interruption_watchdog": None,
        "completed_batches": [batch1_binding, batch2_binding],
        "start_rank": 6,
        "outer_attempt": 13,
        "ranking_document": ranking,
        "evaluations": {SCENE: [real[0]]},
        "technical_batch_exclusions": [],
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "completed_rank_release_race": False,
        "completed_rank5_recovery": None,
        "empty_candidate_ranks": [2, 3, 4, 5],
        "empty_candidate_gate_and_measurement_skipped": True,
        "completed_fallback_reused": True,
        "new_physical_work_starts_at": "current_interval_plan_coverage",
        "checkpoint_incumbent_baseline_carry_forward": True,
        "checkpoint_incumbent_baseline_worker_count": 4,
    }


def main() -> int:
    validate_contract()
    old = {
        "root": predecessor.CONTRACT_ROOT,
        "parent": predecessor.PARENT_CONTRACT,
        "optimization": predecessor.OPTIMIZATION_CONTRACT,
        "contract": predecessor.CONTRACT,
        "validate": predecessor.validate_contract,
        "source_root": predecessor.SOURCE_ROOT,
        "source_state": predecessor.SOURCE_STATE,
        "fallback1": predecessor.SOURCE_FALLBACK_WAVE1,
        "fallback2": predecessor.SOURCE_FALLBACK_WAVE2,
        "scene": predecessor.SCENE,
        "validate_source": predecessor.validate_source,
        "partial": partial_runtime._validate_partial_topk_settlement,
    }
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.PARENT_CONTRACT = PARENT_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.CONTRACT = CONTRACT
    predecessor.validate_contract = validate_contract
    predecessor.SOURCE_ROOT = SOURCE_ROOT
    predecessor.SOURCE_STATE = SOURCE_STATE
    predecessor.SOURCE_FALLBACK_WAVE1 = SOURCE_FALLBACK_WAVE1
    predecessor.SOURCE_FALLBACK_WAVE2 = SOURCE_FALLBACK_WAVE2
    predecessor.SCENE = SCENE
    predecessor.validate_source = validate_source
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
        predecessor.SOURCE_ROOT = old["source_root"]
        predecessor.SOURCE_STATE = old["source_state"]
        predecessor.SOURCE_FALLBACK_WAVE1 = old["fallback1"]
        predecessor.SOURCE_FALLBACK_WAVE2 = old["fallback2"]
        predecessor.SCENE = old["scene"]
        predecessor.validate_source = old["validate_source"]


if __name__ == "__main__":
    raise SystemExit(main())
