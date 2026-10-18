from __future__ import annotations

import pytest

from scripts import run_mysql_algorithm_a_async_scene_v3 as algorithm_a


@pytest.mark.parametrize(
    "scene,active_count,candidate_count",
    [
        ("job_memory", 4, 16),
        ("tpch_sf4_memory", 4, 16),
        ("hybench_xp_cpu", 7, 128),
        ("hybench_xp_io", 7, 128),
    ],
)
def test_v4_freeze_and_candidate_cardinality(
    scene: str, active_count: int, candidate_count: int,
) -> None:
    active, _ranges = algorithm_a.scene_parameter_space(scene)
    contract = algorithm_a.validate_a_contract(scene, active)
    flow = contract["postgresql_algorithm_a_flow"]
    assert len(active) == active_count
    assert contract["scenes"][scene]["candidates_per_round"] == candidate_count
    assert flow["qps_reference_mode"] == "last_accepted"
    assert flow["first_round_qps_reference"] == "canonical_baseline"
    assert flow["recovery_or_worker_transition_reference"] == (
        "freshly_replayed_incumbent_validation"
    )
    assert flow["incumbent_reference_replayed_every_round"] is False
    assert flow[
        "incumbent_reference_refresh_after_consecutive_rejections"
    ] == 2
    assert flow["partially_failed_wave_valid_sibling_retained"] is True
    assert contract["runtime_resource_freeze_authority"][
        "algorithm_flow_changed"
    ] is True


def test_fresh_state_records_qps_progress_contract() -> None:
    active, ranges = algorithm_a.scene_parameter_space("job_memory")
    state = algorithm_a.fresh_state(
        run_id="test", scene="job_memory", active=active, ranges=ranges,
        authority={},
    )
    assert state["qps_reference_mode"] == "last_accepted"
    assert state["first_round_qps_reference"] == "canonical_baseline"
    assert state["recovery_or_worker_transition_reference"] == (
        "freshly_replayed_incumbent_validation"
    )
    assert state["consecutive_rejected_rounds"] == 0
    assert state["incumbent_reference_refresh_count"] == 0
    assert state[
        "incumbent_reference_refresh_after_consecutive_rejections"
    ] == 2


def test_incumbent_reference_refresh_only_after_two_consecutive_rejections() -> None:
    assert algorithm_a.incumbent_reference_refresh_due({
        "consecutive_rejected_rounds": 1,
        "rejected_rounds": 1,
    }) is False
    assert algorithm_a.incumbent_reference_refresh_due({
        "consecutive_rejected_rounds": 2,
        "rejected_rounds": 2,
    }) is True
    assert algorithm_a.incumbent_reference_refresh_due({
        "consecutive_rejected_rounds": 2,
        "rejected_rounds": 10,
    }) is False


def test_incumbent_reference_aggregate_preserves_physical_gate() -> None:
    rows = [
        {
            "db_qps": 10.0,
            "p95_ms": 2.0,
            "p99_ms": 4.0,
            "resource_means": {"cpu": 0.4, "memory": 0.5, "io": 0.6},
            "hard_gate_passed": True,
            "coverage_complete": True,
        },
        {
            "db_qps": 12.0,
            "p95_ms": 4.0,
            "p99_ms": 6.0,
            "resource_means": {"cpu": 0.6, "memory": 0.7, "io": 0.8},
            "hard_gate_passed": True,
            "coverage_complete": True,
        },
    ]
    aggregate = algorithm_a.aggregate_incumbent_reference(rows)
    assert aggregate["db_qps"] == 11.0
    assert aggregate["resource_means"] == {
        "cpu": 0.5, "memory": 0.6, "io": 0.7,
    }


def _candidate_row(rank: int, *, valid: bool) -> dict:
    return {
        "rank": rank,
        "db_qps": 10.0 + rank,
        "p95_ms": 2.0,
        "p99_ms": 4.0,
        "resource_means": {"cpu": 0.4, "memory": 0.5, "io": 0.6},
        "hard_gate_passed": valid,
        "coverage_complete": True,
    }


def test_partially_failed_wave_retains_valid_sibling_candidate() -> None:
    assignments = {
        "job_w1": {"rank": 11, "params": {}},
        "job_w2": {"rank": 12, "params": {}},
    }
    baseline = {
        worker_id: _candidate_row(index, valid=True)
        for index, worker_id in enumerate(assignments, start=1)
    }
    canonical = {
        "db_qps": 10.0,
        "p95_ms": 2.0,
        "p99_ms": 4.0,
        "resource_means": {"cpu": 0.4, "memory": 0.5, "io": 0.6},
    }
    candidates = [
        {"optimized_params": {}, "algorithm_a_factor_levels": {}}
        for _ in range(16)
    ]
    terminal = {
        "status": "FAILED_CLOSED",
        "failures": [],
        "measurements": {
            "job_w1": _candidate_row(11, valid=True),
            "job_w2": _candidate_row(12, valid=False),
        },
    }

    retained, valid_ranks, technical_ranks = (
        algorithm_a.retain_valid_wave_candidates(
            terminal_document=terminal,
            measured={},
            assignments=assignments,
            baseline_rows=baseline,
            canonical=canonical,
            candidates=candidates,
        )
    )

    assert valid_ranks == [11]
    assert technical_ranks == [12]
    assert [row["rank"] for row in retained] == [11]
    assert retained[0]["candidate_ranking"] == candidates[10]
