from __future__ import annotations

from scripts import mysql_tpch_client_qps_measurement_v1 as metric


def test_successor_metric_is_tpch_client_throughput() -> None:
    contract = metric.contract()
    assert contract["scene"] == "tpch_sf4_memory"
    assert contract["server_db_qps_role"] == "audit_only"
    assert contract["client_qps_role"] == "outer_acceptance_and_incumbent_progress"


def test_a_is_not_an_input_to_successor_metric() -> None:
    assert metric.contract()["external_algorithm_evidence_used"] is False
