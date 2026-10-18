from __future__ import annotations

import struct
import subprocess

from scripts import run_mysql_four_scene_algorithm_c_v1 as controller


def mysql_float(value: float) -> float:
    return struct.unpack("!f", struct.pack("!f", value))[0]


def test_binary32_optimizer_cost_round_trip_is_accepted() -> None:
    requested = 0.123456789012
    assert controller.optimizer_cost_readback_matches(
        requested, mysql_float(requested)
    )


def test_mysql_six_significant_digit_float_readback_is_accepted() -> None:
    # Regression for formal v7: mysql rendered the stored FLOAT as 0.157739.
    assert controller.optimizer_cost_readback_matches(0.15773933612, 0.157739)


def test_material_optimizer_cost_mismatch_is_rejected() -> None:
    assert not controller.optimizer_cost_readback_matches(0.123456789012, 0.124)


def test_non_finite_optimizer_cost_is_rejected() -> None:
    assert not controller.optimizer_cost_readback_matches(float("nan"), 1.0)
    assert not controller.optimizer_cost_readback_matches(1.0, float("inf"))


def test_json_optimizer_ranges_are_log_shrunk_without_shape_change() -> None:
    ranges = {
        "io_block_read_cost": {"lower": 0.1, "upper": 10.0},
        "row_evaluate_cost": {"lower": 0.01, "upper": 1.0},
    }
    shrunk = controller.shrink_ranges(
        ranges,
        {"io_block_read_cost": 0.5, "row_evaluate_cost": 0.5},
        ["io_block_read_cost", "row_evaluate_cost"],
    )
    assert shrunk == {
        "io_block_read_cost": {"lower": 0.1, "upper": 1.0},
        "row_evaluate_cost": {"lower": 0.1, "upper": 1.0},
    }
    assert ranges["io_block_read_cost"] == {"lower": 0.1, "upper": 10.0}


def test_json_optimizer_range_validation_fails_closed() -> None:
    try:
        controller.shrink_ranges(
            {"io_block_read_cost": {"lower": 10.0, "upper": 0.1}},
            {"io_block_read_cost": 1.0},
            ["io_block_read_cost"],
        )
    except ValueError as error:
        assert "range bounds differ" in str(error)
    else:
        raise AssertionError("invalid optimizer range was accepted")


def ranked_candidate(rank: int, value: float) -> dict:
    return {
        "rank": rank,
        "optimized_params": {
            "io_block_read_cost": value,
            "row_evaluate_cost": 0.1,
        },
    }


def test_rejected_points_are_excluded_from_the_next_top_k() -> None:
    rankings = [ranked_candidate(rank, float(rank)) for rank in range(1, 11)]
    replayed = {
        controller.optimizer_params_key(row["optimized_params"])
        for row in rankings[:5]
    }
    selected, audit = controller.select_unreplayed_candidates(
        rankings, replayed, top_k=5
    )
    assert [row["rank"] for row in selected] == [6, 7, 8, 9, 10]
    assert audit["selected_original_ranks"] == [6, 7, 8, 9, 10]
    assert audit["cycle_reset"] is False


def test_candidate_replay_cycle_resets_only_when_fewer_than_top_k_remain() -> None:
    rankings = [ranked_candidate(rank, float(rank)) for rank in range(1, 8)]
    replayed = {
        controller.optimizer_params_key(row["optimized_params"])
        for row in rankings[:5]
    }
    selected, audit = controller.select_unreplayed_candidates(
        rankings, replayed, top_k=5
    )
    assert [row["rank"] for row in selected] == [1, 2, 3, 4, 5]
    assert audit["cycle_reset"] is True


