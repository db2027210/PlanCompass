from __future__ import annotations

from scripts import mysql_algorithm_b_pg_outer_policy_v36 as policy


def baseline(qps: float) -> dict:
    return {
        "db_qps": qps,
        "p95_ms": 10.0,
        "p99_ms": 20.0,
        "resource_means": {"cpu": 0.50, "memory": 0.70, "io": 0.30},
    }


def candidate(qps: float) -> dict:
    return {
        "worker_id": "w1",
        "candidate_ranking": {
            "rank": 1,
            "optimized_params": {"io_block_read_cost": 1.1},
            "active_subset": ["io_block_read_cost"],
        },
        "db_qps": qps,
        "p95_ms": 10.0,
        "p99_ms": 20.0,
        "resource_means": {"cpu": 0.49, "memory": 0.72, "io": 0.29},
        "hard_gate_passed": True,
        "coverage_complete": True,
    }


def test_recovery_drift_uses_replayed_incumbent_not_stale_canonical() -> None:
    canonical = baseline(100.0)
    incumbent = baseline(65.0)

    chosen, evaluations = policy.evaluate_batch(
        [candidate(70.0)], incumbent,
        canonical_baseline=canonical,
        target="memory",
    )

    assert chosen is not None
    decision = evaluations[0]["decision"]
    assert decision["accepted"] is True
    assert decision["qps_reference_mode"] == "last_accepted"
    assert decision["qps_reference_db_qps"] == 65.0
    assert decision["qps_gate_gain"] == 70.0 / 65.0 - 1.0
    assert abs(decision["qps_gain_vs_canonical_baseline"] + 0.30) < 1e-12
    assert decision["canonical_baseline_db_qps"] == 100.0


def test_first_round_still_uses_canonical_when_it_is_the_incumbent() -> None:
    canonical = baseline(100.0)
    chosen, evaluations = policy.evaluate_batch(
        [candidate(101.0)], canonical,
        canonical_baseline=canonical,
        target="memory",
    )
    assert chosen is not None
    assert evaluations[0]["decision"]["qps_reference_db_qps"] == 100.0


def test_policy_contract_freezes_first_then_last_accepted_reference() -> None:
    contract = policy.policy_contract()
    assert contract["schema_version"] == "mysql_algorithm_b_pg_outer_policy_v36"
    assert contract["qps_reference_mode"] == "last_accepted"
    assert contract["first_round_qps_reference"] == "canonical_baseline"
    assert contract["subsequent_round_qps_reference"] == "incumbent_validation"
    assert contract["recovery_or_worker_transition_reference"] == (
        "freshly_replayed_incumbent_validation"
    )
    assert contract["incumbent_reference_replayed_every_round"] is False
    assert contract[
        "incumbent_reference_refresh_after_consecutive_rejections"
    ] == 2
    assert contract["incumbent_reference_refresh_effect"] == (
        "reference_only_no_acceptance_no_range_shrink"
    )
