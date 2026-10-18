#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experience_table_subset_optimizer import optimize_all_experience_table_subsets
from gradient_descent_optimizer import ParameterRange
from mysql_parameter_optimizer import FROZEN_MYSQL_V2_ACTIVE, run_optimization


def test_experience_table_sgd_enumerates_exact_nonempty_subsets() -> None:
    def factory(_subset, _subset_id):
        def estimate(_sql, params):
            return {
                "cpu": params["a"] + 2.0 * params["b"],
                "memory": 20.0 - params["a"],
                "io": 10.0 - params["b"],
            }
        return estimate

    with tempfile.TemporaryDirectory() as temp_dir:
        summary = optimize_all_experience_table_subsets(
            resource_estimation_factory=factory,
            capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
            p_real={"a": 1.0, "b": 2.0, "fixed": 99.0},
            sensitivity_table={
                "a": {"cpu": 1.0, "memory": -1.0, "io": 0.0},
                "b": {"cpu": 2.0, "memory": 0.0, "io": -1.0},
            },
            param_ranges={"a": ParameterRange(1.0, 5.0), "b": ParameterRange(2.0, 10.0)},
            sql_set=None,
            active_params=["a", "b"],
            max_workers=2,
            round_index=1,
            log_dir=temp_dir,
            optimizer_options={
                "verbose": False,
                "score_mode": "performance_io",
                "max_iter": 2,
            },
            ranking_target_resource="memory",
        )
    assert summary.subset_count == 2**2 - 1 == 3
    assert summary.inner_algorithm == "experience_table_analytic_sgd_v1"
    assert all(row["optimized_params"]["fixed"] == 99.0 for row in summary.candidate_rankings)


def test_frozen_mysql_v2_subset_counts() -> None:
    freeze = Path(
        "/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/"
        "config/formal/frozen_mysql_workloads_and_parameters_20260722_v2/"
        "mysql_parameter_spaces.json"
    )
    payload = json.loads(freeze.read_text(encoding="utf-8"))
    expected = {"hybench_ap": (8, 255), "job": (4, 15), "tpch": (4, 15), "tpcc": (4, 15), "hybench_xp": (7, 127)}
    for benchmark, (k, subsets) in expected.items():
        active = payload["benchmark_candidate_parameters"][benchmark]
        assert active == FROZEN_MYSQL_V2_ACTIVE[benchmark]
        assert len(active) == k
        assert 2**k - 1 == subsets


def test_random_walk_method_id_is_rejected_before_any_experiment() -> None:
    try:
        run_optimization({}, method_id="random_walk")
    except ValueError as exc:
        assert "experience_table_sgd" in str(exc)
    else:
        raise AssertionError("random_walk must not enter the formal small loop")


if __name__ == "__main__":
    test_experience_table_sgd_enumerates_exact_nonempty_subsets()
    test_frozen_mysql_v2_subset_counts()
    test_random_walk_method_id_is_rejected_before_any_experiment()
    print("experience-table-only formal path tests passed", flush=True)
