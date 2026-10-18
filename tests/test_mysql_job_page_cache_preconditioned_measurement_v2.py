from pathlib import Path

from scripts import mysql_job_page_cache_preconditioned_measurement_v2 as subject


def test_contract_freezes_verified_residency_without_extra_job_replay():
    contract = subject._validate_contract()
    assert contract["precondition_mode"] == "verified_host_page_cache_full_imdb_job_ibd"
    assert contract["precondition_query_count"] == 0
    assert contract["minimum_resident_fraction"] == 1.0
    assert contract["formal_measurement_replays_per_candidate"] == 1
    assert contract["outer_loop_semantics_changed"] is False


def test_job_prefetch_precedes_only_scored_replay(monkeypatch, tmp_path: Path):
    order = []
    precondition = {"path": "cache.json", "sha256": "cache-sha"}
    monkeypatch.setattr(subject, "_validate_contract", lambda: {"release_version": "v2"})
    monkeypatch.setattr(subject, "_precondition", lambda *a, **k: order.append("prefetch") or precondition)
    monkeypatch.setattr(
        subject.predecessor, "measure_batch",
        lambda *a, **k: order.append("scored") or {"job_w1": {"db_qps": 1.0}},
    )
    monkeypatch.setattr(subject.alg, "load", lambda path: {})
    written = {}
    monkeypatch.setattr(subject.alg, "atomic", lambda path, value: written.update(value))
    monkeypatch.setattr(subject, "_sha", lambda path: "sha")
    result = subject.measure_batch(
        {}, {"job_w1": {"scene": "job_memory"}},
        {"job_w1": {"rank": 1, "params": {}}}, tmp_path / "point", "token",
    )
    assert order == ["prefetch", "scored"]
    assert result["job_w1"]["db_qps"] == 1.0
    assert written["job_page_cache_precondition"] == precondition
    assert written["precondition_metrics_used_for_scoring"] is False


def test_residency_parser_requires_full_inventory(monkeypatch):
    paths = [Path("/disk2/a.ibd"), Path("/disk2/b.ibd")]
    class Completed:
        stdout = "4096 4096 /disk2/a.ibd\n8192 8192 /disk2/b.ibd\n"
    monkeypatch.setattr(subject.subprocess, "run", lambda *a, **k: Completed())
    rows = subject._residency(paths)
    assert [row["resident_fraction"] for row in rows] == [1.0, 1.0]
