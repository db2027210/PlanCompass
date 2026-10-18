from pathlib import Path

from scripts import mysql_job_page_cache_preconditioned_measurement_v4 as subject


def test_files_uses_privileged_size_inventory(monkeypatch):
    database = Path("/disk2/frozen/job_w1/datadir/imdb_job")
    monkeypatch.setattr(subject.v3, "_database_dir", lambda worker: database)
    rows = [
        (1, database / f"table_{index:02d}.ibd")
        for index in range(subject.legacy.EXPECTED_IBD_FILE_COUNT - 1)
    ]
    used = sum(size for size, _path in rows)
    rows.append((subject.legacy.EXPECTED_IBD_BYTES - used, database / "table_20.ibd"))
    payload = b"".join(f"{size}\t{path}\0".encode() for size, path in rows)
    class Completed:
        stdout = payload
    monkeypatch.setattr(subject.subprocess, "run", lambda *a, **k: Completed())
    paths = subject._files({"worker_id": "job_w1"})
    assert len(paths) == subject.legacy.EXPECTED_IBD_FILE_COUNT
    assert all(path.parent == database for path in paths)


def test_contract_keeps_single_scored_full_suite():
    contract = subject._validate_contract()
    assert contract["precondition_query_count"] == 0
    assert contract["formal_measurement_replays_per_candidate"] == 1
    assert contract["minimum_resident_fraction"] == 1.0
