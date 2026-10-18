#!/usr/bin/env python3
"""Print a compact read-only live snapshot for JOB R0 attempt6."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import statistics

import formal_pg_engine as engine
import formal_pg_point_runner as points


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
ROOT = LANE_ROOT / "history" / "job" / "baseline" / "R0" / "attempt_6"


def epoch(value: str) -> float:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def main() -> int:
    result_path = ROOT / "point_result.json"
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        print(json.dumps({"phase": "complete", "result": result}, ensure_ascii=False), flush=True)
        return 0
    state = json.loads((ROOT / "point_state.json").read_text(encoding="utf-8"))
    sample_path = ROOT / "measurement_resource_samples.jsonl"
    query_path = ROOT / "measurement_queries.jsonl"
    if not sample_path.is_file():
        phase = "warmup" if (ROOT / "warmup_queries.jsonl").is_file() else "pre_measurement"
        print(
            json.dumps(
                {
                    "phase": phase,
                    "status": state.get("status"),
                    "warmup_query_count": sum(
                        1 for _ in (ROOT / "warmup_queries.jsonl").open(encoding="utf-8")
                    )
                    if (ROOT / "warmup_queries.jsonl").is_file()
                    else 0,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    raw_samples = [json.loads(line) for line in sample_path.read_text().splitlines()]
    queries = (
        [json.loads(line) for line in query_path.read_text().splitlines()]
        if query_path.is_file()
        else []
    )
    shared = LANE_ROOT / "bundle" / "attempt6_frozen"
    points.SHARED_CONFIG = shared
    engine.SHARED_CONFIG = shared
    samples = points._stability_samples_from_evidence(
        "job", "memory", sample_path, query_path, None
    )
    start_epoch = epoch(raw_samples[0]["timestamp_utc"])
    end_epoch = epoch(raw_samples[-1]["timestamp_utc"]) + 1.0
    assessment = points.resource_stability(
        samples,
        "job",
        measurement_start_epoch_seconds=start_epoch,
        now_epoch_seconds=end_epoch,
    )
    means = {
        field: statistics.fmean(float(row[f"{field}_utilization"]) for row in samples)
        for field in ("cpu", "memory", "io")
    }
    blocks = assessment.get("throughput_blocks") or []
    latest_block = blocks[-1] if blocks else None
    cross_blocks = assessment.get("cross_window_blocks") or []
    latest_cross_block = cross_blocks[-1] if cross_blocks else None
    output = {
        "phase": "measurement",
        "status": state.get("status"),
        "preliminary_alignment": "first_raw_sample_not_formal_start",
        "elapsed_seconds": end_epoch - start_epoch,
        "sample_count": len(raw_samples),
        "query_count": len(queries),
        "error_count": sum(row.get("success") is not True for row in queries),
        "resource_mean_utilization": means,
        "complete_window_count": assessment.get("complete_window_count"),
        "aggregate_sample_coverage": assessment.get("aggregate_sample_coverage"),
        "complete_window_coverage": [
            {
                "window_index": window.get("window_index"),
                "coverage": window.get("coverage"),
                "coverage_passed": window.get("coverage_passed"),
            }
            for window in assessment.get("windows", [])
        ],
        "latest_cross_window_block": latest_cross_block,
        "latest_throughput_block": latest_block,
        "stable": assessment.get("stable"),
        "earliest_stable_stop_seconds": assessment.get("earliest_stable_stop_seconds"),
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
