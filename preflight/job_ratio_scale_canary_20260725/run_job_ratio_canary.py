#!/usr/bin/env python3
"""Run an interleaved 3+3 JOB canary without touching outer-loop ledgers."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)


ORDER = (
    ("baseline", 1),
    ("candidate", 1),
    ("baseline", 2),
    ("candidate", 2),
    ("baseline", 3),
    ("candidate", 3),
)


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--estimator-evidence", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_root.resolve()
    if root.exists():
        raise RuntimeError(f"immutable canary root already exists: {root}")
    root.mkdir(parents=True, mode=0o700)

    sys.path.insert(0, str(LANE_ROOT / "launch"))
    import job_lane_baseline_attempt6 as lane
    import p24_pg_recost_runner as p24

    p24.run = lane.lane_run
    p24.CASES["job"]["name"] = lane.CONTAINER_NAME
    p24.CASES["job"]["port"] = lane.PORT
    _method, points, source_validation = p24.configure_modules("job")
    import formal_pg_engine as engine

    engine.run = lane.lane_run
    points.run = lane.lane_run
    points.host_measurement_lock = lane.lane_lock
    points.start_container = (
        lambda kind, pgdata, profile_name="none", cost_parameters=None:
        lane.start_lane_container(
            engine, kind, pgdata, profile_name, cost_parameters
        )
    )
    points.cleanup_overlay = (
        lambda kind, restore: lane.cleanup_runtime(engine, kind, restore)
    )

    baseline = points.default_cost_parameters()
    candidate = dict(baseline)
    candidate["parallel_setup_cost"] = 1007.9
    estimator_sha = hashlib.sha256(args.estimator_evidence.read_bytes()).hexdigest()
    manifest: Dict[str, Any] = {
        "schema_version": 1,
        "status": "pre_registered_before_real_replay",
        "benchmark": "JOB-8-mixed-26a-weighted",
        "case_id": "pg_job_memory",
        "target_resource": "memory",
        "baseline": baseline,
        "candidate": candidate,
        "candidate_source": {
            "plan_scan": "job_read_only_scan_v1/points/point_015.json",
            "selection": "first stable exact-plan point across refined boundary",
            "estimator_evidence_sha256": estimator_sha,
        },
        "execution_order": [f"{kind}_r{repeat}" for kind, repeat in ORDER],
        "fresh_template_each_replay": True,
        "warmup_seconds": 60,
        "measurement_seconds": {"minimum": 180, "maximum": 600},
        "acceptance": {
            "qps": "lower95(candidate - baseline) > 0",
            "target_memory": "upper95(candidate - baseline) < 0",
            "candidate_cpu": "upper95 < 0.80",
            "candidate_io": "upper95 < 0.80",
            "target_memory_after": "upper95 < 0.85",
        },
        "primary_goal": {
            "qps_improvement_min_ratio": 0.15,
            "memory_pressure_drop_min_ratio": 0.05,
        },
        "workload_or_data_change": False,
        "outer_counter_or_ledger_write": False,
        "lane_source_validation": source_validation,
        "created_at_epoch": time.time(),
    }
    write_json(root / "manifest.json", manifest)
    state = {"status": "running", "completed": [], "started_at_epoch": time.time()}
    write_json(root / "campaign_state.json", state)

    for kind, repeat in ORDER:
        point_id = f"ratio_canary_{kind}"
        output = root / f"{kind}_r{repeat}"
        params = baseline if kind == "baseline" else candidate
        result = points.run_cost_point(
            "pg_job_memory",
            point_id,
            repeat,
            output,
            params,
            evidence_context={
                "purpose": "ratio_scale_direction_and_step_canary",
                "immutable_manifest": str(root / "manifest.json"),
                "estimator_evidence": str(args.estimator_evidence.resolve()),
                "outer_counter_or_ledger_write": False,
            },
            warmup_seconds=60,
            measurement_seconds=600,
        )
        if result.get("status") != "complete":
            raise RuntimeError(f"point did not complete: {kind}_r{repeat}")
        state["completed"].append(f"{kind}_r{repeat}")
        state["last_completed_at_epoch"] = time.time()
        write_json(root / "campaign_state.json", state)

    state["status"] = "complete"
    state["completed_at_epoch"] = time.time()
    write_json(root / "campaign_state.json", state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
