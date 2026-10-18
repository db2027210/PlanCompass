#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import experience_table_subset_optimizer as subset_module  # noqa: E402
from experience_table_subset_optimizer import (  # noqa: E402
    AllSubsetOptimizationError,
    IncompleteSubsetOptimizationError,
    optimize_all_experience_table_subsets,
)
from gradient_descent_optimizer import OptimizationResult, ParameterRange  # noqa: E402


def make_result(
    initial_params: Dict[str, float],
    optimized_params: Dict[str, float],
    final_score: float,
) -> OptimizationResult:
    initial_score = 0.0
    return OptimizationResult(
        optimized_params=copy.deepcopy(optimized_params),
        initial_params=copy.deepcopy(initial_params),
        initial_resource={"cpu": 1.0},
        final_resource={"cpu": final_score},
        initial_score=initial_score,
        final_score=final_score,
        score_improvement=final_score - initial_score,
        score_improvement_pct=None,
        initial_utilization={"cpu": 0.01},
        final_utilization={"cpu": final_score / 100.0},
        initial_qps=None,
        final_qps=None,
        qps_improvement=None,
        qps_improvement_pct=None,
        initial_query_count=None,
        final_query_count=None,
        initial_query_time=None,
        final_query_time=None,
        degraded=None,
        degradation_pct=None,
        iterations=1,
        history=[],
    )


def with_fake_optimizer(fake: Any, test_body: Any) -> None:
    original = subset_module.optimize_with_gradient_descent
    subset_module.optimize_with_gradient_descent = fake
    try:
        test_body()
    finally:
        subset_module.optimize_with_gradient_descent = original


def test_subset_count_for_six_and_seven_parameters() -> None:
    six_params = subset_module._canonical_active_params(
        [f"p{index}" for index in reversed(range(6))]
    )
    seven_params = subset_module._canonical_active_params(
        [f"p{index}" for index in reversed(range(7))]
    )
    assert six_params == ("p5", "p4", "p3", "p2", "p1", "p0")
    assert seven_params == ("p6", "p5", "p4", "p3", "p2", "p1", "p0")
    six_subsets = subset_module._all_nonempty_subsets(six_params)
    seven_subsets = subset_module._all_nonempty_subsets(seven_params)

    assert len(six_subsets) == 2**6 - 1 == 63
    assert len(seven_subsets) == 2**7 - 1 == 127
    assert len(set(six_subsets)) == 63
    assert len(set(seven_subsets)) == 127


def test_max_workers_limits_concurrency_without_truncating_subsets() -> None:
    lock = threading.Lock()
    two_workers_entered = threading.Event()
    active_count = 0
    peak_active_count = 0
    completed_subsets: List[Tuple[str, ...]] = []

    def factory(subset: Tuple[str, ...], subset_id: str) -> Any:
        _ = subset_id

        def estimator(sql_set: Any, params: Dict[str, float]) -> Dict[str, float]:
            _ = sql_set, params
            return {"cpu": 1.0}

        estimator.subset = subset
        return estimator

    def fake_optimizer(**kwargs: Any) -> OptimizationResult:
        nonlocal active_count, peak_active_count
        subset = tuple(kwargs["active_params"])
        with lock:
            active_count += 1
            peak_active_count = max(peak_active_count, active_count)
            if active_count >= 2:
                two_workers_entered.set()
        try:
            if not two_workers_entered.wait(timeout=5.0):
                raise AssertionError(" worker  fake optimizer")
            time.sleep(0.02)
            optimized = copy.deepcopy(kwargs["p_real"])
            for name in subset:
                optimized[name] += 1.0
            return make_result(
                kwargs["p_real"],
                optimized,
                float(len(subset)),
            )
        finally:
            with lock:
                active_count -= 1
                completed_subsets.append(subset)

    def body() -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = optimize_all_experience_table_subsets(
                factory,
                {"cpu": 100.0},
                {"a": 1.0, "b": 2.0, "c": 3.0},
                {
                    "a": {"cpu": 1.0},
                    "b": {"cpu": 1.0},
                    "c": {"cpu": 1.0},
                },
                {
                    "a": ParameterRange(0.0, 10.0),
                    "b": ParameterRange(0.0, 10.0),
                    "c": ParameterRange(0.0, 10.0),
                },
                None,
                ["a", "b", "c"],
                2,
                "concurrency",
                temp_dir,
                {"eta": 0.1, "max_iter": 1, "patience": 1, "gamma": 0.5},
            )

            assert peak_active_count == 2
            assert summary.subset_count == 2**3 - 1 == 7
            assert summary.successful_subset_count == 7
            assert summary.failed_subset_count == 0
            assert len(completed_subsets) == 7
            assert len(set(completed_subsets)) == 7
            assert len(summary.audit_records) == 7
            assert all(item["status"] == "success" for item in summary.audit_records)

    with_fake_optimizer(fake_optimizer, body)


