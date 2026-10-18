from __future__ import annotations

import copy

import pytest

from scripts import run_mysql_algorithm_a_dynamic_resume_v2 as resume


def row(worker: str, qps: float):
    return {
        "worker_id": worker,
        "db_qps": qps,
        "p95_ms": 10,
        "p99_ms": 20,
        "resource_means": {"cpu": 0.5, "memory": 0.6, "io": 0.7},
        "hard_gate_passed": True,
        "coverage_complete": True,
    }


def test_average_worker_repetitions_requires_two_complete_cohorts():
    first = {f"w{i}": row(f"w{i}", i) for i in range(1, 5)}
    second = copy.deepcopy(first)
    second["w1"]["db_qps"] = 3
    result = resume.average_worker_repetitions([first, second])
    assert set(result) == set(first)
    assert result["w1"]["db_qps"] == 2
    assert result["w1"]["transition_baseline_repetitions"] == 2


def test_average_worker_repetitions_rejects_missing_worker():
    first = {f"w{i}": row(f"w{i}", i) for i in range(1, 5)}
    second = copy.deepcopy(first)
    second.pop("w4")
    with pytest.raises(RuntimeError, match="two full repetitions"):
        resume.average_worker_repetitions([first, second])


def test_average_rows_fails_closed_on_physical_invalid():
    rows = [row("w1", 1), row("w2", 2)]
    rows[0]["hard_gate_passed"] = False
    with pytest.raises(RuntimeError, match="hard gate"):
        resume.average_rows(rows)

