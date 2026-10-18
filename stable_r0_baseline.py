"""Fail-closed extraction of a stable R0 decision from workload output."""

from __future__ import annotations

import copy
import json
import math
from typing import Any, Dict, Mapping, Optional, Tuple


MARKER = "RESOURCE_ADAPTIVE_BENCHMARK_JSON="
SEMANTICS = "pg5_io_total_throughput_utilization_v1"


def _positive_float(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive")
    return number


def _utilization(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 100.0:
        raise ValueError(f"{name} utilization must be within [0, 100]")
    return number


def _last_payload(workload_output: str) -> Optional[Dict[str, Any]]:
    lines = str(workload_output or "").splitlines()
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if MARKER not in line:
            continue
        marker_payload = json.loads(line.split(MARKER, 1)[1].strip())
        if not isinstance(marker_payload, dict):
            raise ValueError("benchmark marker payload must be an object")

        # The rolling TPC-H runner emits the resource marker and then the
        # authoritative run summary as the immediately following JSON line.
        # Preserve support for older runners that embedded the summary fields
        # directly in the marker.
        if marker_payload.get("status") is not None:
            return marker_payload
        if index + 1 >= len(lines):
            raise ValueError("R0 summary line is missing after benchmark marker")
        summary_payload = json.loads(lines[index + 1].strip())
        if not isinstance(summary_payload, dict):
            raise ValueError("R0 summary payload must be an object")
        merged_payload = copy.deepcopy(summary_payload)
        for key in (
            "benchmark",
            "benchmark_latency",
            "formal_pg_resource",
            "candidate_measurement_protocol",
        ):
            if key in marker_payload:
                merged_payload[key] = copy.deepcopy(marker_payload[key])
        return merged_payload
    return None


def extract_stable_r0_baseline(
    resource_stats: Mapping[str, Any],
    runtime_contract: Mapping[str, Any],
) -> Optional[Tuple[Dict[str, float], Dict[str, Any], Dict[str, Any]]]:
    """
    Replace whole-process startup averages with the validated latest-six R0 window.

    Returns:
        ``(resource_avg, resource_stats, audit)`` when an exact stable decision is
        present. Returns ``None`` when the workload emitted no benchmark marker.

    Raises:
        ValueError: A marker exists but its evidence is incomplete or inconsistent.
    """
    payload = _last_payload(str(resource_stats.get("workload_output") or ""))
    if payload is None:
        return None
    if payload.get("status") != "complete":
        raise ValueError(f"R0 payload status is not complete: {payload.get('status')!r}")
    if int(payload.get("failed_queries", -1)) != 0:
        raise ValueError("R0 payload contains failed queries")
    if int(payload.get("successful_queries", 0)) <= 0:
        raise ValueError("R0 payload contains no successful query")

    cleanup = payload.get("worker_cleanup")
    if not isinstance(cleanup, Mapping) or any(
        cleanup.get(key) is not expected
        for key, expected in (
            ("controller_thread_stopped", True),
            ("database_backends_fully_released", True),
            ("remaining_benchmark_backend_count", 0),
        )
    ):
        raise ValueError("R0 worker cleanup is incomplete")

    decision = payload.get("r0_decision_window")
    construction_gate = payload.get("r0_profile_construction_gate")
    resource_gate = payload.get("r0_resource_gate")
    if not isinstance(decision, Mapping):
        raise ValueError("R0 decision window is missing")
    if decision.get("available") is not True or decision.get("stable") is not True:
        raise ValueError("R0 decision window is unavailable or unstable")
    if not isinstance(construction_gate, Mapping) or construction_gate.get("passed") is not True:
        raise ValueError("R0 profile construction gate did not pass")
    if not isinstance(resource_gate, Mapping) or resource_gate.get("passed") is not True:
        raise ValueError("R0 formal resource gate did not pass")

    utilization_raw = decision.get("utilization_pct")
    if not isinstance(utilization_raw, Mapping):
        raise ValueError("R0 decision utilization is missing")
    utilization = {
        name: _utilization(utilization_raw.get(name), name)
        for name in ("cpu", "memory", "io")
    }
    if not 85.0 <= utilization["memory"] <= 90.0:
        raise ValueError("R0 decision memory is outside inclusive 85-90%")
    for name in ("cpu", "io"):
        if not 30.0 <= utilization[name] <= 50.0:
            raise ValueError(f"R0 decision {name} is outside inclusive 30-50%")
    start_window = int(decision.get("start_window_number", -1))
    end_window = int(decision.get("end_window_number", -1))
    if start_window <= 0 or end_window - start_window != 5:
        raise ValueError("R0 decision is not exactly six consecutive windows")

    payload_resource = payload.get("formal_pg_resource")
    if not isinstance(payload_resource, Mapping):
        payload_resource = resource_stats.get("formal_pg_resource")
    if not isinstance(payload_resource, Mapping):
        raise ValueError("formal PostgreSQL resource evidence is missing")
    if payload_resource.get("semantics") != SEMANTICS:
        raise ValueError("formal PostgreSQL resource semantics mismatch")
    caps_raw = payload_resource.get("caps")
    aggregate_raw = payload_resource.get("aggregate")
    if not isinstance(caps_raw, Mapping) or not isinstance(aggregate_raw, Mapping):
        raise ValueError("formal PostgreSQL caps or aggregate are missing")
    caps = {
        "cpu_quota_cores": _positive_float(
            caps_raw.get("cpu_quota_cores"), "cpu_quota_cores"
        ),
        "memory_bytes": _positive_float(caps_raw.get("memory_bytes"), "memory_bytes"),
        "read_cap_bps": _positive_float(caps_raw.get("read_cap_bps"), "read_cap_bps"),
        "write_cap_bps": _positive_float(caps_raw.get("write_cap_bps"), "write_cap_bps"),
    }
    caps["io_total_cap_bps"] = caps["read_cap_bps"] + caps["write_cap_bps"]

    expected = {
        "cpu_quota_cores": _positive_float(
            runtime_contract.get("cpu_quota_cores"), "runtime cpu_quota_cores"
        ),
        "memory_bytes": _positive_float(
            runtime_contract.get("memory_limit_bytes"), "runtime memory_limit_bytes"
        ),
        "read_cap_bps": _positive_float(
            runtime_contract.get("io_read_cap_mib_s"), "runtime io_read_cap_mib_s"
        )
        * 1024.0
        * 1024.0,
        "write_cap_bps": _positive_float(
            runtime_contract.get("io_write_cap_mib_s"), "runtime io_write_cap_mib_s"
        )
        * 1024.0
        * 1024.0,
    }
    for name, value in expected.items():
        if not math.isclose(caps[name], value, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError(f"R0 cap mismatch for {name}: {caps[name]} != {value}")

    original_read = max(0.0, float(aggregate_raw.get("read_bps", 0.0)))
    original_write = max(0.0, float(aggregate_raw.get("write_bps", 0.0)))
    original_total = original_read + original_write
    read_fraction = original_read / original_total if original_total > 0.0 else 1.0
    decision_total_bps = utilization["io"] / 100.0 * caps["io_total_cap_bps"]
    decision_read_bps = decision_total_bps * read_fraction
    decision_write_bps = decision_total_bps - decision_read_bps

    stable_resource = copy.deepcopy(dict(payload_resource))
    stable_resource["caps"] = caps
    stable_resource["sample_count"] = 180
    stable_resource["aggregate"] = {
        "cpu_utilization": utilization["cpu"] / 100.0,
        "memory_utilization": utilization["memory"] / 100.0,
        "io_utilization": utilization["io"] / 100.0,
        "read_bps": decision_read_bps,
        "write_bps": decision_write_bps,
        "total_bps": decision_total_bps,
        "memory_working_set_bytes": utilization["memory"]
        / 100.0
        * caps["memory_bytes"],
    }

    rewritten_stats = copy.deepcopy(dict(resource_stats))
    rewritten_stats["formal_pg_resource"] = stable_resource
    rewritten_stats["stable_r0_decision_override"] = {
        "schema_version": "stable_r0_baseline_override_v1",
        "source": "validated_latest_six_consecutive_30_second_windows",
        "decision_window": copy.deepcopy(dict(decision)),
        "profile_construction_gate": copy.deepcopy(dict(construction_gate)),
        "formal_resource_gate": copy.deepcopy(dict(resource_gate)),
    }
    resource_avg = {
        "cpu": utilization["cpu"] / 100.0 * caps["cpu_quota_cores"],
        "memory": stable_resource["aggregate"]["memory_working_set_bytes"],
        "io": decision_total_bps,
    }
    audit = {
        "event": "baseline_stable_r0_decision_applied",
        "schema_version": "stable_r0_baseline_override_v1",
        "utilization_pct": utilization,
        "caps": caps,
        "decision_start_window": decision.get("start_window_number"),
        "decision_end_window": decision.get("end_window_number"),
        "decision_end_elapsed_seconds": decision.get("end_elapsed_seconds"),
        "successful_queries": int(payload["successful_queries"]),
        "failed_queries": 0,
    }
    return resource_avg, rewritten_stats, audit