def test_seven_parameters_run_exactly_127_subsets_in_parallel() -> None:
    """The formal seven-parameter shape must execute, not just enumerate, 127 tasks."""
    params = [f"p{index}" for index in range(7)]
    initial_params = {name: float(index + 1) for index, name in enumerate(params)}
    lock = threading.Lock()
    active_count = 0
    peak_active_count = 0
    completed_subsets: List[Tuple[str, ...]] = []

    def factory(subset: Tuple[str, ...], subset_id: str) -> Any:
        _ = subset_id

        def estimator(sql_set: Any, values: Dict[str, float]) -> Dict[str, float]:
            _ = sql_set, values
            return {"cpu": 1.0}

        estimator.subset = subset
        return estimator

    def fake_optimizer(**kwargs: Any) -> OptimizationResult:
        nonlocal active_count, peak_active_count
        subset = tuple(kwargs["active_params"])
        with lock:
            active_count += 1
            peak_active_count = max(peak_active_count, active_count)
        try:
            # Release the GIL so the test proves actual overlap between workers.
            time.sleep(0.005)
            optimized = copy.deepcopy(kwargs["p_real"])
            for name in subset:
                optimized[name] += 1.0
            return make_result(kwargs["p_real"], optimized, float(len(subset)))
        finally:
            with lock:
                active_count -= 1
                completed_subsets.append(subset)

    def body() -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = optimize_all_experience_table_subsets(
                factory,
                {"cpu": 100.0},
                initial_params,
                {name: {"cpu": 1.0} for name in params},
                {name: ParameterRange(0.0, 20.0) for name in params},
                None,
                params,
                8,
                "formal-127",
                temp_dir,
                {"eta": 0.1, "max_iter": 1, "patience": 1, "gamma": 0.5},
            )

            assert summary.subset_count == 127
            assert summary.successful_subset_count == 127
            assert summary.failed_subset_count == 0
            assert len(summary.audit_records) == 127
            assert len(completed_subsets) == 127
            assert len(set(completed_subsets)) == 127
            assert 2 <= peak_active_count <= 8

    with_fake_optimizer(fake_optimizer, body)


