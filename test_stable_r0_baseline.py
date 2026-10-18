"""Self-tests for stable R0 baseline extraction."""

from __future__ import annotations

import copy
import json

from stable_r0_baseline import MARKER, SEMANTICS, extract_stable_r0_baseline


def payload() -> dict:
    return {
        "status": "complete",
        "successful_queries": 100,
        "failed_queries": 0,
        "worker_cleanup": {
            "controller_thread_stopped": True,
            "database_backends_fully_released": True,
            "remaining_benchmark_backend_count": 0,
        },
        "r0_decision_window": {
            "available": True,
            "stable": True,
            "start_window_number": 5,
            "end_window_number": 10,
            "end_elapsed_seconds": 300,
            "utilization_pct": {"cpu": 47.0, "memory": 88.0, "io": 44.0},
        },
        "r0_profile_construction_gate": {"passed": True},
        "r0_resource_gate": {"passed": True},
        "formal_pg_resource": {
            "semantics": SEMANTICS,
            "caps": {
                "cpu_quota_cores": 22,
                "memory_bytes": 1_730_000_000,
                "read_cap_bps": 16 * 1024 * 1024,
                "write_cap_bps": 20 * 1024 * 1024,
                "io_total_cap_bps": 36 * 1024 * 1024,
            },
            "aggregate": {
                "read_bps": 10_000_000,
                "write_bps": 1_000_000,
            },
        },
    }


def stats(value: dict) -> dict:
    return {
        "workload_output": "prefix\n" + MARKER + json.dumps(value),
        "formal_pg_resource": {"semantics": SEMANTICS},
    }


def rolling_stats(value: dict) -> dict:
    marker = {
        "benchmark": {"throughput_qps": 1.0},
        "benchmark_latency": {"all_p95_avg": 1.0},
        "formal_pg_resource": value["formal_pg_resource"],
        "candidate_measurement_protocol": {
            "stable": True,
            "stop_reason": "stable_early_stop",
        },
    }
    summary = {
        key: item
        for key, item in value.items()
        if key != "formal_pg_resource"
    }
    return {
        "workload_output": (
            "prefix\n"
            + MARKER
            + json.dumps(marker)
            + "\n"
            + json.dumps(summary)
        ),
        "formal_pg_resource": value["formal_pg_resource"],
    }


def contract() -> dict:
    return {
        "cpu_quota_cores": 22,
        "memory_limit_bytes": 1_730_000_000,
        "io_read_cap_mib_s": 16,
        "io_write_cap_mib_s": 20,
    }


def main() -> None:
    result = extract_stable_r0_baseline(stats(payload()), contract())
    assert result is not None
    resource_avg, rewritten, audit = result
    assert resource_avg["cpu"] == 10.34
    assert audit["utilization_pct"] == {"cpu": 47.0, "memory": 88.0, "io": 44.0}
    aggregate = rewritten["formal_pg_resource"]["aggregate"]
    assert aggregate["memory_utilization"] == 0.88
    assert aggregate["io_utilization"] == 0.44
    assert abs(
        aggregate["total_bps"]
        - 0.44 * 36 * 1024 * 1024
    ) < 1e-9

    rolling_result = extract_stable_r0_baseline(rolling_stats(payload()), contract())
    assert rolling_result is not None
    rolling_resource_avg, rolling_rewritten, rolling_audit = rolling_result
    assert rolling_resource_avg["cpu"] == 10.34
    assert rolling_rewritten["stable_r0_decision_override"][
        "formal_resource_gate"
    ]["passed"]
    assert rolling_audit["decision_start_window"] == 5
    assert rolling_audit["decision_end_window"] == 10

    for mutation in (
        ("status", "failed"),
        ("failed_queries", 1),
    ):
        invalid = payload()
        invalid[mutation[0]] = mutation[1]
        try:
            extract_stable_r0_baseline(stats(invalid), contract())
        except ValueError:
            pass
        else:
            raise AssertionError(f"mutation must fail closed: {mutation}")

    invalid_gate = payload()
    invalid_gate["r0_resource_gate"] = {"passed": False}
    try:
        extract_stable_r0_baseline(stats(invalid_gate), contract())
    except ValueError:
        pass
    else:
        raise AssertionError("failed formal gate must fail closed")

    mismatched = copy.deepcopy(contract())
    mismatched["cpu_quota_cores"] = 32
    try:
        extract_stable_r0_baseline(stats(payload()), mismatched)
    except ValueError:
        pass
    else:
        raise AssertionError("cap mismatch must fail closed")

    assert extract_stable_r0_baseline({"workload_output": "no marker"}, contract()) is None
    print("stable_r0_baseline self-test passed", flush=True)


if __name__ == "__main__":
    main()
