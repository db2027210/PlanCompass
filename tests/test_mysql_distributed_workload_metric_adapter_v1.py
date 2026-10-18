from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "shell/mysql_distributed_workload_metric_adapter_v1.py"


def module_from(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ADAPTER = module_from(MODULE_PATH, "distributed_workload_metric_adapter")


def marker(kind: str, epoch_ms: int, completed: int = 11) -> str:
    common = {
        "epoch_ms": epoch_ms,
        "measurement_phase": True,
        "latency_p50_ms": 1.0,
        "latency_p95_ms": 2.0,
        "latency_p99_ms": 3.0,
    }
    if kind == "hybench_xp":
        common.update({"xap_or_ap_total": completed - 4, "xtp_total": 4})
        prefix = "HYBENCH_FORMAL_SAMPLE_JSON="
    else:
        common["completed_total"] = completed
        prefix = "BENCHBASE_FORMAL_SAMPLE_JSON="
    return prefix + json.dumps(common, sort_keys=True) + "\n"


def test_adapter_source_is_python36_compatible() -> None:
    ast.parse(
        MODULE_PATH.read_text(encoding="utf-8"),
        filename=str(MODULE_PATH),
        feature_version=(3, 6),
    )


@pytest.mark.parametrize("kind", ("hybench_xp", "benchbase_tpcc"))
def test_native_marker_emits_exact_same_attempt_metric(
    tmp_path: Path, kind: str,
) -> None:
    source = tmp_path / "native.stdout"
    stream = tmp_path / "metric.jsonl"
    epoch_ms = int(time.time() * 1000)
    source.write_text(marker(kind, epoch_ms), encoding="utf-8")
    returned = ADAPTER.emit_fresh_sample(
        kind,
        source,
        stream,
        "attempt-" + ("x" * 32),
        7,
        None,
        timeout_seconds=0.1,
    )
    assert returned == epoch_ms
    row = json.loads(stream.read_text(encoding="utf-8"))
    assert row["completed_total"] == 11
    assert row["sequence"] == 7
    assert row["native_adapter_kind"] == kind
    assert row["latency_p50_ms"] <= row["latency_p95_ms"]
    assert row["latency_p95_ms"] <= row["latency_p99_ms"]


@pytest.mark.parametrize("completed", (0, 9))
def test_sql_stdout_marker_crosses_private_container_namespace(
    tmp_path: Path, completed: int,
) -> None:
    """The host collector must not depend on the client's private tmpfs."""
    source = tmp_path / "docker-exec.stdout"
    stream = tmp_path / "host-tmpfs" / "metric.jsonl"
    stream.parent.mkdir()
    attempt = "attempt-" + ("s" * 32)
    epoch_seconds = time.time()
    native = {
        "schema_version":
            "mysql_algorithm_c_debug_workload_metric_sample_v1",
        "qualification_attempt_id": attempt,
        "sequence": 19,
        "epoch_seconds": epoch_seconds,
        "completed_total": completed,
    }
    if completed:
        native.update(
            {
                "latency_p50_ms": 1.0,
                "latency_p95_ms": 2.0,
                "latency_p99_ms": 3.0,
            }
        )
    else:
        native["query_duration_ms"] = 250.0
    source.write_text(
        "DEBUG_SQL_METRIC_SAMPLE_JSON="
        + json.dumps(native, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    returned = ADAPTER.emit_fresh_sample(
        "sql_query_native",
        source,
        stream,
        attempt,
        7,
        None,
        timeout_seconds=0.1,
    )
    assert returned == int(round(epoch_seconds * 1000.0))
    row = json.loads(stream.read_text(encoding="utf-8"))
    assert row["qualification_attempt_id"] == attempt
    assert row["sequence"] == 7
    assert row["completed_total"] == completed
    assert row["native_adapter_kind"] == "sql_query_native"
    if completed:
        assert row["latency_p99_ms"] == 3.0
    else:
        assert row["query_duration_ms"] == 250.0


def test_sql_stdout_marker_wrong_attempt_fails_closed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "docker-exec.stdout"
    source.write_text(
        "DEBUG_SQL_METRIC_SAMPLE_JSON="
        + json.dumps(
            {
                "schema_version":
                    "mysql_algorithm_c_debug_workload_metric_sample_v1",
                "qualification_attempt_id": "attempt-" + ("x" * 32),
                "sequence": 0,
                "epoch_seconds": time.time(),
                "completed_total": 1,
                "latency_p50_ms": 1,
                "latency_p95_ms": 1,
                "latency_p99_ms": 1,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ADAPTER.WorkloadMetricAdapterError,
        match="missing/stale/timed out",
    ):
        ADAPTER.emit_fresh_sample(
            "sql_query_native",
            source,
            tmp_path / "metric.jsonl",
            "attempt-" + ("y" * 32),
            0,
            None,
            timeout_seconds=0.02,
        )


@pytest.mark.parametrize(
    "content,previous",
    (
        ("no native marker\n", None),
        (
            marker("hybench_xp", int(time.time() * 1000) - 10_000),
            None,
        ),
        (
            marker("benchbase_tpcc", int(time.time() * 1000)),
            int(time.time() * 1000) + 1,
        ),
    ),
)
def test_missing_stale_or_reused_native_marker_fails_closed(
    tmp_path: Path, content: str, previous: int | None,
) -> None:
    source = tmp_path / "native.stdout"
    source.write_text(content, encoding="utf-8")
    with pytest.raises(
        ADAPTER.WorkloadMetricAdapterError,
        match="missing/stale/timed out",
    ):
        ADAPTER.emit_fresh_sample(
            "hybench_xp" if "HYBENCH" in content else "benchbase_tpcc",
            source,
            tmp_path / "metric.jsonl",
            "attempt-" + ("y" * 32),
            1,
            previous,
            timeout_seconds=0.02,
        )
    assert not (tmp_path / "metric.jsonl").exists()
