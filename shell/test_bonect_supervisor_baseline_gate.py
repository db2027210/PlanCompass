"""Baseline strong-constraint handling tests for the Bonect supervisor."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shell.bonect_supervisor import baseline_strong_constraint_gate_trigger


def main() -> int:
    passed_event = {
        "event": "baseline_strong_constraint_gate",
        "passed": True,
        "checks": [],
    }
    assert baseline_strong_constraint_gate_trigger(passed_event) is None

    failed_event = {
        "event": "baseline_strong_constraint_gate",
        "passed": False,
        "target_resource": "cpu",
        "gate_type": "exactly_one_bottleneck",
        "timestamp": "2026-07-11 22:49:29",
        "checks": [
            {"resource": "cpu", "metric_value": 78.78, "passed": False},
            {"resource": "memory", "metric_value": 129.72, "passed": False},
            {"resource": "io", "metric_value": 19.67, "passed": True},
        ],
    }
    trigger = baseline_strong_constraint_gate_trigger(failed_event)
    assert trigger is not None
    assert trigger["name"] == "baseline_strong_constraint_gate_failed"
    assert trigger["target_resource"] == "cpu"
    assert [item["resource"] for item in trigger["failures"]] == ["cpu", "memory"]
    print("ALL Bonect baseline gate supervisor tests passed (3/3)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
