from __future__ import annotations

import json
from pathlib import Path

import pytest

from formal_mysql_production_sampling import (
    CgroupCounterReader,
    MySQLAttributableCounterReader,
    ProductionSamplingError,
    ProductionSamplingStream,
    append_atomic_jsonl,
    build_five_scenario_sampling_services,
    build_production_sampling_services,
)


def _write(path: Path, value: str) -> None:
    path.write_text(value, encoding="ascii")


def _cgroup_v2(tmp_path: Path) -> tuple[CgroupCounterReader, Path]:
    root = tmp_path / "cgroup"
    root.mkdir()
    _write(root / "cpu.stat", "usage_usec 1000000\n")
    _write(root / "memory.current", "500\n")
    _write(root / "memory.stat", "inactive_file 100\n")
    _write(root / "io.stat", "259:1 rbytes=1000 wbytes=2000 rios=1 wios=2\n")
    return CgroupCounterReader({
        "version": 2,
        "root": str(root),
        "device": "259:1",
        "cpu_quota_cores": 2.0,
        "memory_limit_bytes": 1000,
        "io_read_cap_bps": 1000,
        "io_write_cap_bps": 1000,
    }), root


def _registered_database(totals: list[int]) -> MySQLAttributableCounterReader:
    digest = "a" * 64
    import hashlib
    canonical = json.dumps([digest], separators=(",", ":"), ensure_ascii=True)
    digest_set = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    return MySQLAttributableCounterReader({
        "attribution_scope": "registered_workload_digest_set",
        "counter_name": "performance_schema_digest_sum_count_star",
        "schema": "formal_db",
        "digests": [digest],
        "digest_set_sha256": digest_set,
    }, runner=lambda _command: str(totals.pop(0) if len(totals) > 1 else totals[0]))


def test_combined_one_second_record_contains_raw_cgroup_and_database_counter(
    tmp_path: Path,
) -> None:
    cgroup, root = _cgroup_v2(tmp_path)
    totals = [10, 14]
    database = _registered_database(totals)
    monotonic = [1_000_000_000, 2_000_000_000]
    epochs = [1000, 2000]
    output = tmp_path / "samples.jsonl"
    stream = ProductionSamplingStream(
        cgroup, database, output,
        monotonic_ns=lambda: monotonic.pop(0) if monotonic else 3_000_000_000,
        epoch_ms=lambda: epochs.pop(0) if epochs else 3000,
    )
    assert stream.sample_once() is None
    _write(root / "cpu.stat", "usage_usec 1500000\n")
    _write(root / "memory.current", "600\n")
    _write(root / "memory.stat", "inactive_file 100\n")
    _write(root / "io.stat", "259:1 rbytes=1400 wbytes=2600 rios=2 wios=3\n")
    record = stream.sample_once()
    assert record is not None
    resource = record["resource"]
    assert resource["cpu_usage_raw_ns"] == 1_500_000_000
    assert resource["cpu_delta_raw_ns"] == 500_000_000
    assert resource["memory_current_raw_bytes"] == 600
    assert resource["io_read_raw_bytes"] == 1400
    assert resource["io_write_raw_bytes"] == 2600
    assert resource["cpu_utilization_ratio"] == pytest.approx(0.25)
    assert resource["memory_utilization_ratio"] == pytest.approx(0.5)
    assert resource["io_utilization_ratio"] == pytest.approx(0.5)
    assert record["database"] == {
        "epoch_ms": 2000,
        "completed_total": 14,
        "source": "database_status_counter",
        "attribution_scope": "registered_workload_digest_set",
        "counter_name": "performance_schema_digest_sum_count_star",
        "digest_set_sha256": database.digest_set_sha256,
    }
    lines = output.read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == record
    assert stream.resource_samples()[0]["epoch_ms"] == 2000
    assert stream.database_qps_samples()[0]["completed_total"] == 14
    stream.close()