def test_enumerates_every_subset_with_isolated_state_and_logs() -> None:
    lock = threading.Lock()
    factory_calls: List[Tuple[Tuple[str, ...], str, int]] = []
    optimizer_calls: List[Dict[str, Any]] = []
    retained_inputs: List[Dict[str, float]] = []
    retained_estimators: List[Any] = []
    original_params = {"a": 1.0, "b": 2.0, "c": 3.0, "fixed": 9.0}

    class Estimator:
        def __init__(self, subset: Tuple[str, ...]) -> None:
            self.subset = subset

        def __call__(self, sql_set: Any, params: Dict[str, float]) -> Dict[str, float]:
            _ = sql_set, params
            return {"cpu": 1.0}

    def factory(subset: Tuple[str, ...], subset_id: str) -> Estimator:
        estimator = Estimator(subset)
        with lock:
            retained_estimators.append(estimator)
            factory_calls.append((subset, subset_id, id(estimator)))
        return estimator

    def fake_optimizer(**kwargs: Any) -> OptimizationResult:
        subset = tuple(kwargs["active_params"])
        p_real = kwargs["p_real"]
        p_candidate = kwargs["p_candidate"]
        assert p_real == original_params
        assert p_candidate == original_params
        assert p_real is not p_candidate
        assert kwargs["resource_estimation_func"].subset == subset
        assert kwargs["eta"] == 0.25
        assert kwargs["max_iter"] == 2
        retained_inputs.append(p_real)

        optimized = copy.deepcopy(p_real)
        for name in subset:
            optimized[name] += 1.0
        with lock:
            optimizer_calls.append(
                {
                    "subset": subset,
                    "p_real_id": id(p_real),
                    "log_path": kwargs["log_path"],
                }
            )
        return make_result(p_real, optimized, float(len(subset)))

    def body() -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = optimize_all_experience_table_subsets(
                resource_estimation_factory=factory,
                capability={"cpu": 100.0},
                p_real=original_params,
                sensitivity_table={
                    name: {"cpu": 1.0} for name in ("a", "b", "c")
                },
                param_ranges={
                    name: ParameterRange(0.0, 10.0) for name in ("a", "b", "c")
                },
                sql_set="workload",
                active_params=["c", "a", "b"],
                max_workers=3,
                round_index=4,
                log_dir=temp_dir,
                optimizer_options={
                    "eta": 0.25,
                    "max_iter": 2,
                    "patience": 1,
                    "gamma": 0.5,
                    "verbose": False,
                },
            )

            expected_subsets = [
                ("c",),
                ("a",),
                ("b",),
                ("c", "a"),
                ("c", "b"),
                ("a", "b"),
                ("c", "a", "b"),
            ]
            assert summary.subset_count == 2**3 - 1
            assert [tuple(item["active_subset"]) for item in summary.audit_records] == expected_subsets
            assert summary.successful_subset_count == 7
            assert summary.failed_subset_count == 0
            assert summary.unique_candidate_count == 7
            assert all(item["status"] == "success" for item in summary.audit_records)
            assert summary.top_candidates(20) == summary.candidate_rankings
            assert summary.top_candidates(2) == summary.candidate_rankings[:2]
            assert len(summary.candidate_rankings) == 7
            assert tuple(summary.best_result.best_subset) == ("c", "a", "b")
            assert original_params == {"a": 1.0, "b": 2.0, "c": 3.0, "fixed": 9.0}

            assert len(factory_calls) == 7
            assert len({call[2] for call in factory_calls}) == 7
            assert len(optimizer_calls) == 7
            assert len({call["p_real_id"] for call in optimizer_calls}) == 7
            assert all(
                ranking["optimized_params"]["fixed"] == 9.0
                for ranking in summary.candidate_rankings
            )

            logs = sorted(Path(summary.log_dir).glob("*.jsonl"))
            assert len(logs) == 7
            for log_path in logs:
                records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
                assert records[0]["event"] == "experience_table_subset_start"
                assert records[-1]["event"] in {
                    "experience_table_subset_complete",
                    "experience_table_subset_failed",
                }

    with_fake_optimizer(fake_optimizer, body)


def test_partial_failure_is_fail_closed_after_full_audit() -> None:
    factory_calls: List[Tuple[str, ...]] = []
    lock = threading.Lock()

    def factory(subset: Tuple[str, ...], subset_id: str) -> Any:
        _ = subset_id
        with lock:
            factory_calls.append(subset)
        if subset == ("b",):
            raise RuntimeError("planned single-subset failure")

        def estimator(sql_set: Any, params: Dict[str, float]) -> Dict[str, float]:
            _ = sql_set, params
            return {"cpu": 1.0}

        return estimator

    def fake_optimizer(**kwargs: Any) -> OptimizationResult:
        subset = tuple(kwargs["active_params"])
        optimized = copy.deepcopy(kwargs["p_real"])
        for name in subset:
            optimized[name] += 1.0
        return make_result(kwargs["p_real"], optimized, float(len(subset)))

    def body() -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                optimize_all_experience_table_subsets(
                    factory,
                    {"cpu": 100.0},
                    {"a": 1.0, "b": 2.0, "c": 3.0},
                    {
                        "a": {"cpu": 1.0},
                        "b": {"cpu": 1.0},
                        "c": {"cpu": 1.0},
                    },
                    {
                        "a": ParameterRange(0.0, 10.0),
                        "b": ParameterRange(0.0, 10.0),
                        "c": ParameterRange(0.0, 10.0),
                    },
                    None,
                    ["a", "b", "c"],
                    3,
                    "partial-failure",
                    temp_dir,
                    {"eta": 0.1, "max_iter": 1, "patience": 1, "gamma": 0.5},
                )
            except IncompleteSubsetOptimizationError as exc:
                assert exc.subset_count == 7
                assert exc.successful_subset_count == 6
                assert exc.failed_subset_count == 1
                assert exc.candidate_rankings == []
                assert exc.all_candidate_rankings == []
                assert len(exc.audit_records) == 7
                assert [
                    tuple(item["active_subset"]) for item in exc.audit_records
                ] == [
                    ("a",),
                    ("b",),
                    ("c",),
                    ("a", "b"),
                    ("a", "c"),
                    ("b", "c"),
                    ("a", "b", "c"),
                ]
                failed = [
                    item for item in exc.audit_records if item["status"] == "failed"
                ]
                assert len(failed) == 1
                assert tuple(failed[0]["active_subset"]) == ("b",)
                assert failed[0]["error_type"] == "RuntimeError"
                assert len(factory_calls) == 7
                assert len(set(factory_calls)) == 7
                assert len(list(Path(exc.log_dir).glob("*.jsonl"))) == 7
            else:
                raise AssertionError(" fail-closed")

    with_fake_optimizer(fake_optimizer, body)


