#!/usr/bin/env python3
"""Compare plan-formula and ratio-scale estimates at the JOB plan boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any, Dict, List

from plan_resource_estimator import estimate_plan_resources
from ratio_scale_resource_estimator import RatioScaleResourceEstimator


QUERY_IDS = ("1a", "5a", "9a", "12a", "16a", "20a", "26a", "33a")
QUERY_WEIGHTS = (10, 10, 10, 10, 10, 10, 30, 10)
DEFAULT_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
POINTS = {
    "baseline": DEFAULT_PARAMS,
    "same_plan_plateau": {**DEFAULT_PARAMS, "parallel_setup_cost": 1003.94},
    "first_cross_boundary": {**DEFAULT_PARAMS, "parallel_setup_cost": 1007.9},
}


def run(command: List[str], *, stdin: str | None = None) -> str:
    completed = subprocess.run(
        command,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"command failed ({completed.returncode}): {detail}")
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slave", default="anonymous@192.0.2.4")
    parser.add_argument("--container", default="ra_pg5_job_node02_v1_outercontrol")
    parser.add_argument("--database", default="imdb_job_plan_sensitive_v1")
    parser.add_argument(
        "--sql-dir",
        default=(
            "/mnt/nvme2n1/anonymous/resource_adaptive/"
            "pg5_common_outer_bgallowed_20260722_v1/job_node02/assets/job_sql"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    queries: List[Dict[str, Any]] = []
    for query_id, weight in zip(QUERY_IDS, QUERY_WEIGHTS):
        sql = run(["ssh", args.slave, "cat", f"{args.sql_dir}/{query_id}.sql"])
        queries.append({"queryid": query_id, "query": sql, "replay_count": weight})

    def psql(sql: str) -> str:
        return run(
            [
                "ssh", args.slave,
                "docker", "exec", "-i", args.container,
                "psql", "-X", "-q", "-A", "-t", "-F", "\t",
                "-v", "ON_ERROR_STOP=1", "-U", "postgres",
                "-d", args.database,
            ],
            stdin=sql,
        )

    # The units match Algorithm C: cores, GiB, and MiB/s.  This anchor is from
    # the latest complete historical JOB default replay and only sets scale;
    # the final comparison uses the new interleaved canary baselines.
    baseline_anchor = {
        "cpu": 0.40942009612536584 * 1.5,
        "memory": 0.8473264092396018 * 3.0,
        "io": 0.393952568926242 * 256.0,
    }
    ratio = RatioScaleResourceEstimator(
        pg_host="unused",
        pg_port="5432",
        pg_user="postgres",
        pg_dbname=args.database,
        estimator_cfg={
            "ratio_scale": {
                "enabled": True,
                "explain_workers": 1,
                "require_complete_explain": True,
            }
        },
        psql_query_func=psql,
        verbose=False,
    )
    calibration = ratio.calibrate(queries, DEFAULT_PARAMS, baseline_anchor)

    result: Dict[str, Any] = {
        "schema_version": 1,
        "benchmark": "JOB-8-mixed-26a-weighted",
        "target_resource": "memory",
        "points": POINTS,
        "baseline_anchor": baseline_anchor,
        "ratio_scale_calibration": calibration,
        "estimates": {},
    }
    for point_id, params in POINTS.items():
        ratio_raw, _ratio_stats, ratio_metadata = ratio.estimate_with_metadata(
            queries, params
        )
        old_error = None
        try:
            old_raw, old_stats, old_metadata = estimate_plan_resources(
                queries=queries,
                pg_host="unused",
                pg_port="5432",
                pg_user="postgres",
                pg_dbname=args.database,
                estimator_cfg={"shared": {"explain_workers": 1}},
                psql_query_func=psql,
                params=params,
                workload_context={"runtime_resource_raw": baseline_anchor},
            )
        except Exception as exc:  # Preserve ratio evidence if legacy mode fails.
            old_raw, old_stats, old_metadata = None, None, None
            old_error = f"{type(exc).__name__}: {exc}"
        result["estimates"][point_id] = {
            "ratio_scale": {
                "resource": ratio_raw,
                "feature_ratios": ratio_metadata["feature_ratios_unclipped"],
                "plan_fingerprint": ratio_metadata["workload_plan_fingerprint"],
                "query_features": ratio_metadata["query_features"],
            },
            "plan_formula": {
                "resource": old_raw,
                "stats": old_stats,
                "metadata": old_metadata,
                "error": old_error,
            },
        }

    baseline_ratio = result["estimates"]["baseline"]["ratio_scale"]["resource"]
    for point_id in ("same_plan_plateau", "first_cross_boundary"):
        resource = result["estimates"][point_id]["ratio_scale"]["resource"]
        result["estimates"][point_id]["ratio_scale"]["delta_vs_baseline"] = {
            name: resource[name] - baseline_ratio[name] for name in baseline_ratio
        }
    result["pre_registered_real_candidate"] = {
        "point_id": "first_cross_boundary",
        "parallel_setup_cost": 1007.9,
        "selection_rule": "first stable exact-plan point across the refined boundary",
        "manual_substitution": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "ratio_feature_ratios": {
            point: result["estimates"][point]["ratio_scale"]["feature_ratios"]
            for point in POINTS
        },
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
