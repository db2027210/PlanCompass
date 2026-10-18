from __future__ import annotations

import pytest

from scripts import mysql_tpch_client_qps_measurement_v2 as subject


def _row(*, coverage: bool = True, hard_gate: bool = True, client: float = 0.2):
    return {
        "scene": subject.SCENE,
        "db_qps": 4.25,
        "client_qps_audit": client,
        "coverage_complete": coverage,
        "hard_gate_passed": hard_gate,
        "hard_gate_checks": {},
    }


def test_complete_row_remains_strict_and_controls_acceptance() -> None:
    row = subject.rebind_measurements({"tpch_w1": _row()})["tpch_w1"]
    assert row["db_qps"] == 0.2
    assert row["server_db_qps_audit"] == 4.25
    assert row["eligible_for_outer_acceptance"] is True
    assert row["client_qps_used_for_outer_acceptance"] is True


def test_explicit_failed_incomplete_row_preserves_zero_for_classifier() -> None:
    row = subject.rebind_measurements({
        "tpch_w2": _row(coverage=False, hard_gate=False, client=0.0),
    })["tpch_w2"]
    assert row["db_qps"] == 0.0
    assert row["server_db_qps_audit"] == 4.25
    assert row["eligible_for_outer_acceptance"] is False
    assert row["client_qps_used_for_outer_acceptance"] is False


def test_incomplete_row_cannot_expose_positive_client_qps() -> None:
    with pytest.raises(RuntimeError, match="cannot expose positive"):
        subject.rebind_measurements({
            "tpch_w2": _row(coverage=False, hard_gate=False, client=0.1),
        })


def test_complete_row_still_requires_positive_client_qps() -> None:
    with pytest.raises(RuntimeError, match="finite and positive"):
        subject.rebind_measurements({"tpch_w1": _row(client=0.0)})