def test_calls_real_experience_table_optimizer() -> None:
    def factory(subset: Tuple[str, ...], subset_id: str) -> Any:
        _ = subset, subset_id

        def estimator(sql_set: Any, params: Dict[str, float]) -> Dict[str, float]:
            _ = sql_set
            return {"cpu": float(params["a"])}

        return estimator

    with tempfile.TemporaryDirectory() as temp_dir:
        summary = optimize_all_experience_table_subsets(
            factory,
            {"cpu": 100.0},
            {"a": 1.0, "fixed": 7.0},
            {"a": {"cpu": 1.0}},
            {"a": ParameterRange(0.1, 10.0)},
            None,
            ["a"],
            1,
            "real",
            temp_dir,
            {
                "eta": 0.1,
                "max_iter": 1,
                "patience": 1,
                "gamma": 0.5,
                "verbose": False,
            },
        )
        assert summary.best_result.optimized_params["a"] > 1.0
        assert summary.best_result.optimized_params["fixed"] == 7.0
        assert summary.best_result.history[0]["algorithm"] == "experience_table_analytic_sgd_v1"
        assert summary.inner_algorithm == "experience_table_analytic_sgd_v1"


def test_exact_dedup_and_deterministic_score_then_subset_order() -> None:
    def factory(subset: Tuple[str, ...], subset_id: str) -> Any:
        _ = subset_id

        def estimator(sql_set: Any, params: Dict[str, float]) -> Dict[str, float]:
            _ = sql_set, params
            return {"cpu": 1.0}

        estimator.subset = subset
        return estimator

    def fake_optimizer(**kwargs: Any) -> OptimizationResult:
        subset = tuple(kwargs["active_params"])
        score = 5.0 if subset == ("a",) else 10.0

        return make_result(kwargs["p_real"], kwargs["p_real"], score)

    def body() -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = optimize_all_experience_table_subsets(
                factory,
                {"cpu": 100.0},
                {"a": 1.0, "b": 2.0},
                {"a": {"cpu": 1.0}, "b": {"cpu": 1.0}},
                {
                    "a": ParameterRange(0.0, 10.0),
                    "b": ParameterRange(0.0, 10.0),
                },
                None,
                ["a", "b"],
                2,
                "tie",
                temp_dir,
                {"eta": 0.1, "max_iter": 1, "patience": 1, "gamma": 0.5},
            )

            assert [
                tuple(item["active_subset"]) for item in summary.all_candidate_rankings
            ] == [("b",), ("a", "b"), ("a",)]
            assert len(summary.candidate_rankings) == 1
            assert tuple(summary.candidate_rankings[0]["active_subset"]) == ("b",)
            assert summary.candidate_rankings[0]["final_score"] == 10.0
            assert summary.best_result is not None
            assert summary.best_result.candidate_rankings == summary.candidate_rankings
            assert summary.best_result.subset_count == 3
            assert sorted(summary.candidate_rankings[0]["duplicate_subset_ids"]) == [
                "subset_0001__a",
                "subset_0003__a__b",
            ]

    with_fake_optimizer(fake_optimizer, body)


