from scripts import mysql_outer_qps_strict_progress_v1 as policy


def row(qps, *, valid=True, p95=100.0, cpu=0.9, memory=0.5, io=0.1):
    return {
        "db_qps": qps,
        "p95_ms": p95,
        "hard_gate_passed": valid,
        "resource_means": {"cpu": cpu, "memory": memory, "io": io},
    }


def decide(candidate):
    return policy.decide_qps_strict_progress(
        row(10.0, p95=50.0, cpu=0.8),
        candidate,
        target_resource="cpu",
        # Simulate an old caller's composite latency/resource veto.  The new
        # semantic gate must ignore it and use candidate technical validity.
        hard_gate_passed=False,
        utilization_key="resource_means",
        acceptance_tier="strict_three_level",
    )


def test_higher_qps_is_accepted_despite_latency_and_resource_regression():
    decision = decide(row(10.000001, p95=500.0, cpu=0.99, memory=0.95, io=0.95))
    assert decision.accepted is True
    assert decision.qps_strictly_increased is True
    assert decision.reason == "accepted_qps_strictly_higher_than_last_accepted"


def test_equal_or_lower_qps_is_rejected():
    assert decide(row(10.0)).reason == "db_qps_not_strictly_higher"
    assert decide(row(9.999999)).accepted is False


def test_invalid_measurement_never_enters_semantic_candidate_set():
    decision = decide(row(11.0, valid=False))
    assert decision.accepted is False
    assert decision.reason == "technical_measurement_invalid"


def test_tier_progress_is_audit_only_and_never_downgrades():
    progress = policy.keep_qps_only_mode(
        acceptance_tier="strict_three_level",
        consecutive_failed_rounds=99,
        accepted=False,
    )
    assert progress.acceptance_tier == policy.ACCEPTANCE_MODE
    assert progress.downgraded is False
    assert policy.policy_contract()["algorithm_a_changed"] is False


def test_pg_adapter_uses_same_strict_last_accepted_qps_rule():
    decision = policy.pg_outer_qps_strict_progress(
        row(10.1, valid=True, cpu=0.99, memory=0.99, io=0.99),
        row(10.0, valid=True, cpu=0.5, memory=0.5, io=0.5),
        canonical_baseline=row(1.0),
        target="cpu",
    )
    assert decision["accepted"] is True
    assert decision["qps_reference_db_qps"] == 10.0
    assert decision["minimum_db_qps_gain_ratio"] == 0.0
