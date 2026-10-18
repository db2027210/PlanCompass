import math

import pytest

from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization


def baseline():
    return {
        "status": "PASS",
        "failures": [],
        "measurements": {
            "tpch_w1": {
                "scene": "tpch_sf4_memory", "db_qps": 0.19,
                "hard_gate_passed": True, "coverage_complete": True,
            },
            "tpch_w2": {
                "scene": "tpch_sf4_memory", "db_qps": 0.21,
                "hard_gate_passed": True, "coverage_complete": True,
            },
        },
    }


def test_same_worker_baselines_map_to_same_canonical_qps():
    contract = normalization.baseline_contract(
        baseline(), scene="tpch_sf4_memory",
    )
    assert contract["canonical_baseline_client_qps"] == pytest.approx(0.20)
    for worker_id, raw in (("tpch_w1", 0.19), ("tpch_w2", 0.21)):
        row = {"worker_id": worker_id, "db_qps": raw}
        normalization.normalize_candidate_in_place(row, contract)
        assert row["db_qps"] == pytest.approx(0.20)
        assert row["raw_worker_client_qps"] == raw


def test_relative_improvement_is_worker_invariant():
    contract = normalization.baseline_contract(
        baseline(), scene="tpch_sf4_memory",
    )
    values = []
    for worker_id, raw in (("tpch_w1", 0.19 * 1.05), ("tpch_w2", 0.21 * 1.05)):
        row = {"worker_id": worker_id, "db_qps": raw}
        normalization.normalize_candidate_in_place(row, contract)
        values.append(row["db_qps"])
    assert values[0] == pytest.approx(values[1])
    assert values[0] == pytest.approx(0.21)


def test_invalid_or_unknown_worker_fails_closed():
    contract = normalization.baseline_contract(
        baseline(), scene="tpch_sf4_memory",
    )
    with pytest.raises(RuntimeError):
        normalization.normalize_candidate_in_place(
            {"worker_id": "tpch_w3", "db_qps": 0.2}, contract,
        )
    broken = baseline()
    broken["measurements"]["tpch_w1"]["db_qps"] = math.nan
    with pytest.raises(RuntimeError):
        normalization.baseline_contract(broken, scene="tpch_sf4_memory")
