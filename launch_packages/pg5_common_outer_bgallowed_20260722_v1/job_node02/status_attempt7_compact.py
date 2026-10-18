#!/usr/bin/env python3
"""Print a compact, exact-clock live snapshot for JOB R0 attempt7."""

from __future__ import annotations

import json
import os
from pathlib import Path
import statistics
import subprocess
import time

import formal_pg_engine as engine
import formal_pg_point_runner as points


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
ROOT = LANE_ROOT / "history" / "job" / "baseline" / "R0" / "attempt_7"
FROZEN = LANE_ROOT / "bundle" / "attempt7_frozen"


def java_measurement_start_epoch() -> tuple[int, float]:
    rows = subprocess.run(
        ["ps", "-eo", "pid,args"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.splitlines()
    pid = int(
        next(
            line.split(None, 1)[0]
            for line in rows
            if "FormalSqlLoop" in line
            and "attempt_7/measurement_queries.jsonl" in line
        )
    )
    ticks = int(Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[21])
    clock_ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    uptime = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    now = time.time()
    return pid, now - uptime + ticks / clock_ticks


def main() -> int:
    result_path = ROOT / "point_result.json"
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        measurement = result.get("measurement") or {}
        stability = measurement.get("resource_stability") or {}
        blocks = stability.get("cross_window_blocks") or []
        print(
            json.dumps(
                {
                    "phase": "terminal",
                    "status": result.get("status"),
                    "measurement_termination_reason": measurement.get(
                        "measurement_termination_reason"
                    ),
                    "effective_measurement_seconds": measurement.get(
                        "effective_measurement_seconds"
                    ),
                    "sample_count": measurement.get("sample_count"),
                    "sample_coverage": measurement.get("sample_coverage"),
                    "query_count": measurement.get("query_count"),
                    "resource_mean_utilization": measurement.get(
                        "resource_mean_utilization"
                    ),
                    "stable": stability.get("stable"),
                    "earliest_stable_stop_seconds": stability.get(
                        "earliest_stable_stop_seconds"
                    ),
                    "latest_cross_window_block": blocks[-1] if blocks else None,
                    "cleanup": result.get("cleanup"),
                    "error": result.get("error"),
                    "error_type": result.get("error_type"),
                },
                sort_keys=True,
                indent=2,
            ),
            flush=True,
        )
        return 0
    sample_path = ROOT / "measurement_resource_samples.jsonl"
    query_path = ROOT / "measurement_queries.jsonl"
    if not sample_path.is_file():
        print(json.dumps({"phase": "pre_measurement"}), flush=True)
        return 0
    engine.SHARED_CONFIG = FROZEN
    points.SHARED_CONFIG = FROZEN
    samples = points._stability_samples_from_evidence(
        "job", "memory", sample_path, query_path, None
    )
    java_pid, start_epoch = java_measurement_start_epoch()
    now = time.time()
    assessment = points.resource_stability(
        samples,
        "job",
        measurement_start_epoch_seconds=start_epoch,
        now_epoch_seconds=now,
    )
    blocks = assessment.get("cross_window_blocks") or []
    latest = blocks[-1] if blocks else None
    output = {
        "phase": "measurement",
        "java_pid": java_pid,
        "elapsed_seconds": now - start_epoch,
        "sample_count": len(samples),
        "aggregate_sample_coverage": assessment.get("aggregate_sample_coverage"),
        "complete_window_count": assessment.get("complete_window_count"),
        "query_count": sum(1 for _ in query_path.open(encoding="utf-8")),
        "full_period_means": {
            field: statistics.fmean(float(row[field]) for row in samples)
            for field in (
                "qps",
                "cpu_utilization",
                "memory_utilization",
                "io_utilization",
            )
        },
        "stable": assessment.get("stable"),
        "earliest_stable_stop_seconds": assessment.get(
            "earliest_stable_stop_seconds"
        ),
        "latest_cross_window_block": latest,
    }
    print(json.dumps(output, sort_keys=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