def test_manifest_order_controls_tie_break_and_subset_ids() -> None:
    def factory(subset: Tuple[str, ...], subset_id: str) -> Any:
        _ = subset, subset_id

        def estimator(sql_set: Any, params: Dict[str, float]) -> Dict[str, float]:
            _ = sql_set, params
            return {"cpu": 1.0}

        return estimator

    def fake_optimizer(**kwargs: Any) -> OptimizationResult:

        return make_result(kwargs["p_real"], kwargs["p_real"], 10.0)

    def body() -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = optimize_all_experience_table_subsets(
                factory,
                {"cpu": 100.0},
                {"z_param": 1.0, "a_param": 2.0},
                {
                    "z_param": {"cpu": 1.0},
                    "a_param": {"cpu": 1.0},
                },
                {
                    "z_param": ParameterRange(0.0, 10.0),
                    "a_param": ParameterRange(0.0, 10.0),
                },
                None,
                ["z_param", "a_param"],
                2,
                "manifest-order",
                temp_dir,
                {"eta": 0.1, "max_iter": 1, "patience": 1, "gamma": 0.5},
            )

            assert [
                tuple(item["active_subset"]) for item in summary.audit_records
            ] == [("z_param",), ("a_param",), ("z_param", "a_param")]
            assert [item["subset_id"] for item in summary.audit_records] == [
                "subset_0001__z_param",
                "subset_0002__a_param",
                "subset_0003__z_param__a_param",
            ]
            assert len(summary.candidate_rankings) == 1
            assert tuple(summary.candidate_rankings[0]["active_subset"]) == (
                "z_param",
            )
            assert summary.best_result.best_subset_id == "subset_0001__z_param"

    with_fake_optimizer(fake_optimizer, body)


def test_equal_score_prefers_lower_predicted_target_utilization() -> None:
    def factory(subset: Tuple[str, ...], subset_id: str) -> Any:
        _ = subset, subset_id
        return lambda sql_set, params: {"io": 1.0}

    def fake_optimizer(**kwargs: Any) -> OptimizationResult:
        subset = tuple(kwargs["active_params"])
        optimized = copy.deepcopy(kwargs["p_real"])
        for name in subset:
            optimized[name] += 1.0
        result = make_result(kwargs["p_real"], optimized, 10.0)
        predicted_io = {
            ("a",): 90.0,
            ("b",): 70.0,
            ("a", "b"): 80.0,
        }[subset]
        result.final_resource = {"cpu": 10.0, "io": predicted_io}
        result.final_utilization = {"cpu": 0.1, "io": predicted_io / 100.0}
        return result

    def body() -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = optimize_all_experience_table_subsets(
                factory,
                {"cpu": 100.0, "io": 100.0},
                {"a": 1.0, "b": 2.0},
                {"a": {"io": 1.0}, "b": {"io": 1.0}},
                {
                    "a": ParameterRange(0.0, 10.0),
                    "b": ParameterRange(0.0, 10.0),
                },
                None,
                ["a", "b"],
                2,
                "target-tie",
                temp_dir,
                {"eta": 0.1, "max_iter": 1, "patience": 1, "gamma": 0.5},
                ranking_target_resource="io",
            )

            assert [
                tuple(item["active_subset"])
                for item in summary.all_candidate_rankings
            ] == [("b",), ("a", "b"), ("a",)]
            assert [
                item["ranking_tiebreak"]["predicted_target_utilization"]
                for item in summary.candidate_rankings
            ] == [0.7, 0.8, 0.9]
            assert all(
                item["ranking_tiebreak"]["applies_only_when_final_score_equal"]
                for item in summary.candidate_rankings
            )

    with_fake_optimizer(fake_optimizer, body)


def test_target_tiebreak_never_overrides_strictly_higher_main_score() -> None:
    def factory(subset: Tuple[str, ...], subset_id: str) -> Any:
        _ = subset, subset_id
        return lambda sql_set, params: {"io": 1.0}

    def fake_optimizer(**kwargs: Any) -> OptimizationResult:
        subset = tuple(kwargs["active_params"])
        optimized = copy.deepcopy(kwargs["p_real"])
        for name in subset:
            optimized[name] += 1.0
        score, predicted_io = {
            ("a",): (10.0 + 1e-12, 99.0),
            ("b",): (10.0, 1.0),
            ("a", "b"): (9.0, 0.0),
        }[subset]
        result = make_result(kwargs["p_real"], optimized, score)
        result.final_resource = {"cpu": score, "io": predicted_io}
        result.final_utilization = {"cpu": score / 100.0, "io": predicted_io / 100.0}
        return result

    def body() -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = optimize_all_experience_table_subsets(
                factory,
                {"cpu": 100.0, "io": 100.0},
                {"a": 1.0, "b": 2.0},
                {"a": {"io": 1.0}, "b": {"io": 1.0}},
                {
                    "a": ParameterRange(0.0, 10.0),
                    "b": ParameterRange(0.0, 10.0),
                },
                None,
                ["a", "b"],
                2,
                "strict-score-before-target-tie",
                temp_dir,
                {"eta": 0.1, "max_iter": 1, "patience": 1, "gamma": 0.5},
                ranking_target_resource="io",
            )

            assert [
                tuple(item["active_subset"])
                for item in summary.candidate_rankings
            ] == [("a",), ("b",), ("a", "b")]

    with_fake_optimizer(fake_optimizer, body)


