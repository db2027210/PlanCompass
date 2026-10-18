from __future__ import annotations

import pytest

from scripts import mysql_tpch_client_qps_measurement_v1 as subject


def _row(*, scene: str = subject.SCENE, coverage: bool = True) -> dict:
    return {
        "scene": scene,
        "db_qps": 4.25,
        "client_qps_audit": 0.20,
        "coverage_complete": coverage,
        "hard_gate_passed": True,
        "hard_gate_checks": {"database_qps_positive": True},
    }


def test_rebinds_outer_metric_and_preserves_server_counter() -> None:
    rebound = subject.rebind_measurements({"tpch_w1": _row()})["tpch_w1"]
    assert rebound["db_qps"] == 0.20
    assert rebound["server_db_qps_audit"] == 4.25
    assert rebound["client_qps_used_for_outer_acceptance"] is True
    assert rebound["server_db_qps_used_for_outer_acceptance"] is False
    assert rebound["qps_metric"] == subject.METRIC


def test_rejects_mixed_scene_or_incomplete_suite() -> None:
    with pytest.raises(RuntimeError, match="non-TPC-H"):
        subject.rebind_measurements({"job_w1": _row(scene="job_memory")})
    with pytest.raises(RuntimeError, match="complete Q1-Q22"):
        subject.rebind_measurements({"tpch_w1": _row(coverage=False)})


def test_contract_prohibits_external_algorithm_prior() -> None:
    contract = subject.contract()
    assert contract["outer_progress_metric"] == subject.METRIC
    assert contract["external_algorithm_evidence_used"] is False
    assert contract["other_scene_measurement_semantics_changed"] is False
