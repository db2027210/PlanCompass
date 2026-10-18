#!/usr/bin/env python3
"""Run Top-4 Algorithm C with zero-work empty-wave skipping v68."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as checkpointmod
from scripts import mysql_cross_project_wave_coordination_v11 as gate_v11
from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle
from scripts import preflight_mysql_four_scene_v9 as preflight_v9
from scripts import run_mysql_algorithm_c_async_scene_v52 as top4
from scripts import run_mysql_algorithm_c_async_scene_v63 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v28 as v28


alg = predecessor.alg
runtime = predecessor.predecessor.runtime
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_qps_20260826_v20"
)
ASYNC_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} must be an object")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    contract = alg.load(ASYNC_CONTRACT)
    partial = contract.get("partial_topk_settlement_recovery") or {}
    transient = contract.get("actual_disjoint_transient_client_policy") or {}
    if (
        contract.get("release_version") != "v68"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("outer_acceptance_policy") != qps_policy.policy_contract()
        or contract.get("outer_rejection_cycle_policy")
        != rejection_cycle.policy_contract()
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("physical_replay_parallelism_per_scene") != 2
        or contract.get("maximum_concurrent_mysql_workers") != 8
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("lease_mode") != "none"
        or contract.get("post_search_stability_confirmation") is not False
        or contract.get("empty_candidate_wave_policy", {}).get(
            "implemented_by_runtime_successor_v68"
        ) is not True
        or contract.get("xp_top4_boundary_integrity", {}).get(
            "active_and_range_keys_must_match_frozen_top4"
        ) is not True
        or partial.get("settle_when_all_selected_candidates_measured") is not True
        or partial.get("empty_candidate_ranks_require_no_gate_or_measurement") is not True
        or transient.get("actual_target_cpu_overlap_prohibited") is not True
        or transient.get("external_process_mutation_performed") is not False
    ):
        raise RuntimeError("v68 Algorithm C recovery contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("v68 Algorithm C source binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding.get("sha256") or "")
        ):
            raise RuntimeError(f"v68 Algorithm C source binding differs: {path}")
    return contract


_ORIGINAL_CHECKPOINT_VERIFY = checkpointmod.verify_checkpoint


def verify_top4_checkpoint(path: Path) -> dict[str, Any]:
    checkpoint = _ORIGINAL_CHECKPOINT_VERIFY(path)
    boundary = checkpoint.get("independent_scene_boundary") or {}
    scene = str(boundary.get("scene") or "")
    if scene in top4.EXPECTED_ACTIVE:
        reconstructed = checkpoint.get("reconstructed_state") or {}
        active = list((reconstructed.get("active_by_scene") or {}).get(scene) or [])
        ranges = (reconstructed.get("ranges_by_scene") or {}).get(scene) or {}
        expected = list(top4.EXPECTED_ACTIVE[scene])
        if active != expected or set(ranges) != set(expected):
            raise RuntimeError(f"{scene} boundary is not frozen Top-4")
    return checkpoint


def _validate_partial_topk_settlement(
    *, source_root: Path, checkpoint: Mapping[str, Any],
    reconstructed: Mapping[str, Any], start_rank: int,
    completed_rank5_root: Path | None = None,
) -> dict[str, Any] | None:
    """Reuse the two real candidates and settle; ranks 3..5 do not exist."""
    source_root = Path(source_root).resolve()
    if start_rank != 5 or completed_rank5_root is not None:
        return None
    scene = "hybench_xp_io"
    expected_outer = int(reconstructed.get("outer_attempt") or 0) + 1
    state_path = source_root / "run_state.json"
    outer_root = source_root / f"outer_{expected_outer:03d}"
    ranking_path = outer_root / "predicted_rankings.json"
    batch1_path = outer_root / "top5_batch_1/batch_result.json"
    empty_batch_path = outer_root / "top5_batch_2/batch_result.json"
    gate_path = outer_root / "top5_batch_3_dynamic_noninterference_gate.json"
    baseline_binding_path = source_root / "formal_incumbent_baseline/batch_result.json"
    required = (
        state_path, ranking_path, batch1_path, empty_batch_path,
        gate_path, baseline_binding_path,
    )
    if any(not path.is_file() or path.is_symlink() for path in required):
        raise RuntimeError("partial-TopK settlement evidence is incomplete")

    state = alg.load(state_path)
    ranking = alg.load(ranking_path)
    batch1 = alg.load(batch1_path)
    empty_batch = alg.load(empty_batch_path)
    gate = alg.load(gate_path)
    baseline = alg.load(baseline_binding_path)
    state_fields = (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged",
    )
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != "RuntimeError: dynamic non-interference gate failed closed"
        or state.get("formal_evidence_eligible") is not True
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("async_scene") != scene
        or int(state.get("outer_attempt") or -1)
        != int(reconstructed.get("outer_attempt") or -2)
        or any(state.get(field) != reconstructed.get(field) for field in state_fields)
        or (state.get("full_plan_replay_history_v2") or {}).get(scene)
        != (reconstructed.get("full_plan_replay_history_v2") or {}).get(scene)
        or list(outer_root.glob("*.decision.json"))
        or (outer_root / "top5_batch_3/batch_result.json").exists()
    ):
        raise RuntimeError("partial-TopK source state differs")

    selected = _mapping(ranking.get("selected_top_k"), "selected Top-K")
    selected_rows = list(selected.get(scene) or [])
    if (
        ranking.get("schema_version")
        != "mysql_four_scene_algorithm_c_rankings_boundary_resume_v2"
        or int(ranking.get("outer_attempt") or 0) != expected_outer
        or ranking.get("active_decision_scenes") != [scene]
        or set(selected) != {scene}
        or len(selected_rows) != 2
        or [int(row.get("rank") or 0) for row in selected_rows] != [1, 2]
        or len((_mapping(ranking.get("all_nonempty_subsets"), "all rankings").get(scene) or [])) != 15
        or ranking.get("top_k_is_upper_bound") is not True
        or ranking.get("partial_top_k_allowed") is not True
    ):
        raise RuntimeError("partial-TopK ranking differs")

    measurements = _mapping(batch1.get("measurements"), "candidate measurements")
    if (
        batch1.get("status") != "PASS"
        or batch1.get("failures") not in ([], None)
        or len(measurements) != 2
        or sorted(int(row.get("rank") or 0) for row in measurements.values()) != [1, 2]
        or {str(row.get("scene")) for row in measurements.values()} != {scene}
        or any(
            row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in measurements.values()
        )
    ):
        raise RuntimeError("partial-TopK candidate batch differs")
    by_rank = {int(row["rank"]): row for row in measurements.values()}
    for rank, predicted in enumerate(selected_rows, start=1):
        if alg.optimizer_params_key(by_rank[rank]["params"]) != alg.optimizer_params_key(
            _mapping(predicted.get("optimized_params"), "predicted params")
        ):
            raise RuntimeError(f"partial-TopK rank {rank} parameters differ")

    empty_rows = _mapping(empty_batch.get("measurements"), "legacy empty-wave rows")
    incumbent_params = _mapping(
        _mapping(reconstructed.get("params_by_scene"), "checkpoint params").get(scene),
        "checkpoint scene params",
    )
    if (
        empty_batch.get("status") != "PASS"
        or empty_batch.get("failures") not in ([], None)
        or len(empty_rows) != 2
        or any(
            int(row.get("rank", -1)) != 0
            or row.get("scene") != scene
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            or alg.optimizer_params_key(row.get("params") or {})
            != alg.optimizer_params_key(incumbent_params)
            for row in empty_rows.values()
        )
    ):
        raise RuntimeError("legacy empty-wave diagnostic differs")

    baseline_binding = _mapping(
        state.get("formal_incumbent_baseline"), "formal incumbent baseline"
    )
    baseline_rows = _mapping(baseline.get("measurements"), "baseline measurements")
    if (
        baseline_binding.get("path") != str(baseline_binding_path)
        or baseline_binding.get("sha256") != alg.sha(baseline_binding_path)
        or baseline.get("status") != "PASS"
        or baseline.get("failures") not in ([], None)
        or len(baseline_rows) != 2
        or any(
            row.get("scene") != scene
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in baseline_rows.values()
        )
        or gate.get("status") != "BLOCKED_FAIL_CLOSED"
        or gate.get("errors") != ["dynamic_noninterference_window_timeout"]
        or float(gate.get("observed_seconds") or 0) < 180.0
    ):
        raise RuntimeError("partial-TopK boundary evidence differs")

    batch_binding = {
        "batch_index": 1,
        "candidate_ranks": [1, 2],
        "path": str(batch1_path),
        "sha256": alg.sha(batch1_path),
    }
    empty_binding = {
        "batch_index": 2,
        "candidate_ranks": [],
        "path": str(empty_batch_path),
        "sha256": alg.sha(empty_batch_path),
        "legacy_incumbent_only_empty_wave_excluded": True,
    }
    evaluations = {scene: []}
    for rank in (1, 2):
        row = copy.deepcopy(dict(by_rank[rank]))
        row["candidate_ranking"] = copy.deepcopy(selected_rows[rank - 1])
        row["formal_partial_replay_source"] = copy.deepcopy(batch_binding)
        evaluations[scene].append(row)
    return {
        "source_root": str(source_root),
        "source_state": {"path": str(state_path), "sha256": alg.sha(state_path)},
        "baseline": {
            "path": str(baseline_binding_path),
            "sha256": alg.sha(baseline_binding_path),
        },
        "predicted_rankings": {
            "path": str(ranking_path), "sha256": alg.sha(ranking_path),
        },
        "blocked_gate": {"path": str(gate_path), "sha256": alg.sha(gate_path)},
        "continuation_reason": "settle_completed_partial_topk_without_empty_waves",
        "runtime_interruption_watchdog": None,
        "completed_batches": [batch_binding, empty_binding],
        "start_rank": 6,
        "outer_attempt": expected_outer,
        "ranking_document": ranking,
        "evaluations": evaluations,
        "technical_batch_exclusions": [],
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "completed_rank_release_race": False,
        "completed_rank5_recovery": None,
        "empty_candidate_ranks": [3, 4, 5],
        "empty_candidate_gate_and_measurement_skipped": True,
        "legacy_empty_wave_incumbent_measurement_excluded_from_decision": True,
    }


def main() -> int:
    v62 = predecessor.predecessor
    v62.gate_v8 = gate_v11
    v62.preflight_v8 = preflight_v9
    predecessor.ASYNC_CONTRACT = ASYNC_CONTRACT
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.validate_contract = validate_contract
    original_v62_install = v62.install

    def install_with_empty_wave_skip() -> None:
        original_v62_install()
        installed_measure = runtime._measure_batch_with_fair_coordination

        def measure_or_skip_empty(**kwargs: Any):
            assignments = kwargs.get("assignments") or {}
            if assignments:
                return installed_measure(**kwargs)
            output = Path(kwargs["output"])
            state_path = Path(kwargs["state_path"])
            state = kwargs["state"]
            stage_prefix = str(kwargs["stage_prefix"])
            audit = {
                "schema_version": "mysql_algorithm_c_empty_candidate_wave_skip_v1",
                "status": "PASS",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "four_scene_wave_barrier": False,
                "failures": [],
                "measurements": {},
                "elapsed_seconds": 0.0,
                "empty_candidate_wave": True,
                "candidate_count": 0,
                "dynamic_noninterference_gate_started": False,
                "mysql_container_reset_performed": False,
                "physical_workload_started": False,
                "semantic_decision_deferred_to_all_admitted_candidates": True,
                "captured_at": alg.now(),
            }
            alg.atomic(output / "batch_result.json", audit)
            state["stage"] = f"{stage_prefix}_EMPTY_CANDIDATE_WAVE_SKIPPED"
            state.setdefault("empty_candidate_wave_skips", []).append({
                "stage_prefix": stage_prefix,
                "batch_result_path": str(output / "batch_result.json"),
                "batch_result_sha256": alg.sha(output / "batch_result.json"),
                "gate_started": False,
                "reset_performed": False,
                "measurement_started": False,
            })
            alg.atomic(state_path, state)
            return {}, state

        runtime._measure_batch_with_fair_coordination = measure_or_skip_empty

    v62.install = install_with_empty_wave_skip
    original_checkpoint = checkpointmod.verify_checkpoint
    original_partial = runtime._validate_formal_partial_continuation
    original_v28_partial = v28.validate_formal_partial_continuation
    checkpointmod.verify_checkpoint = verify_top4_checkpoint
    runtime._validate_formal_partial_continuation = _validate_partial_topk_settlement
    v28.validate_formal_partial_continuation = _validate_partial_topk_settlement
    try:
        return predecessor.main()
    finally:
        v62.install = original_v62_install
        checkpointmod.verify_checkpoint = original_checkpoint
        runtime._validate_formal_partial_continuation = original_partial
        v28.validate_formal_partial_continuation = original_v28_partial


if __name__ == "__main__":
    raise SystemExit(main())
