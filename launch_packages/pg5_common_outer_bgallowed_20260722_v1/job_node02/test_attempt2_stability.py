#!/usr/bin/env python3
"""Focused self-test for the JOB attempt2 aggregate-QPS stability rule."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "attempt2_workspace"))

from formal_measurement import FrozenMeasurementProtocol, assess_measurement_samples


def protocol() -> FrozenMeasurementProtocol:
    return FrozenMeasurementProtocol(
        sample_interval_seconds=1,
        warmup_seconds=60,
        minimum_measurement_seconds=180,
        maximum_measurement_seconds=600,
        stability_window_seconds=30,
        required_consecutive_stability_windows=6,
        sample_coverage_min=0.9,
        cpu_cv_max=0.05,
        memory_cv_max=0.03,
        io_cv_max=0.05,
        normal_throughput_cv_max=0.05,
        xp_xap_qps_cv_max=0.1,
        xp_xtp_tps_cv_max=0.1,
        low_utilization_ratio_threshold=0.1,
        low_utilization_absolute_range_max=0.05,
        normal_throughput_calculation=(
            "per_window_mean_then_cv_across_latest_six_windows"
        ),
    )


def samples(window_qps_counts: list[int], *, omitted_slots: set[int] | None = None):
    omitted = omitted_slots or set()
    rows = []
    for window, count in enumerate(window_qps_counts):
        completion_slots = set(range(count))
        for offset in range(30):
            slot = window * 30 + offset
            if slot in omitted:
                continue
            rows.append(
                {
                    "epoch_seconds": slot + 0.5,
                    "cpu_utilization": 0.4,
                    "memory_utilization": 0.9,
                    "io_utilization": 0.01,
                    "qps": 1.0 if offset in completion_slots else 0.0,
                }
            )
    return rows


def assess(rows):
    return assess_measurement_samples(
        rows,
        measurement_start_epoch_seconds=0.0,
        now_epoch_seconds=180.0,
        xp=False,
        protocol=protocol(),
    )


def main() -> int:
    bursty = assess(samples([3, 3, 3, 3, 3, 3]))
    assert bursty.stable is True
    assert bursty.earliest_stable_stop_seconds == 180
    assert bursty.consecutive_stable_windows == 6
    assert len(bursty.throughput_blocks) == 1
    assert bursty.throughput_blocks[0].coefficient_of_variation == 0.0
    assert all(window.stable for window in bursty.windows)

    variable = assess(samples([1, 2, 3, 4, 5, 6]))
    assert variable.stable is False
    assert variable.throughput_blocks[0].stable is False

    low_coverage = assess(samples([3] * 6, omitted_slots={0, 1, 2, 3}))
    assert low_coverage.stable is False
    assert low_coverage.windows[0].coverage_passed is False

    unstable_resource_rows = samples([3] * 6)
    for row in unstable_resource_rows[:30]:
        row["cpu_utilization"] = 0.2 if int(row["epoch_seconds"]) % 2 else 0.8
    unstable_resource = assess(unstable_resource_rows)
    assert unstable_resource.stable is False
    assert unstable_resource.throughput_blocks[0].resource_windows_stable is False
    print("attempt2 aggregate-QPS stability self-test: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
