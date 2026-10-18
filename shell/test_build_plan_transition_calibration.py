#!/usr/bin/env python3

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile

import build_plan_transition_calibration as builder


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def summary(qps: float, memory_windows) -> dict:
    return {
        "db_qps": qps,
        "resource_mean_utilization": {
            "cpu": 0.40,
            "memory": sum(memory_windows) / len(memory_windows),
            "io": 0.30,
        },
        "resource_denominators": {"io_read_bps": 100.0, "io_write_bps": 100.0},
        "resource_stability": {
            "blocks": [
                {
                    "metrics": {
                        "memory_utilization": {"window_means": memory_windows}
                    }
                }
            ]
        },
    }


def measurement_files(root: Path, qps: float, memory_windows) -> None:
    write_json(root / "measurement_summary.json", summary(qps, memory_windows))
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    write_jsonl(
        root / "measurement_queries.jsonl",
        [
            {
                "event": "query_complete",
                "timestamp_utc": (origin + timedelta(seconds=2)).isoformat(),
                "query_id": "1",
                "latency_ms": 2000.0,
                "success": True,
            }
        ],
    )
    write_jsonl(
        root / "measurement_resource_samples.jsonl",
        [
            {
                "timestamp_utc": (origin + timedelta(seconds=index)).isoformat(),
                "monotonic_ns": index * 1_000_000_000,
                "cpu_quota_us": 100000,
                "cpu_period_us": 100000,
                "cpu_usage_ns": index * 500_000_000,
                "io_read_bytes": index * 50,
                "io_write_bytes": 0,
                "memory_current_bytes": int(memory_windows[min(index, 2)] * 100),
                "memory_limit_bytes": 100,
            }
            for index in range(3)
        ],
    )


def test_builder_emits_learnable_artifact() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        before_plan = root / "before_plan.json"
        after_plan = root / "after_plan.json"
        write_json(
            before_plan,
            {"exact_structural_plans": {"1": {"Node Type": "Seq Scan"}}},
        )
        write_json(
            after_plan,
            {"exact_structural_plans": {"1": {"Node Type": "Index Scan"}}},
        )
        before_measurement = root / "before"
        after_measurement = root / "after"
        before_measurement.mkdir()
        after_measurement.mkdir()
        measurement_files(before_measurement, 100.0, [0.900, 0.901, 0.899])
        measurement_files(after_measurement, 110.0, [0.800, 0.801, 0.799])
        output = root / "calibration.json"
        original = sys.argv
        try:
            sys.argv = [
                "build_plan_transition_calibration.py",
                "--before-plan",
                str(before_plan),
                "--after-plan",
                str(after_plan),
                "--before-measurement-dir",
                str(before_measurement),
                "--after-measurement-dir",
                str(after_measurement),
                "--before-distance",
                "0.2",
                "--after-distance",
                "0.3",
                "--target-resource",
                "memory",
                "--hard-gate-passed",
                "--output",
                str(output),
            ]
            assert builder.main() == 0
        finally:
            sys.argv = original
        artifact = json.loads(output.read_text(encoding="utf-8"))
        assert artifact["status"] == "learnable"
        assert artifact["plan_diff"]["changed_query_ids"] == ["1"]
        assert artifact["target_error_gate"]["direction"] == "decrease"
        assert artifact["joint_improvement"]["strict_acceptance"] is True
        assert artifact["changed_query_impact"]["shares"]["client_time_share"] == 1.0


if __name__ == "__main__":
    test_builder_emits_learnable_artifact()
    print("PASS test_builder_emits_learnable_artifact")
