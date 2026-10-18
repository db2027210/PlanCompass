#!/usr/bin/env python3
"""Frozen measurement-duration gate for MySQL Algorithms B/C.

The search flow remains the PostgreSQL double-loop flow.  This module is only
the MySQL physical-measurement boundary.  Native XP uses six consecutive
30-second target-resource windows beginning at 300 seconds.  Full JOB and
TPC-H are completion-bounded suites: one complete canonical suite within the
900-second hard timeout is valid without a minimum duration or a stability
requirement.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import statistics
import threading
import time
from typing import Any, Mapping, Sequence


MINIMUM_MEASUREMENT_SECONDS = 300
XP_MAXIMUM_MEASUREMENT_SECONDS = 900
SUITE_HARD_TIMEOUT_SECONDS = 900
DECISION_INTERVAL_SECONDS = 30
WINDOW_SECONDS = 30
WINDOW_COUNT = 6
WINDOW_COVERAGE_FRACTION = 0.90
MAX_WINDOW_MEAN_CV = 0.10
LOW_MEAN_THRESHOLD = 0.10
LOW_MEAN_MAX_RANGE = 0.05

SCENE_TARGET_RESOURCE = {
    "job_memory": "memory",
    "tpch_sf4_memory": "memory",
    "hybench_xp_cpu": "cpu",
    "hybench_xp_io": "io",
}


def _atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _atomic_stop_request(path: Path, checkpoint_seconds: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(f"{checkpoint_seconds}\n", encoding="ascii")
    os.replace(temporary, path)


def _interval_samples(
    rows: Sequence[Mapping[str, Any]], worker: Mapping[str, Any], started_monotonic: float,
) -> list[dict[str, float]]:
    ordered = sorted(
        (row for row in rows if float(row["captured_monotonic"]) >= started_monotonic),
        key=lambda row: float(row["captured_monotonic"]),
    )
    intervals: list[dict[str, float]] = []
    for previous, current in zip(ordered, ordered[1:]):
        previous_time = float(previous["captured_monotonic"])
        current_time = float(current["captured_monotonic"])
        elapsed = current_time - previous_time
        if elapsed <= 0 or elapsed > 5.0:
            continue
        read_bps = max(0.0, float(current["read_bytes"]) - float(previous["read_bytes"])) / elapsed
        write_bps = max(0.0, float(current["write_bytes"]) - float(previous["write_bytes"])) / elapsed
        intervals.append({
            "relative_end_seconds": current_time - started_monotonic,
            "elapsed_seconds": elapsed,
            "cpu": (
                max(0.0, float(current["cpu_ns"]) - float(previous["cpu_ns"]))
                / 1e9 / elapsed / float(worker["cpu_quota_cores"])
            ),
            "memory": float(current["memory_working_set_bytes"]) / float(worker["memory_bytes"]),
            "io": max(
                read_bps / float(worker["io_read_bps"]),
                write_bps / float(worker["io_write_bps"]),
            ),
        })
    return intervals


def evaluate_target_resource_stability(
    rows: Sequence[Mapping[str, Any]],
    worker: Mapping[str, Any],
    started_monotonic: float,
    checkpoint_seconds: int,
    target_resource: str,
) -> dict[str, Any]:
    """Evaluate one checkpoint without considering QPS or non-target resources."""
    if target_resource not in {"cpu", "memory", "io"}:
        raise ValueError(f"unsupported target resource: {target_resource}")
    if checkpoint_seconds < MINIMUM_MEASUREMENT_SECONDS or checkpoint_seconds % 30:
        raise ValueError("checkpoint must be a 30-second multiple at or after 300 seconds")
    intervals = _interval_samples(rows, worker, started_monotonic)
    windows: list[dict[str, Any]] = []
    means: list[float] = []
    coverage_passed = True
    first_window_start = checkpoint_seconds - WINDOW_SECONDS * WINDOW_COUNT
    for index in range(WINDOW_COUNT):
        window_start = first_window_start + index * WINDOW_SECONDS
        window_end = window_start + WINDOW_SECONDS
        selected = [
            row for row in intervals
            if window_start < row["relative_end_seconds"] <= window_end
        ]
        covered = sum(row["elapsed_seconds"] for row in selected)
        values = [row[target_resource] for row in selected]
        window_ok = covered >= WINDOW_SECONDS * WINDOW_COVERAGE_FRACTION and bool(values)
        coverage_passed = coverage_passed and window_ok
        mean = statistics.fmean(values) if values else math.nan
        means.append(mean)
        windows.append({
            "index": index + 1,
            "start_seconds": window_start,
            "end_seconds": window_end,
            "sample_count": len(values),
            "covered_seconds": covered,
            "coverage_passed": window_ok,
            "target_mean": mean if math.isfinite(mean) else None,
        })
    finite = coverage_passed and all(math.isfinite(value) for value in means)
    overall_mean = statistics.fmean(means) if finite else math.nan
    window_range = max(means) - min(means) if finite else math.nan
    cv = (
        statistics.pstdev(means) / overall_mean
        if finite and overall_mean > 0 else math.inf
    )
    low_mean_mode = finite and overall_mean < LOW_MEAN_THRESHOLD
    variability_passed = bool(
        finite and (
            window_range <= LOW_MEAN_MAX_RANGE
            if low_mean_mode else cv <= MAX_WINDOW_MEAN_CV
        )
    )
    return {
        "schema_version": "mysql_target_resource_six_window_stability_v1",
        "worker_id": str(worker["worker_id"]),
        "target_resource": target_resource,
        "checkpoint_seconds": checkpoint_seconds,
        "minimum_measurement_seconds": MINIMUM_MEASUREMENT_SECONDS,
        "decision_interval_seconds": DECISION_INTERVAL_SECONDS,
        "window_seconds": WINDOW_SECONDS,
        "window_count": WINDOW_COUNT,
        "window_coverage_fraction": WINDOW_COVERAGE_FRACTION,
        "maximum_window_mean_cv": MAX_WINDOW_MEAN_CV,
        "qps_participates_in_stability": False,
        "coverage_passed": coverage_passed,
        "low_mean_mode": low_mean_mode,
        "window_mean_cv": cv if math.isfinite(cv) else None,
        "window_mean_range": window_range if math.isfinite(window_range) else None,
        "variability_passed": variability_passed,
        "stable": coverage_passed and variability_passed,
        "windows": windows,
    }


def target_for_scene(scene: str) -> str:
    try:
        return SCENE_TARGET_RESOURCE[scene]
    except KeyError as error:
        raise ValueError(f"scene has no frozen target resource: {scene}") from error


def completed_suite_stability(
    rows: Sequence[Mapping[str, Any]], worker: Mapping[str, Any],
    started_monotonic: float, finished_monotonic: float,
) -> dict[str, Any]:
    """Validate a completed JOB/TPC-H suite without a minimum wall time.

    Coverage is deliberately checked by the caller because it has the
    authoritative canonical SQL manifest.  This function only records that a
    returned relational suite stayed inside the frozen 900-second hard cap.
    Resource samples remain measurements, but their window stability does not
    decide JOB/TPC-H validity.
    """
    scene = str(worker["scene"])
    if scene not in {"job_memory", "tpch_sf4_memory"}:
        raise ValueError("completed-suite duration policy is relational-only")
    elapsed = max(0.0, finished_monotonic - started_monotonic)
    checkpoint = min(
        SUITE_HARD_TIMEOUT_SECONDS,
        int(elapsed // DECISION_INTERVAL_SECONDS) * DECISION_INTERVAL_SECONDS,
    )
    within_hard_timeout = elapsed <= SUITE_HARD_TIMEOUT_SECONDS
    return {
        "schema_version": "mysql_relational_full_suite_completion_gate_v1",
        "worker_id": str(worker["worker_id"]),
        "scene": scene,
        "target_resource": target_for_scene(scene),
        "elapsed_seconds": elapsed,
        "checkpoint_seconds_diagnostic": checkpoint,
        "minimum_measurement_seconds": None,
        "minimum_measurement_required": False,
        "target_resource_stability_required": False,
        "resource_samples_remain_measurements": True,
        "full_canonical_suite_required": True,
        "coverage_complete_is_separate_required_gate": True,
        "suite_hard_timeout_seconds": SUITE_HARD_TIMEOUT_SECONDS,
        "within_hard_timeout": within_hard_timeout,
        "stable": within_hard_timeout,
        "reason": (
            "full_suite_completed_within_hard_timeout"
            if within_hard_timeout
            else "full_suite_exceeded_hard_timeout"
        ),
    }


def monitor_xp_stop_request(
    *, sampler: Any, worker: Mapping[str, Any], started_monotonic: float,
    stop_file: Path, evidence_file: Path, workload_done: threading.Event,
) -> dict[str, Any]:
    """Publish the first stable XP checkpoint; otherwise stop at the 900s cap."""
    worker_id = str(worker["worker_id"])
    target = target_for_scene(str(worker["scene"]))
    decisions: list[dict[str, Any]] = []
    next_checkpoint = MINIMUM_MEASUREMENT_SECONDS
    while next_checkpoint <= XP_MAXIMUM_MEASUREMENT_SECONDS:
        deadline = started_monotonic + next_checkpoint
        while time.monotonic() < deadline:
            if workload_done.wait(min(0.5, max(0.0, deadline - time.monotonic()))):
                break
        # The sampler can trail the wall clock slightly; allow its next row.
        sample_deadline = time.monotonic() + 3.0
        while (
            not workload_done.is_set()
            and time.monotonic() < sample_deadline
            and (
                not sampler.rows.get(worker_id)
                or float(sampler.rows[worker_id][-1]["captured_monotonic"])
                < deadline - 0.5
            )
        ):
            time.sleep(0.1)
        decision = evaluate_target_resource_stability(
            list(sampler.rows.get(worker_id, ())), worker, started_monotonic,
            next_checkpoint, target,
        )
        decisions.append(decision)
        if decision["stable"]:
            _atomic_stop_request(stop_file, next_checkpoint)
            break
        if workload_done.is_set() or next_checkpoint == XP_MAXIMUM_MEASUREMENT_SECONDS:
            break
        next_checkpoint += DECISION_INTERVAL_SECONDS
    document = {
        "schema_version": "mysql_xp_adaptive_stop_v1",
        "worker_id": worker_id,
        "target_resource": target,
        "minimum_measurement_seconds": MINIMUM_MEASUREMENT_SECONDS,
        "maximum_measurement_seconds": XP_MAXIMUM_MEASUREMENT_SECONDS,
        "hard_maximum_empirical_basis": "recent_100_measurements_plus_v33_570s_and_v34_660s_io_stability_failures",
        "decision_interval_seconds": DECISION_INTERVAL_SECONDS,
        "stable": bool(decisions and decisions[-1]["stable"]),
        "selected_measurement_seconds": (
            decisions[-1]["checkpoint_seconds"] if decisions else None
        ),
        "stop_request_published": stop_file.is_file(),
        "decisions": decisions,
    }
    _atomic_json(evidence_file, document)
    return document