def test_registered_digest_counter_excludes_monitoring_query_from_dbqps() -> None:
    digest = "a" * 64
    monitoring_query_digest = "b" * 64
    canonical = json.dumps([digest], separators=(",", ":"), ensure_ascii=True)
    import hashlib
    digest_set = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    database_digest_counts = {digest: 7, monitoring_query_digest: 100}

    def performance_schema_runner(command):
        query = command[0]
        assert digest.upper() in query
        assert monitoring_query_digest.upper() not in query
        return str(database_digest_counts[digest])

    reader = MySQLAttributableCounterReader({
        "attribution_scope": "registered_workload_digest_set",
        "counter_name": "performance_schema_digest_sum_count_star",
        "schema": "formal_db",
        "digests": [digest],
        "digest_set_sha256": digest_set,
    }, runner=performance_schema_runner)
    row = reader.sample(1234)
    assert row["completed_total"] == 7
    assert row["completed_total"] != sum(database_digest_counts.values())
    assert row["digest_set_sha256"] == digest_set
    assert "events_statements_summary_by_digest" in reader.query
    assert f"DIGEST IN ('{digest.upper()}')" in reader.query
    assert "global_status" not in reader.query
    assert "Com_select" not in reader.query
    assert "Questions" not in reader.query
    # The monitoring SELECT increments its own, different digest.  Because the
    # aggregate is restricted to the frozen workload digest list, it cannot
    # contribute to completed_total.
    with pytest.raises(ProductionSamplingError, match="only permits registered"):
        MySQLAttributableCounterReader({
            "attribution_scope": "dedicated_mysql_instance",
            "counter_name": "mysql_global_com_dml_sum",
        }, runner=lambda command: "1")


def test_missing_production_configuration_fails_before_any_connection(tmp_path: Path) -> None:
    with pytest.raises(ProductionSamplingError, match="production_sampling"):
        build_production_sampling_services({})
    with pytest.raises(ProductionSamplingError, match="interval_seconds"):
        build_production_sampling_services({"production_sampling": {}})
    with pytest.raises(ProductionSamplingError, match="missing=.*hybench_ap"):
        build_five_scenario_sampling_services({
            "production_sampling_by_benchmark": {}
        })


def test_atomic_jsonl_rejects_relative_or_nonfinite_payload(tmp_path: Path) -> None:
    with pytest.raises(ProductionSamplingError, match="absolute"):
        append_atomic_jsonl(Path("relative.jsonl"), {"ok": True})
    with pytest.raises(ValueError):
        append_atomic_jsonl(tmp_path / "samples.jsonl", {"value": float("nan")})


def test_atomic_jsonl_rejects_insecure_parent_and_existing_mode(tmp_path: Path) -> None:
    insecure_parent = tmp_path / "shared"
    insecure_parent.mkdir(mode=0o777)
    insecure_parent.chmod(0o777)
    with pytest.raises(ProductionSamplingError, match="parent"):
        append_atomic_jsonl(insecure_parent / "samples.jsonl", {"ok": True})
    output = tmp_path / "samples.jsonl"
    output.write_text("", encoding="ascii")
    output.chmod(0o644)
    with pytest.raises(ProductionSamplingError, match="mode 0600"):
        append_atomic_jsonl(output, {"ok": True})


def test_database_counter_regression_fails_closed(tmp_path: Path) -> None:
    cgroup, root = _cgroup_v2(tmp_path)
    totals = iter((10, 9))
    database = _registered_database(list(totals))
    ticks = iter((1_000_000_000, 2_000_000_000))
    epochs = iter((1000, 2000))
    stream = ProductionSamplingStream(
        cgroup, database, tmp_path / "samples.jsonl",
        monotonic_ns=lambda: next(ticks), epoch_ms=lambda: next(epochs),
    )
    stream.sample_once()
    _write(root / "cpu.stat", "usage_usec 1100000\n")
    with pytest.raises(ProductionSamplingError, match="regressed"):
        stream.sample_once()
