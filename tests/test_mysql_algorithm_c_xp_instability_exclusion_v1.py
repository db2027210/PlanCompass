from scripts.mysql_algorithm_c_xp_instability_exclusion_v1 import classify


def row(*, valid: bool):
    return {
        "scene": "hybench_xp_io",
        "coverage_complete": True,
        "hard_gate_passed": valid,
        "hard_gate_checks": {
            "coverage_complete": True,
            "target_resource_stability_passed": valid,
        },
        "resource_stability": {
            "schema_version": "mysql_xp_adaptive_stop_v1",
            "stable": valid,
            "selected_measurement_seconds": 780 if valid else 900,
            "maximum_measurement_seconds": 900,
            "stop_request_published": valid,
        },
    }


def test_one_unstable_candidate_is_excluded_and_valid_sibling_retained():
    result = classify(
        {"status": "FAILED_CLOSED", "failures": [], "measurements": {
            "xp_io_w1": row(valid=True), "xp_io_w2": row(valid=False),
        }},
        {"xp_io_w1": {"rank": 1}, "xp_io_w2": {"rank": 2}},
    )
    assert result["failed_worker_ids"] == ["xp_io_w2"]
    assert result["accepted_state_credit_increment"] == 0
    assert result["pruning_evidence_eligible"] is False


def test_non_stability_failure_remains_unclassified():
    invalid = row(valid=False)
    invalid["hard_gate_checks"]["coverage_complete"] = False
    assert classify(
        {"status": "FAILED_CLOSED", "failures": [], "measurements": {
            "xp_io_w1": invalid,
        }},
        {"xp_io_w1": {"rank": 1}},
    ) is None