def test_replay_cycle_never_reselects_permanently_invalid_points() -> None:
    rankings = [ranked_candidate(rank, float(rank)) for rank in range(1, 9)]
    replayed = {
        controller.optimizer_params_key(row["optimized_params"])
        for row in rankings[:5]
    }
    permanently_invalid = {
        controller.optimizer_params_key(row["optimized_params"])
        for row in rankings[:2]
    }
    selected, audit = controller.select_unreplayed_candidates(
        rankings, replayed, top_k=5,
        permanently_invalid_param_keys=permanently_invalid,
    )
    assert [row["rank"] for row in selected] == [3, 4, 5, 6, 7]
    assert audit["cycle_reset"] is True
    assert audit["permanently_invalid_unique_points"] == 2
    assert audit["permanent_invalid_selected"] is False



def test_optimizer_point_identity_is_key_order_independent() -> None:
    assert controller.optimizer_params_key({"b": 2, "a": 1}) == (
        controller.optimizer_params_key({"a": 1.0, "b": 2.0})
    )


def test_duplicate_ranked_points_are_not_selected_twice() -> None:
    rankings = [ranked_candidate(rank, float(rank)) for rank in range(1, 7)]
    rankings.insert(1, ranked_candidate(99, 1.0))
    selected, audit = controller.select_unreplayed_candidates(
        rankings, set(), top_k=5
    )
    assert [row["rank"] for row in selected] == [1, 2, 3, 4, 5]
    assert audit["candidate_pool_rows"] == 7
    assert audit["candidate_pool_unique_points"] == 6


def test_plan_capture_timeout_is_a_candidate_level_failure() -> None:
    def timed_out() -> dict:
        raise subprocess.TimeoutExpired(["mysql", "tpch"], timeout=180)

    plans, failure = controller.capture_plans_fail_closed(
        "tpch_w1", timed_out
    )
    assert plans == {}
    assert failure is not None
    assert failure["task"] == "tpch_w1_plan_capture"
    assert failure["error"].startswith("TimeoutExpired:")


def test_successful_plan_capture_preserves_the_plan() -> None:
    expected = {"q6": {"query_block": {}}}
    plans, failure = controller.capture_plans_fail_closed(
        "tpch_w1", lambda: expected
    )
    assert plans is expected
    assert failure is None


def test_mysql_liveness_fails_closed_when_the_server_probe_errors(monkeypatch) -> None:
    def unavailable(worker: dict, sql: str, **kwargs) -> str:
        raise subprocess.TimeoutExpired(["mysql", "SELECT 1"], timeout=60)

    monkeypatch.setattr(controller, "mysql_root", unavailable)
    assert controller.mysql_server_liveness({"worker_id": "xp_cpu_w1"}) is False


def test_mysql_liveness_requires_positive_readback(monkeypatch) -> None:
    monkeypatch.setattr(
        controller, "mysql_root", lambda worker, sql, **kwargs: "1"
    )
    assert controller.mysql_server_liveness({"worker_id": "xp_cpu_w1"}) is True


def timeout_batch(*, timeout_worker: str = "xp_io_w2") -> dict:
    workers = {
        "job_w1", "job_w2", "tpch_w1", "tpch_w2",
        "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
    }
    return {
        "failures": [{"task": timeout_worker, "error": "TimeoutExpired: bounded"}],
        "measurements": {
            worker_id: {
                "hard_gate_passed": worker_id != timeout_worker,
                "hard_gate_checks": {
                    "coverage_complete": worker_id != timeout_worker,
                    "client_qps_audit_positive": worker_id != timeout_worker,
                },
            }
            for worker_id in workers
        },
    }


def test_only_isolated_xp_timeout_is_retryable() -> None:
    assert controller.retryable_xp_timeout_batch(timeout_batch())
    assert not controller.retryable_xp_timeout_batch(timeout_batch(timeout_worker="job_w1"))


def test_xp_timeout_is_not_retryable_when_another_gate_fails() -> None:
    document = timeout_batch()
    document["measurements"]["job_w1"]["hard_gate_passed"] = False
    assert not controller.retryable_xp_timeout_batch(document)
