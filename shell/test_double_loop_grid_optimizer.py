#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from double_loop_grid_optimizer import DoubleLoopGridOptimizer, GridInnerLoopConfig
from double_loop_search_common import OuterLoopConfig, ParameterRange


def main() -> None:
    real_calls = []
    evidence_calls = []

    def prune(_sql_set: Any, candidates: Sequence[str]) -> Sequence[str]:
        assert list(candidates) == ["x", "y", "unused"]
        return ["x", "y"]

    def resource_score(resource: Dict[str, float], _raw: Any) -> float:
        return 1.0 - (resource["cpu"] - 0.7) ** 2 - (resource["memory"] - 0.3) ** 2

    def estimated(_sql_set: Any, params: Dict[str, float]) -> Dict[str, Any]:
        resource = {
            "cpu": params["x"],
            "memory": params["y"],
            "io": 0.5,
            # This value must never affect grid ordering.
            "db_qps": 1000.0 * (1.0 - params["x"]),
        }
        score = resource_score(resource, None)
        return {
            "resource": resource,
            "score": score,
            "estimated_score": score,
        }

    def real(_sql_set: Any, params: Dict[str, float]) -> Dict[str, Any]:
        real_calls.append(dict(params))
        resource = {
            "cpu": params["x"],
            "memory": params["y"],
            "io": 0.5,
            "db_qps": 100.0 + 10.0 * params["x"],
        }
        return {"resource": resource, "score": resource_score(resource, None)}

    def capture_evidence(_sql_set: Any, round_index: int, params: Dict[str, float], raw: Any):
        evidence_calls.append((round_index, dict(params), raw))
        return {"explain_samples": [f"round={round_index} plan"]}

    ranges = {
        "x": ParameterRange(0.0, 1.0, discrete_count=5, priority=1),
        "y": ParameterRange(0.0, 1.0, discrete_count=5, priority=2),
        "unused": ParameterRange(0.0, 1.0, discrete_count=5, priority=3),
    }
    quality_gate = {
        "passed": True,
        "selected_quality": {
            "selected_evaluation": {
                "ranking_prefixes": {
                    "top10": {"passed": True},
                    "top15": {"passed": True},
                }
            }
        },
    }
    optimizer = DoubleLoopGridOptimizer(
        estimated,
        real,
        {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        outer_round_evidence_func=capture_evidence,
        workload_pruner=prune,
        real_score_func=resource_score,
        outer_candidate_acceptance_func=lambda *_args: {
            "accepted": True,
            "reason": "test_accept",
        },
        preflight_quality_gate=quality_gate,
        initial_resource_error_bounds={
            name: {"lower_delta": 0.0, "upper_delta": 0.0, "sample_count": 15}
            for name in ("cpu", "memory", "io")
        },
        grid_config=GridInnerLoopConfig(
            max_workers=2,
            max_passes=4,
            no_improvement_patience=1,
            combination_probe_enabled=True,
            combination_max_size=2,
            combination_values_per_param=3,
            combination_max_candidates=0,
        ),
        outer_config=OuterLoopConfig(
            max_rounds=2,
            real_validation_top_k=0,
            adaptive_topk_min_k=2,
            adaptive_topk_batch_size=1,
            epsilon=0.20,
            target_resource="memory",
        ),
        verbose=False,
    )
    assert optimizer.outer_config.real_validation_top_k == 5
    assert optimizer.outer_config.adaptive_topk_min_k == 2
    capped_config = OuterLoopConfig(
        real_validation_top_k=30,
        adaptive_topk_min_k=16,
    )
    assert capped_config.real_validation_top_k == 5
    assert capped_config.adaptive_topk_min_k == 5
    with tempfile.TemporaryDirectory() as temp_dir:
        result = optimizer.optimize(
            {"x": 0.0, "y": 1.0, "unused": 0.5},
            ranges,
            None,
            candidate_params=["x", "y", "unused"],
            initial_real_resource={
                "resource": {
                    "cpu": 0.0,
                    "memory": 1.0,
                    "io": 0.5,
                    "db_qps": 100.0,
                }
            },
            log_path=str(Path(temp_dir) / "optimizer.jsonl"),
            inner_iteration_log_path=str(Path(temp_dir) / "grid.jsonl"),
        )

    assert result.active_params == ["x", "y"]
    assert result.pruned_params == ["unused"]
    assert result.rounds
    assert all(len(round_item.validated_points) <= 5 for round_item in result.rounds)
    assert len(evidence_calls) == len(result.rounds)
    assert all(
        outer_round.small_loop_best is not None
        and outer_round.small_loop_best.raw_result["explain_samples"]
        for outer_round in result.rounds
    )
    assert result.real_validation_count == len(real_calls)
    assert any(outer_round.outer_accepted for outer_round in result.rounds)
    for outer_round in result.rounds:
        assert outer_round.subset_count == len(outer_round.subset_runs)
        assert outer_round.subset_count > len(result.active_params)
        if outer_round.outer_accepted:
            assert outer_round.binary_shrink_subset == ("x", "y")
            for name in result.active_params:
                assert math.isclose(
                    outer_round.ranges_after[name].width,
                    outer_round.ranges_before[name].width / 2.0,
                )
        else:
            assert outer_round.binary_shrink_subset == tuple()
            assert outer_round.ranges_after == outer_round.ranges_before
    assert result.optimized_params["unused"] == 0.5
    assert abs(result.optimized_params["x"] - 0.7) <= 0.3
    assert abs(result.optimized_params["y"] - 0.3) <= 0.3

    rejected_calls = []

    def reject_estimate(_sql_set: Any, params: Dict[str, float]) -> Dict[str, Any]:
        score = float(params["x"])
        return {
            "resource": {"cpu": score, "memory": 0.5, "io": 0.5, "db_qps": 1.0},
            "score": score,
            "estimated_score": score,
        }

    def reject_real(_sql_set: Any, params: Dict[str, float]) -> Dict[str, Any]:
        rejected_calls.append(dict(params))
        score = float(params["x"])
        return {
            "resource": {"cpu": score, "memory": 0.5, "io": 0.5, "db_qps": 1.0},
            "score": score,
        }

    reject_optimizer = DoubleLoopGridOptimizer(
        reject_estimate,
        reject_real,
        {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        outer_candidate_acceptance_func=lambda *_args: {
            "accepted": False,
            "reason": "test_reject",
        },
        grid_config=GridInnerLoopConfig(
            max_workers=2,
            max_passes=2,
            no_improvement_patience=1,
            combination_probe_enabled=False,
        ),
        outer_config=OuterLoopConfig(
            max_rounds=2,
            real_validation_top_k=1,
            topk_controller="fixed",
            epsilon=0.01,
        ),
        verbose=False,
    )
    reject_result = reject_optimizer.optimize(
        {"x": 0.0},
        {"x": ParameterRange(0.0, 1.0, discrete_count=3)},
        None,
        initial_real_resource={
            "resource": {"cpu": 0.0, "memory": 0.5, "io": 0.5, "db_qps": 1.0},
            "score": 0.0,
        },
    )
    rejected_best = [
        round_item.small_loop_best.params["x"]
        for round_item in reject_result.rounds
        if round_item.small_loop_best is not None
    ]
    assert len(rejected_best) == 2
    assert rejected_best[0] != rejected_best[1]

    explicit = ParameterRange(0.0, 1.0, values=(0.0, 0.2, 0.9, 1.0))
    assert optimizer.discretize(explicit, include_value=0.5) == [0.0, 0.2, 0.5, 0.9, 1.0]

    wide_positive = ParameterRange(1e-6, 1.0, discrete_count=15)
    logarithmic_values = optimizer.discretize(wide_positive)
    assert len(logarithmic_values) == 15
    assert math.isclose(logarithmic_values[0], 1e-6, rel_tol=0.0, abs_tol=1e-15)
    assert math.isclose(logarithmic_values[-1], 1.0, rel_tol=0.0, abs_tol=1e-15)
    assert math.isclose(logarithmic_values[7], 0.001, rel_tol=0.0, abs_tol=1e-15)
    assert math.isclose(
        logarithmic_values[2] / logarithmic_values[1],
        logarithmic_values[1] / logarithmic_values[0],
        rel_tol=1e-9,
        abs_tol=1e-15,
    )

    metadata_range = ParameterRange(
        0.0,
        10.0,
        step=0.5,
        discrete_count=19,
        priority=7,
        values=(0.0, 2.0, 5.0, 8.0, 10.0),
        transform="linear",
    )
    shrunk = optimizer._binary_shrink(
        {"x": metadata_range},
        {"x": 8.0},
        ["x"],
    )["x"]
    assert shrunk.min_value == 5.0
    assert shrunk.max_value == 10.0
    assert shrunk.step == metadata_range.step
    assert shrunk.discrete_count == metadata_range.discrete_count
    assert shrunk.priority == metadata_range.priority
    assert shrunk.values == metadata_range.values
    assert shrunk.transform == metadata_range.transform

    log_range = ParameterRange(1.0, 100.0, transform="log")
    log_lower = optimizer._binary_shrink(
        {"x": log_range},
        {"x": 10.0},
        ["x"],
    )["x"]
    assert math.isclose(log_lower.min_value, 1.0)
    assert math.isclose(log_lower.max_value, 10.0)
    log_upper = optimizer._binary_shrink(
        {"x": log_range},
        {"x": 11.0},
        ["x"],
    )["x"]
    assert math.isclose(log_upper.min_value, 10.0)
    assert math.isclose(log_upper.max_value, 100.0)
    assert log_upper.transform == "log"
    try:
        ParameterRange(0.0, 100.0, transform="log")
    except ValueError:
        pass
    else:
        raise AssertionError("log transform must reject non-positive lower bounds")

    # The common outer rule compares against the current incumbent, not the
    # initial baseline, and score is not an acceptance condition.
    rule_optimizer = DoubleLoopGridOptimizer(
        estimated,
        real,
        {"cpu": 1.0, "memory": 2.0, "io": 1.0},
        outer_config=OuterLoopConfig(target_resource="memory"),
        verbose=False,
    )

    def parsed(
        qps: float,
        memory: float,
        score: float = 0.0,
        *,
        cpu: float = 0.5,
        io: float = 0.5,
    ):
        return rule_optimizer._parse_evaluation(
            {
                "resource": {
                    "db_qps": qps,
                    "cpu": cpu,
                    "memory": memory,
                    "io": io,
                },
                "score": score,
            },
            None,
        )

    initial = parsed(100.0, 1.6, score=10.0)
    current = parsed(110.0, 1.4, score=5.0)
    accepted, reason, details = rule_optimizer._outer_decide(current, initial)
    assert accepted
    assert reason == "db_qps_threshold_met_target_bottleneck_lower_non_targets_safe"
    assert details["hard_gate_passed"]
    assert math.isclose(details["candidate_target_utilization"], 0.7)

    shifted_bottleneck = parsed(111.0, 1.2, cpu=0.85)
    accepted, reason, details = rule_optimizer._outer_decide(
        shifted_bottleneck,
        current,
    )
    assert not accepted
    assert reason == "non_target_resource_became_bottleneck"
    assert details["non_target_bottleneck_resources"] == ["cpu"]

    # This point beats the initial baseline but must lose against current.
    stale_baseline_winner = parsed(105.0, 1.2, score=20.0)
    accepted, reason, _details = rule_optimizer._outer_decide(
        stale_baseline_winner,
        current,
    )
    assert not accepted
    assert reason == "db_qps_not_strictly_higher"
    assert not rule_optimizer._outer_decide(parsed(110.0, 1.2), current)[0]
    assert not rule_optimizer._outer_decide(parsed(111.0, 1.4), current)[0]

    hard_gate_rejector = DoubleLoopGridOptimizer(
        estimated,
        real,
        {"cpu": 1.0, "memory": 2.0, "io": 1.0},
        outer_candidate_acceptance_func=lambda *_args: {
            "accepted": False,
            "reason": "hard_gate_failed",
        },
        outer_config=OuterLoopConfig(target_resource="memory"),
        verbose=False,
    )
    accepted, reason, details = hard_gate_rejector._outer_decide(current, initial)
    assert not accepted
    assert reason == "hard_gate_failed"
    assert not details["hard_gate_passed"]

    soft_start_optimizer = DoubleLoopGridOptimizer(
        estimated,
        real,
        {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        preflight_quality_gate={
            "passed": False,
            "reason": "test_soft_preflight_failure",
        },
        verbose=False,
    )
    assert (
        soft_start_optimizer.preflight_quality_gate_soft_failure_reason
        == "test_soft_preflight_failure"
    )

    def tied_estimate(_sql_set: Any, params: Dict[str, float]) -> Dict[str, Any]:
        novelty = 0.0 if math.isclose(params["x"], 0.5) else 0.25
        return {
            "resource": {
                "cpu": 0.5,
                "memory": 0.5,
                "io": 0.5,
                "db_qps": 1.0 if novelty == 0.0 else 1000.0,
            },
            "score": 0.75,
            "estimated_score": 0.75,
            "model": {
                "novelty": novelty,
                "outside_empirical_support": novelty > 0.1,
            },
        }

    tie_optimizer = DoubleLoopGridOptimizer(
        tied_estimate,
        real,
        {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        verbose=False,
    )
    tie_ranges = {"x": ParameterRange(0.0, 1.0, discrete_count=5)}
    extrapolated = tie_optimizer._point_from_evaluation(
        params={"x": 0.75},
        evaluation=tie_optimizer._estimate(None, {"x": 0.75}),
        common_start={"x": 0.0},
        ranges=tie_ranges,
        source_subset=("x",),
        point_id="extrapolated",
        pass_index=0,
    )
    exact = tie_optimizer._point_from_evaluation(
        params={"x": 0.5},
        evaluation=tie_optimizer._estimate(None, {"x": 0.5}),
        common_start={"x": 0.0},
        ranges=tie_ranges,
        source_subset=("x",),
        point_id="exact",
        pass_index=0,
    )
    tied_ranked = tie_optimizer._deduplicate_and_rank([extrapolated, exact])
    assert tied_ranked[0].subset_id == "exact"
    assert tied_ranked[0].ranking_tiebreak_details["exact_measured_support"]
    assert tied_ranked[0].estimated_score == tied_ranked[1].estimated_score

    seed_optimizer = DoubleLoopGridOptimizer(
        estimated,
        real,
        {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        candidate_seed_points=[{"x": 0.25, "y": 0.75}],
        grid_config=GridInnerLoopConfig(
            max_workers=2,
            max_passes=1,
            no_improvement_patience=1,
            combination_probe_enabled=False,
        ),
        verbose=False,
    )
    seeded_points, seeded_audit = seed_optimizer._grid_small_loop(
        None,
        {"x": 0.0, "y": 1.0},
        ["x", "y"],
        {
            "x": ParameterRange(0.0, 1.0, discrete_count=3),
            "y": ParameterRange(0.0, 1.0, discrete_count=3),
        },
        round_index=0,
        iteration_writer=None,
    )
    assert any(
        math.isclose(point.params["x"], 0.25)
        and math.isclose(point.params["y"], 0.75)
        for point in seeded_points
    )
    assert seeded_audit["calibration_parameter_seed_requested_count"] == 1
    assert seeded_audit["calibration_parameter_seed_usable_count"] == 1
    assert not seeded_audit["calibration_seed_real_result_reused"]

    print("double_loop_grid_optimizer tests passed", flush=True)


if __name__ == "__main__":
    main()