def test_exact_signature_does_not_round_neighboring_floats() -> None:
    low = 1.0
    high = math.nextafter(low, 2.0)
    assert subset_module._exact_parameter_signature({"p": low}) != (
        subset_module._exact_parameter_signature({"p": high})
    )


def test_pruning_metadata_reaches_public_ranking() -> None:
    def factory(subset: Tuple[str, ...], subset_id: str) -> Any:
        _ = subset, subset_id
        return lambda sql_set, params: {"cpu": 1.0}

    pruning_input = {
        "calibration_ready": True,
        "predicted_db_qps": 101.0,
    }

    def fake_optimizer(**kwargs: Any) -> OptimizationResult:
        result = make_result(kwargs["p_real"], {"a": 2.0}, 2.0)
        result.final_estimation_metadata = {
            "optimistic_replay_pruning_input": copy.deepcopy(pruning_input),
        }
        return result

    def body() -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary = optimize_all_experience_table_subsets(
                factory,
                {"cpu": 100.0},
                {"a": 1.0},
                {"a": {"cpu": 1.0}},
                {"a": ParameterRange(0.0, 10.0)},
                None,
                ["a"],
                1,
                "metadata",
                temp_dir,
                {"eta": 0.1, "max_iter": 1, "patience": 1, "gamma": 0.5},
            )
            ranking = summary.candidate_rankings[0]
            sanitized = {
                "schema_violation": "legacy_qps_bound_key_present",
                "violating_keys": ["predicted_db_qps"],
            }
            assert ranking["optimistic_replay_pruning_input"] == sanitized
            assert ranking["final_estimation_metadata"] == {
                "optimistic_replay_pruning_input": sanitized,
            }

    with_fake_optimizer(fake_optimizer, body)


def test_all_failures_remain_available_for_audit() -> None:
    def failed_factory(subset: Tuple[str, ...], subset_id: str) -> Any:
        raise RuntimeError(f"failed {subset_id}: {subset}")

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            optimize_all_experience_table_subsets(
                failed_factory,
                {"cpu": 100.0},
                {"a": 1.0, "b": 2.0},
                {"a": {"cpu": 1.0}, "b": {"cpu": 1.0}},
                {
                    "a": ParameterRange(0.0, 10.0),
                    "b": ParameterRange(0.0, 10.0),
                },
                None,
                ["a", "b"],
                2,
                1,
                temp_dir,
                {},
            )
        except AllSubsetOptimizationError as exc:
            assert len(exc.audit_records) == 3
            assert all(item["status"] == "failed" for item in exc.audit_records)
            assert exc.candidate_rankings == []
        else:
            raise AssertionError(" AllSubsetOptimizationError")


def test_rejects_legacy_optimizer_options() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            optimize_all_experience_table_subsets(
                lambda subset, subset_id: lambda sql_set, params: {"cpu": 1.0},
                {"cpu": 100.0},
                {"a": 1.0},
                {"a": {"cpu": 1.0}},
                {"a": ParameterRange(0.0, 10.0)},
                None,
                ["a"],
                1,
                1,
                temp_dir,
                {"eta_growth_factor": 2.0},
            )
        except ValueError as exc:
            assert "informal sensitivity-prior option" in str(exc)
        else:
            raise AssertionError("")


def main() -> None:
    test_subset_count_for_six_and_seven_parameters()
    test_max_workers_limits_concurrency_without_truncating_subsets()
    test_seven_parameters_run_exactly_127_subsets_in_parallel()
    test_enumerates_every_subset_with_isolated_state_and_logs()
    test_partial_failure_is_fail_closed_after_full_audit()
    test_calls_real_experience_table_optimizer()
    test_exact_dedup_and_deterministic_score_then_subset_order()
    test_manifest_order_controls_tie_break_and_subset_ids()
    test_equal_score_prefers_lower_predicted_target_utilization()
    test_target_tiebreak_never_overrides_strictly_higher_main_score()
    test_exact_signature_does_not_round_neighboring_floats()
    test_pruning_metadata_reaches_public_ranking()
    test_all_failures_remain_available_for_audit()
    test_rejects_legacy_optimizer_options()
    print("experience-table subset optimizer tests passed", flush=True)


if __name__ == "__main__":
    main()
