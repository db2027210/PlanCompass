from pathlib import Path

from scripts import mysql_job_page_cache_preconditioned_measurement_v5 as subject


def test_find_format_contains_escaped_not_embedded_nul(monkeypatch):
    database = Path("/disk2/frozen/job_w1/datadir/imdb_job")
    monkeypatch.setattr(subject.v4.v3, "_database_dir", lambda worker: database)
    rows = [(1, database / f"t{index}.ibd") for index in range(20)]
    rows.append((subject.legacy.EXPECTED_IBD_BYTES - 20, database / "t20.ibd"))
    payload = b"".join(f"{size}\t{path}\0".encode() for size, path in rows)
    seen = {}
    class Completed:
        stdout = payload
    def run(command, **kwargs):
        seen["format"] = command[-1]
        return Completed()
    monkeypatch.setattr(subject.subprocess, "run", run)
    assert len(subject._files({"worker_id": "job_w1"})) == 21
    assert "\0" not in seen["format"]
    assert r"\0" in seen["format"]


def test_contract_keeps_single_scored_full_suite():
    contract = subject._validate_contract()
    assert contract["precondition_query_count"] == 0
    assert contract["formal_measurement_replays_per_candidate"] == 1
    assert contract["minimum_resident_fraction"] == 1.0
