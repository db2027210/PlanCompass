from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys

import pytest

from shell import mysql_sql_debug_metric_adapter_v1 as adapter
from shell import mysql_distributed_live_readback_qualification_v1 as collector


def test_native_metrics_keep_one_real_query_duration_shape() -> None:
    metrics = adapter.NativeMetrics()
    metrics.begin(0, 10.0)
    inflight = metrics.document("a" * 32, 0, 100.0, 10.25)
    assert inflight == {
        "schema_version":
            "mysql_algorithm_c_debug_workload_metric_sample_v1",
        "qualification_attempt_id": "a" * 32,
        "sequence": 0,
        "epoch_seconds": 100.0,
        "completed_total": 0,
        "query_duration_ms": 250.0,
    }
    metrics.complete(0, 100.0)
    metrics.begin(0, 11.0)
    metrics.complete(0, 300.0)
    completed = metrics.document("a" * 32, 1, 101.0, 12.0)
    assert completed["completed_total"] == 2
    assert completed["query_duration_ms"] == 300.0
    assert "latency_p50_ms" not in completed
    assert "latency_p95_ms" not in completed
    assert "latency_p99_ms" not in completed


def test_weighted_schedule_is_complete_deterministic_and_exact() -> None:
    paths = [Path("Q1.sql"), Path("Q2.sql"), Path("Q3.sql")]
    first = adapter._weighted_schedule(paths, [10.0, 20.0, 7.5], 4508333)
    second = adapter._weighted_schedule(paths, [10.0, 20.0, 7.5], 4508333)
    assert first == second
    assert first.count(paths[0]) == 4
    assert first.count(paths[1]) == 8
    assert first.count(paths[2]) == 3


def test_append_sample_is_jsonl_and_fsync_visible(tmp_path: Path) -> None:
    target = tmp_path / "metrics.jsonl"
    document = {
        "schema_version":
            "mysql_algorithm_c_debug_workload_metric_sample_v1",
        "qualification_attempt_id": "b" * 32,
        "sequence": 0,
        "epoch_seconds": 100.0,
        "completed_total": 1,
        "latency_p50_ms": 1.0,
        "latency_p95_ms": 2.0,
        "latency_p99_ms": 3.0,
    }
    adapter._append_sample(target, document)
    assert target.read_bytes().endswith(b"\n")
    assert json.loads(target.read_text().splitlines()[-1]) == document


def test_no_synthetic_latency_is_permitted() -> None:
    metrics = adapter.NativeMetrics()
    with pytest.raises(adapter.AdapterError, match="no completed or in-flight"):
        metrics.document("c" * 32, 0, 1.0, 1.0)


def test_query_timeouts_are_bounded_by_campaign_deadline() -> None:
    assert adapter._deadline_bounded_query_timeouts(
        100.0, 600, now=0.0
    ) == (100000, 102.0)
    assert adapter._deadline_bounded_query_timeouts(
        100.0, 600, now=99.75
    ) == (250, 2.25)
    assert adapter._deadline_bounded_query_timeouts(
        100.0, 3, now=0.0
    ) == (3000, 5.0)
    assert adapter._deadline_bounded_query_timeouts(
        100.0, 600, now=100.0
    ) is None


def test_deadline_cancel_is_not_a_completion_or_failure() -> None:
    metrics = adapter.NativeMetrics()
    metrics.begin(7, 10.0)
    metrics.cancel(7)
    assert metrics.in_flight == {}
    assert metrics.completed_total == 0
    assert metrics.failure is None


def test_real_sql_adapter_command_spec_passes_shared_collector(
    tmp_path: Path,
) -> None:
    query_dir = tmp_path / "sql"
    query_dir.mkdir()
    query = query_dir / "Q1.sql"
    query.write_text("SELECT 1;\n", encoding="utf-8")
    host_tmpfs = tmp_path / "host-tmpfs"
    host_tmpfs.mkdir()
    metric = host_tmpfs / "metrics.jsonl"
    defaults = tmp_path / "mysql.cnf"
    defaults.write_text("[client]\nuser=root\n", encoding="utf-8")
    script = Path(adapter.__file__).resolve()
    executable = Path(sys.executable).resolve()
    mysql = Path("/usr/bin/mysql").resolve()

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    spec_path = tmp_path / "command.json"
    document = {
        "schema_version":
            "mysql_algorithm_c_debug_qualification_command_v1",
        "bundle_id": "d" * 64,
        "mode": "qualification_solo",
        "qualification_attempt_id": "attempt-" + ("e" * 32),
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "argv": [
            "/runtime/python",
            "/bundle/adapter.py",
            "--attempt-id",
            "attempt-" + ("e" * 32),
            "--metric-stream",
            "/measurement/client-private-metrics.jsonl",
            "--mysql-binary",
            "/runtime/mysql",
            "--defaults-extra-file",
            "/config/mysql.cnf",
            "--port",
            "65103",
            "--database",
            "tpch",
            "--query-dir",
            "/sql",
            "--concurrency",
            "4",
            "--duration-seconds",
            "240",
        ],
        "executable_sha256": digest(executable),
        "code_files": {
            str(executable): digest(executable),
            str(script): digest(script),
            str(mysql): digest(mysql),
            str(defaults): digest(defaults),
            str(query): digest(query),
        },
        "environment": {},
        "cwd": "/measurement",
        "workload_metric_stream": {
            "schema_version":
                "mysql_algorithm_c_debug_workload_metric_stream_v1",
            "path": str(metric),
        },
        "workload_metric_adapter": {
            "schema_version":
                "mysql_algorithm_c_debug_workload_metric_adapter_v1",
            "kind": "sql_query_native",
        },
        "workload_process_isolation": {
            "cpuset_cpus": "0-7,36-43",
            "cpuset_mems": "0",
            "root_and_descendants_exact": True,
        },
        "client_container": {
            "name": "ra-mysql-tpch-client",
            "container_id": "a" * 64,
            "image_id": "sha256:" + ("b" * 64),
            "created": "2026-07-31T00:00:00Z",
            "inspect_config_sha256": "c" * 64,
            "required_read_only_mounts": [
                {
                    "source": str(executable),
                    "destination": "/runtime/python",
                },
                {
                    "source": str(script),
                    "destination": "/bundle/adapter.py",
                },
                {
                    "source": str(mysql),
                    "destination": "/runtime/mysql",
                },
                {
                    "source": str(defaults),
                    "destination": "/config/mysql.cnf",
                },
                {
                    "source": str(query_dir),
                    "destination": "/sql",
                },
            ],
        },
        "measurement_output": {
            "host_tmpfs_directory": str(host_tmpfs),
            "container_tmpfs_destination": "/measurement",
        },
        "paired_peer_workload": None,
    }
    collector.atomic_json(spec_path, document)
    assert collector.validate_command_spec(
        spec_path,
        "d" * 64,
        "qualification_solo",
        {
            "execution_mode": "dedicated_read_only_docker_client_cgroup",
            "cpuset_cpus": "0-7,36-43",
            "cpuset_mems": "0",
        },
    ) == document
