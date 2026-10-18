from pathlib import Path

from scripts import mysql_job_cache_preconditioned_measurement_v1 as subject


def test_non_job_delegates_without_precondition(monkeypatch, tmp_path: Path):
    called = []
    monkeypatch.setattr(subject, "_validate_contract", lambda: {"release_version": "v1"})
    monkeypatch.setattr(
        subject.predecessor, "measure_batch",
        lambda *args, **kwargs: called.append((args, kwargs)) or {"tpch_w1": {}},
    )
    monkeypatch.setattr(subject.alg, "load", lambda path: {})
    written = {}
    monkeypatch.setattr(subject.alg, "atomic", lambda path, value: written.update(value))
    monkeypatch.setattr(subject, "_sha", lambda path: "sha")
    result = subject.measure_batch(
        {}, {"tpch_w1": {"scene": "tpch_sf4_memory"}},
        {"tpch_w1": {"rank": 1, "params": {}}}, tmp_path / "point", "token",
    )
    assert result == {"tpch_w1": {}}
    assert len(called) == 1
    assert written["job_cache_precondition"] is None


def test_job_preconditions_before_scored_measurement(monkeypatch, tmp_path: Path):
    order = []
    precondition = {"path": "precondition.json", "sha256": "pre-sha"}
    monkeypatch.setattr(subject, "_validate_contract", lambda: {"release_version": "v1"})
    monkeypatch.setattr(
        subject, "_precondition",
        lambda *args, **kwargs: order.append("precondition") or precondition,
    )
    monkeypatch.setattr(
        subject.predecessor, "measure_batch",
        lambda *args, **kwargs: order.append("scored") or {"job_w1": {"db_qps": 1}},
    )
    monkeypatch.setattr(subject.alg, "load", lambda path: {})
    written = {}
    monkeypatch.setattr(subject.alg, "atomic", lambda path, value: written.update(value))
    monkeypatch.setattr(subject, "_sha", lambda path: "sha")
    result = subject.measure_batch(
        {}, {"job_w1": {"scene": "job_memory"}},
        {"job_w1": {"rank": 1, "params": {}}}, tmp_path / "point", "token",
    )
    assert order == ["precondition", "scored"]
    assert result["job_w1"]["db_qps"] == 1
    assert written["job_cache_precondition"] == precondition
    assert written["precondition_metrics_used_for_scoring"] is False
    assert written["outer_loop_semantics_changed"] is False


def test_contract_requires_full_job_and_no_scoring():
    contract = subject._validate_contract()
    assert contract["precondition_query_count"] == 113
    assert contract["precondition_metrics_used_for_scoring"] is False
    assert contract["candidate_parameter_application_after_precondition"] is True
    assert contract["outer_loop_semantics_changed"] is False
