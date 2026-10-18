#!/usr/bin/env python3
"""One safe JOB-9 real pair for the MySQL ratio-scale direction canary."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PG_ROOT = Path("/home/anonymous/program/resource_adaptive")
for path in (PG_ROOT, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from mysql_cost_adapter import MySQLCostAdapter
from mysql_live_measurement import summarize_samples
from mysql_resource_monitor import _aggregate
from mysql_workload_adapter import MySQLEndpoint, build_job_manifest, run_job


SQL_DIR = (
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
CANDIDATE = {**BASELINE, "row_evaluate_cost": 1.0}
PREDICTED_RATIOS = {
    "cpu": 0.995885266330329,
    "memory": 1.0,
    "io": 0.8771550344673437,
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
        if proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout)[-4000:])
        return proc.stdout


class DockerMysqlRunner:
    def __init__(self, container: str, password: str, database: str) -> None:
        self.argv = [
            "docker", "exec", "-i", container,
            "mysql", "-uroot", f"-p{password}",
            "--batch", "--raw", "--skip-column-names",
            f"--database={database}",
        ]

    def __call__(self, _argv, **kwargs):
        return subprocess.run(
            self.argv,
            input=kwargs.get("input"),
            cwd=kwargs.get("cwd"),
            text=True,
            capture_output=True,
            timeout=kwargs.get("timeout"),
            check=False,
        )


def measure_docker_job(
    manifest: Path,
    output: Path,
    endpoint: MySQLEndpoint,
    runner: DockerMysqlRunner,
    mysqld_pid: int,
) -> dict[str, Any]:
    clock_ticks = float(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
    samples: list[dict[str, float]] = []
    started = time.monotonic()
    previous_time = started
    previous = _aggregate([mysqld_pid])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_job,
            manifest,
            endpoint,
            output,
            concurrency=1,
            repetitions=1,
            mysql_binary="docker-mysql-wrapper",
            dry_run=False,
            timeout_s=900.0,
            runner=runner,
        )
        while not future.done():
            if time.monotonic() - started > 900.0:
                raise TimeoutError("JOB-9 canary exceeded 900 seconds")
            time.sleep(1.0)
            now = time.monotonic()
            current = _aggregate([mysqld_pid])
            elapsed = max(now - previous_time, 1e-9)
            rss_gib = max(0.0, current[1]) / 1024.0 / 1024.0
            samples.append(
                {
                    "cpu": max(0.0, current[0] - previous[0]) / clock_ticks / elapsed,
                    "memory": rss_gib,
                    "rss_memory": rss_gib,
                    "io": max(
                        0.0,
                        current[2] + current[3] - previous[2] - previous[3],
                    )
                    / 1024.0
                    / 1024.0
                    / elapsed,
                }
            )
            previous_time, previous = now, current
        result = future.result()
    resource = summarize_samples(samples, 1)
    metrics = result["metrics"]
    return {
        "db_qps": float(metrics["qps"]),
        "resource": resource,
        "latency_p95_ms": float(metrics["latency_p95_ms"]),
        "latency_p99_ms": float(metrics["latency_p99_ms"]),
        "elapsed_s": time.monotonic() - started,
        "sample_count": len(samples),
        "adapter_result": str(output / "adapter_result.json"),
    }


def ratios(
    candidate: dict[str, Any], baseline: dict[str, Any]
) -> tuple[dict[str, float | None], dict[str, str]]:
    values: dict[str, float | None] = {}
    invalid: dict[str, str] = {}
    for name in ("cpu", "memory", "io"):
        candidate_value = float(candidate["resource"][name])
        baseline_value = float(baseline["resource"][name])
        if not math.isfinite(baseline_value) or baseline_value <= 0.0:
            values[name] = None
            invalid[name] = f"invalid baseline resource value: {baseline_value!r}"
            continue
        ratio = candidate_value / baseline_value
        if not math.isfinite(ratio):
            values[name] = None
            invalid[name] = f"non-finite candidate/baseline ratio: {ratio!r}"
            continue
        values[name] = ratio
    return values, invalid


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default="ra-mysql-grid-job-w2")
    parser.add_argument("--root-password", default='CHANGE_ME')
    parser.add_argument("--database", default="imdb_job")
    parser.add_argument("--port", type=int, default=13412)
    parser.add_argument("--mysqld-pid", type=int, default=3147151)
    parser.add_argument("--query-limit", type=int, default=9)
    parser.add_argument("--skip-warmup", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "history/ratio_scale_job_canary_20260726/real_pair_v3",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    sql_paths = sorted(SQL_DIR.glob("*.sql"))
    if len(sql_paths) != 9:
        raise RuntimeError(f"expected 9 JOB SQL files, found {len(sql_paths)}")
    if not 1 <= args.query_limit <= len(sql_paths):
        raise ValueError("query-limit must be in 1..9")
    sql_paths = sql_paths[: args.query_limit]
    manifest = build_job_manifest(sql_paths, args.output_dir / "manifest")
    root = DockerExecClient(args.container, args.root_password, args.database)
    adapter = MySQLCostAdapter(root)
    snapshot = adapter.snapshot()
    endpoint = MySQLEndpoint("docker", 3306, args.database, "root", "")
    runner = DockerMysqlRunner(args.container, args.root_password, args.database)
    payload: dict[str, Any] = {}
    restored = False
    try:
        adapter.apply(BASELINE)
        warmup = (
            {
                "reused_completed_prior_warmup": True,
                "evidence": str(
                    ROOT
                    / "history/ratio_scale_job_canary_20260726/real_pair_v5/warmup/adapter_result.json"
                ),
            }
            if args.skip_warmup
            else measure_docker_job(
                manifest,
                args.output_dir / "warmup",
                endpoint,
                runner,
                args.mysqld_pid,
            )
        )
        baseline = measure_docker_job(
            manifest,
            args.output_dir / "baseline",
            endpoint,
            runner,
            args.mysqld_pid,
        )
        adapter.apply(CANDIDATE)
        candidate = measure_docker_job(
            manifest,
            args.output_dir / "candidate",
            endpoint,
            runner,
            args.mysqld_pid,
        )
        actual_ratios, invalid_ratios = ratios(candidate, baseline)
        direction_matches = {}
        for name in ("cpu", "memory", "io"):
            actual_ratio = actual_ratios[name]
            if actual_ratio is None:
                continue
            direction_matches[name] = (
                math.copysign(1.0, PREDICTED_RATIOS[name] - 1.0)
                == math.copysign(1.0, actual_ratio - 1.0)
                if not math.isclose(
                    PREDICTED_RATIOS[name], 1.0, abs_tol=1e-12
                )
                else math.isclose(actual_ratio, 1.0, rel_tol=0.05)
            )
        payload = {
            "schema_version": "mysql_ratio_scale_job_real_pair_canary_v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "formal_result": False,
            "reason": "single frozen JOB subset pair validates direction only",
            "query_count": len(sql_paths),
            "baseline_params": BASELINE,
            "candidate_params": CANDIDATE,
            "predicted_ratios": PREDICTED_RATIOS,
            "actual_ratios": actual_ratios,
            "direction_matches": direction_matches,
            "measurement_status": "inconclusive" if invalid_ratios else "valid",
            "invalid_ratio_reasons": invalid_ratios,
            "warmup": warmup,
            "baseline": baseline,
            "candidate": candidate,
        }
    finally:
        adapter.restore(snapshot)
        restored = all(
            math.isclose(
                adapter.effective_values()[item.name], item.effective_value,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            for item in snapshot
            if item.name in BASELINE
        )

    payload["snapshot_restored"] = restored
    result_path = args.output_dir / "result.json"
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
