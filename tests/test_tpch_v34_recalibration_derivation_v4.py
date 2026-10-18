import json

import pytest

from shell import build_tpch_v34_debug_bundle as builder


def test_pre_interference_diagnostic_projects_cpu_into_target_band():
    evidence = builder.diagnostic_demand_evidence()

    assert evidence["valid_r0"] is False
    assert evidence["accepted_round_authorized"] is False
    assert evidence["window"]["resource_sample_count"] == 462
    assert evidence["observed_utilization"] == pytest.approx({
        "cpu": 0.2450616546302356,
        "memory": 0.899551846802987,
        "io": 0.45057753143752005,
    })
    assert evidence["client_completion"] == {
        "sample_count": 458,
        "coverage_ratio": 458 / 462,
        "completed_total": 1,
    }
    assert evidence["measured_cpu_demand_cores"] == pytest.approx(
        0.2450616546302356 * 6.5
    )
    assert evidence["new_cpu_quota_cores"] == 4.0
    assert 0.30 < evidence["projected_cpu_utilization"] < 0.50
    assert evidence["new_io_bps_per_direction"] == 100_663_296
    for sampler in evidence["database_counter"]["samplers"].values():
        assert sampler["coverage_ratio"] >= 0.90
        assert sampler["maximum_gap_seconds"] <= 10.0
        assert sampler["first_completed_total"] == 43
        assert sampler["last_completed_total"] == 60


def test_recalibrated_config_preserves_algorithm_c_search_contract():
    payload = builder.build_config(builder.BASE_SOURCE)
    config = json.loads(payload)

    assert config["workload"]["v34_resource_contract"] == {
        "cpu_quota_cores": 4.0,
        "derivation":
            "pre_interference_measured_cpu_demand_recalibration_v4",
        "io_read_bps": 100_663_296,
        "io_write_bps": 100_663_296,
        "terminals": 4,
    }
    assert config["optimizer"]["original_gradient_only"] is True
    assert config["optimizer"]["candidate_generator"] == (
        "all_nonempty_subsets_experience_table_analytic_sgd_v1"
    )
    assert len(config["cost_parameters"]["active"]) == 4
    protocol = config["formal_outer_protocol"]
    assert protocol["formal_search_initial_replay_top_k"] == 5
    assert protocol["formal_search_max_replay_top_k"] == 5
    assert protocol["target_accepted_shrinks"] == 10
