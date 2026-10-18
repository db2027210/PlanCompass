#!/usr/bin/env python3
"""Print a read-only live JOB R0 status snapshot."""

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


def epoch(value: str) -> float:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def main() -> int:
    root = LANE_ROOT / "history" / "job" / "baseline" / "R0" / "attempt_1"
    raw_samples = [json.loads(line) for line in (root / "measurement_resource_samples.jsonl").read_text().splitlines()]
    queries = [json.loads(line) for line in (root / "measurement_queries.jsonl").read_text().splitlines()]
    shared_config = LANE_ROOT / "bundle" / "frozen"
    points.SHARED_CONFIG = shared_config
    engine.SHARED_CONFIG = shared_config
    samples = points._stability_samples_from_evidence(
        "job", "memory", root / "measurement_resource_samples.jsonl",
        root / "measurement_queries.jsonl", None,
    )
    start_epoch = epoch(raw_samples[0]["timestamp_utc"])
    end_epoch = epoch(raw_samples[-1]["timestamp_utc"]) + 1.0
    assessment = points.resource_stability(
        samples,
        "job",
        measurement_start_epoch_seconds=start_epoch,
        now_epoch_seconds=end_epoch,
    )
    resource_means = {
        field: statistics.fmean(float(row[f"{field}_utilization"]) for row in samples)
        for field in ("cpu", "memory", "io")
    }
    output = {
        "measurement_elapsed_seconds_from_samples": end_epoch - start_epoch,
        "sample_count": len(raw_samples),
        "query_count": len(queries),
        "error_count": sum(row.get("success") is not True for row in queries),
        "resource_mean_utilization": resource_means,
        "preliminary_stability_from_first_sample_alignment": assessment,
        "formal_online_check_eligible": end_epoch - start_epoch >= 180.0,
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
