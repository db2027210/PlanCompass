from __future__ import annotations

import math

from scripts import mysql_algorithm_c_pg_recovery_v1 as recovery
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v56 as runtime


ACTIVE = ("a", "b")
RANGES = {
    "a": {"lower": 1.0, "upper": 100.0},
    "b": {"lower": 1.0, "upper": 100.0},
}


def _row(*, params, qps_gain, target_drop, bottlenecks, rank):
    return {
        "rank": rank,
        "worker_id": f"w{rank}",
        "params": params,
        "resource_means": {
            "cpu": 0.50,
            "memory": 0.50 if not bottlenecks else 0.90,
            "io": 0.40,
        },
        "candidate_ranking": {"rank": rank, "optimized_params": params},
        "outer_decision": {
            "qps_gate_gain": qps_gain,
            "minimum_db_qps_gain_ratio": 0.0,
            "target_drop_vs_reference": target_drop,
            "candidate_non_target_bottlenecks": bottlenecks,
            "hard_gate_passed": not bottlenecks,
        },
    }


def test_measured_safe_unsafe_pair_generates_log_space_interior_points():
    rows = [
        _row(params={"a": 4.0, "b": 4.0}, qps_gain=-0.01,
             target_drop=0.10, bottlenecks=[], rank=1),
        _row(params={"a": 64.0, "b": 64.0}, qps_gain=0.10,
             target_drop=0.10, bottlenecks=["memory"], rank=2),
    ]
    observations = recovery.record_outer_observations(
        [], rows=rows, target="cpu", active=ACTIVE, outer_attempt=2,
    )
    pair = recovery.outer_boundary_pair(
        observations, ranges=RANGES, active=ACTIVE,
    )
    assert pair is not None
    vectors, generated, audit = recovery.outer_boundary_vectors(
        pair, active=ACTIVE, limit=5, generated_ids=[],
    )
    assert len(vectors) == 5
    assert len(generated) == 5
    assert audit["qps_used_for_inner_scoring"] is False
    assert all(4.0 < row["a"] < 64.0 and 4.0 < row["b"] < 64.0 for row in vectors)


def test_progressive_numeric_windows_match_pg_radius_ladder():
    incumbent = {"a": 10.0, "b": 10.0}
    audits = []
    for index, radius in enumerate(recovery.NUMERIC_RADII):
        vectors, audit = recovery.progressive_numeric_vectors(
            incumbent=incumbent,
            ranges=RANGES,
            active=ACTIVE,
            window_index=index,
            count=6,
        )
        assert vectors
        assert all(
            RANGES[name]["lower"] <= row[name] <= RANGES[name]["upper"]
            for row in vectors for name in ACTIVE
        )
        assert math.isclose(audit["normalized_radius"], radius)
        audits.append(audit)
    assert [row["normalized_radius"] for row in audits] == list(recovery.NUMERIC_RADII)


def test_second_valid_miss_refines_one_bracket_then_bisects():
    observations = recovery.record_outer_observations(
        [],
        rows=[
            _row(params={"a": 4.0, "b": 4.0}, qps_gain=-0.01,
                 target_drop=0.10, bottlenecks=[], rank=1),
            _row(params={"a": 64.0, "b": 64.0}, qps_gain=0.10,
                 target_drop=0.10, bottlenecks=["memory"], rank=2),
        ],
        target="cpu",
        active=ACTIVE,
        outer_attempt=2,
    )
    unchanged, progress, pending = recovery.advance_outer_decision(
        accepted=False,
        consecutive_rejections=1,
        ranges=RANGES,
        initial_ranges=RANGES,
        incumbent_params={"a": 10.0, "b": 10.0},
        active=ACTIVE,
        observations=observations,
        boundary_attempts_since_shrink=0,
    )
    assert unchanged == RANGES
    assert pending is True
    assert progress["consecutive_rejections_after"] == 2
    assert progress["forced_incumbent_shrink"] is False

    shrunk, progress, pending = recovery.advance_outer_decision(
        accepted=False,
        consecutive_rejections=2,
        ranges=unchanged,
        initial_ranges=RANGES,
        incumbent_params={"a": 10.0, "b": 10.0},
        active=ACTIVE,
        observations=observations,
        boundary_attempts_since_shrink=1,
    )
    assert pending is False
    assert progress["forced_incumbent_shrink"] is True
    assert all(
        math.isclose(
            math.log(shrunk[name]["upper"] / shrunk[name]["lower"]),
            0.5 * math.log(RANGES[name]["upper"] / RANGES[name]["lower"]),
        )
        for name in ACTIVE
    )


def test_expanded_runtime_has_all_pg_recovery_guards():
    source = runtime._source
    assert "EMPTY_ADMISSION_RECOVERY" in source
    assert "numeric_windows_exhausted_without_physical_evidence" in source
    assert "pg_recovery.advance_outer_decision" in source
    assert '"algorithm_c_outer_boundary_pending"' in source
    assert "if not accepted and parent_ranges is not None:" in source
    assert "post_search_stability_confirmation\"] = False" in source
    assert "candidate_space_exhausted\"][scene] = True" not in source


def test_recovery_contract_prohibits_false_convergence_and_inner_qps():
    contract = recovery.policy_contract()
    assert contract["maximum_outer_bracket_attempts_per_interval"] == 1
    assert contract["numeric_window_radii"] == [0.05, 0.10, 0.20, 0.40, 0.70, 1.0]
    assert contract["empty_admission_counts_as_physical_miss"] is False
    assert contract["numeric_windows_exhausted_is_convergence"] is False
    assert contract["qps_used_for_inner_scoring"] is False
