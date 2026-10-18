#!/usr/bin/env python3
"""Regression tests for the final PG5 sum/total I/O contract."""

from __future__ import annotations

import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from formal_pg_resource_semantics import (
    FormalPgResourceError,
    SEMANTICS_NAME,
    derive_resource_samples,
    normalize_derived_resource_samples,
    normalize_runtime_resource,
    summarize_resource_samples,
)
from system_resource_monitor import parse_hybench_benchmark_output


def rows(read_bytes: int, write_bytes: int) -> list[dict[str, object]]:
    return [
        {
            "timestamp_utc": "2026-01-01T00:00:00Z",
            "monotonic_ns": 0,
            "cpu_usage_ns": 0,
            "memory_current_bytes": 400,
            "memory_working_set_bytes": 400,
            "io_read_bytes": 0,
            "io_write_bytes": 0,
        },
        {
            "timestamp_utc": "2026-01-01T00:00:01Z",
            "monotonic_ns": 1_000_000_000,
            "cpu_usage_ns": 2_000_000_000,
            "memory_current_bytes": 400,
            "memory_working_set_bytes": 400,
            "io_read_bytes": read_bytes,
            "io_write_bytes": write_bytes,
        },
    ]


def main() -> int:
    profile = {
        "cpu_quota_cores": 4,
        "memory_bytes": 1000,
        "io_read_bps": 100,
        "io_write_bps": 300,
    }
    sample = derive_resource_samples(rows(50, 270), profile)[0]
    assert math.isclose(sample["io_utilization"], 0.8)
    assert math.isclose(sample["total_bps"], 320.0)
    evidence = summarize_resource_samples([sample], profile)
    assert evidence["semantics"] == SEMANTICS_NAME
    assert evidence["caps"] == {
        "cpu_quota_cores": 4.0,
        "memory_bytes": 1000.0,
        "read_cap_bps": 100.0,
        "write_cap_bps": 300.0,
        "io_total_cap_bps": 400.0,
    }
    normalized, utilization = normalize_runtime_resource(
        evidence,
        {"cpu": 4, "memory": 1000 / 1024 ** 3, "io": 400 / 1024 ** 2},
    )
    assert math.isclose(utilization["io"], 80.0)
    assert math.isclose(normalized["io_read"], 50 / 1024 ** 2)
    assert math.isclose(normalized["io_write"], 270 / 1024 ** 2)

    # Real frozen-runner schema: explicit directional BPS and working set,
    # but no redundant total_bps field.
    runner_sample = {
        "cpu_utilization": 0.386,
        "memory_utilization": 0.848,
        "io_utilization": 0.35,
        "read_bps": 93_952_409.6,
        "write_bps": 0.0,
        "memory_working_set_bytes": 2_731_000_000.0,
        "read_cap_bps": 134_217_728.0,
        "write_cap_bps": 134_217_728.0,
        "io_total_cap_bps": 268_435_456.0,
        "qps": 0.738,
    }
    normalized_runner_samples = normalize_derived_resource_samples([runner_sample])
    assert math.isclose(normalized_runner_samples[0]["total_bps"], 93_952_409.6)
    runner_evidence = summarize_resource_samples(normalized_runner_samples, profile)
    assert math.isclose(runner_evidence["aggregate"]["read_bps"], 93_952_409.6)
    assert math.isclose(runner_evidence["aggregate"]["write_bps"], 0.0)
    assert math.isclose(runner_evidence["aggregate"]["total_bps"], 93_952_409.6)
    assert math.isclose(
        runner_evidence["aggregate"]["memory_working_set_bytes"],
        2_731_000_000.0,
    )

    inconsistent_runner_sample = dict(runner_sample, total_bps=1.0)
    try:
        normalize_derived_resource_samples([inconsistent_runner_sample])
    except FormalPgResourceError as exc:
        assert "differs from read_bps + write_bps" in str(exc)
    else:
        raise AssertionError("inconsistent total_bps must fail closed")

    overloaded = derive_resource_samples(rows(101, 300), profile)[0]
    assert math.isclose(overloaded["io_utilization"], 401 / 400)
    assert overloaded["io_utilization"] > 1.0

    marker = {
        "benchmark": {"xp_qps": "1.0"},
        "formal_pg_resource": evidence,
        "candidate_measurement_protocol": {"stable": True},
    }
    parsed = parse_hybench_benchmark_output(
        ["RESOURCE_ADAPTIVE_BENCHMARK_JSON=" + __import__("json").dumps(marker)]
    )
    assert parsed["formal_pg_resource"] == evidence
    assert parsed["candidate_measurement_protocol"] == {"stable": True}
    print("formal PG sum/total resource semantics tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
