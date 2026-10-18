#!/usr/bin/env python3
"""JOB ratio-scale plan-direction canary on one retained MySQL container."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark_estimator import build_resource_estimator
from mysql_cost_adapter import MySQLCostAdapter


DEFAULT_SQL_DIR = (
    ROOT
    / "history/mysql_sgd_env_agents_20260723/job"
    / "job_strict_plan_capture_20260723_0642/artifacts/sql"
)
BASELINE = {
    "io_block_read_cost": 1.0,
    "memory_block_read_cost": 0.25,
    "row_evaluate_cost": 0.1,
    "memory_temptable_row_cost": 0.1,
}
BOUNDS = {
    "io_block_read_cost": (0.1, 10.0),
    "memory_block_read_cost": (0.025, 2.5),
    "row_evaluate_cost": (0.01, 1.0),
    "memory_temptable_row_cost": (0.01, 1.0),
}
BASELINE_REAL = {
    "cpu": 0.3880099008345266,
    "memory": 0.8912797772539265,
    "io": 0.47087386855668806,
}


class DockerExecClient:
    def __init__(self, container: str, password: str, database: str) -> None:
        self.container = container
        self.password = password
        self.database = database

    def execute(self, sql: str) -> str:
        proc = subprocess.run(
            [
                "docker", "exec", "-i", self.container,
                "mysql", "-uroot", f"-p{self.password}",
                "--batch", "--raw", "--skip-column-names",
                f"--database={self.database}", "--execute", sql,
            ],
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout)[-4000:])
        return proc.stdout


def queries(sql_dir: Path) -> list[dict[str, Any]]:
    paths = sorted(sql_dir.glob("*.sql"))
    if len(paths) != 9:
        raise RuntimeError(f"expected 9 frozen JOB SQL files, found {len(paths)}")
    weights = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 30.0, 10.0, 7.5]
    return [
        {
            "query_id": path.stem,
            "sql": path.read_text(encoding="utf-8").strip().rstrip(";"),
            "weight": weight,
        }
        for path, weight in zip(paths, weights)
    ]


def point_id(params: Mapping[str, float]) -> str:
    changed = [name for name in BASELINE if params[name] != BASELINE[name]]
    if not changed:
        return "baseline"
    name = changed[0]
    side = "low" if params[name] == BOUNDS[name][0] else "high"
    return f"{name}__{side}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default="ra-mysql-grid-job-w2")
    parser.add_argument("--password", default='CHANGE_ME')
    parser.add_argument("--database", default="imdb_job")
    parser.add_argument("--sql-dir", type=Path, default=DEFAULT_SQL_DIR)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "history/ratio_scale_job_canary_20260726/plan_scan.json",
    )
    args = parser.parse_args()

    client = DockerExecClient(args.container, args.password, args.database)
    adapter = MySQLCostAdapter(client)
    original = adapter.snapshot()
    config = {
        "type": "ratio_scale",
        "baseline_real": BASELINE_REAL,
        "baseline_params": BASELINE,
        "ratio_min": 0.05,
        "ratio_max": 20.0,
        "concurrency": 1,
        "memory_bytes_per_unit": 1.0,
        "cache_profile": {
            "sequential_hit_rate": 0.85,
            "random_hit_rate": 0.30,
        },
        "feature_source": {"queries": queries(args.sql_dir)},
    }
    estimator = build_resource_estimator(adapter, config)
    points = [copy.deepcopy(BASELINE)]
    for name, (lower, upper) in BOUNDS.items():
        for value in (lower, upper):
            point = copy.deepcopy(BASELINE)
            point[name] = value
            points.append(point)

    rows: list[dict[str, Any]] = []
    try:
        for params in points:
            resource = estimator(None, params)
            metadata = resource["_metadata"]
            rows.append(
                {
                    "point_id": point_id(params),
                    "params": params,
                    "resource": {name: resource[name] for name in ("cpu", "memory", "io")},
                    "ratios": metadata["feature_ratios"],
                    "ratios_unclipped": metadata["feature_ratios_unclipped"],
                    "baseline_features": metadata["baseline_features"],
                    "current_features": metadata["current_features"],
                    "candidate_work": metadata["candidate_work"],
                    "plan_work_sha256": metadata["plan_work_sha256"],
                    "same_plan_work_as_baseline": (
                        metadata["plan_work_sha256"]
                        == metadata["baseline_plan_work_sha256"]
                    ),
                    "cost_parameter_free_features": metadata["cost_parameter_free_features"],
                }
            )
    finally:
        adapter.restore(original)

    baseline_row = rows[0]
    candidates = [
        row
        for row in rows[1:]
        if not row["same_plan_work_as_baseline"]
        and row["resource"]["memory"] < baseline_row["resource"]["memory"]
        and row["resource"]["cpu"] < 0.8
        and row["resource"]["io"] < 0.8
    ]
    selected = min(
        candidates,
        key=lambda row: (
            row["resource"]["memory"],
            max(row["resource"]["cpu"], row["resource"]["io"]),
            row["point_id"],
        ),
        default=None,
    )
    same_plan_drift = [
        max(abs(row["ratios"][name] - 1.0) for name in ("cpu", "memory", "io"))
        for row in rows
        if row["same_plan_work_as_baseline"]
    ]
    payload = {
        "schema_version": "mysql_ratio_scale_job_plan_canary_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "container": args.container,
        "database": args.database,
        "baseline_real_source": (
            "job direct_20260724T203107_round_001; direction canary only"
        ),
        "baseline": baseline_row,
        "points": rows,
        "selected_cross_plan_candidate": selected,
        "selected_candidate_gate": {
            "target_memory_strictly_below_baseline": True,
            "predicted_cpu_below": 0.8,
            "predicted_io_below": 0.8,
        },
        "same_plan_max_ratio_drift": max(same_plan_drift, default=0.0),
        "same_plan_cost_drift_eliminated": (
            max(same_plan_drift, default=0.0) <= 1e-12
        ),
        "snapshot_restored": all(
            math.isclose(
                adapter.effective_values()[item.name], item.effective_value,
                rel_tol=0.0, abs_tol=1e-12,
            )
            for item in original
            if item.name in BASELINE
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
