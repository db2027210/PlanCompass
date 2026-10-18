"""CLI bridge that emits one workload metrics JSON object for the monitor."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable

from mysql_workload_adapter import (
    MySQLEndpoint, build_job_manifest, run_benchbase, run_hybench, run_job,
)


ROOT = Path(__file__).resolve().parent


_TPCC_EXPLAIN = """EXPLAIN FORMAT=JSON
SELECT COUNT(DISTINCT s_i_id)
FROM order_line JOIN stock ON s_w_id=ol_w_id AND s_i_id=ol_i_id
WHERE ol_w_id=1 AND ol_d_id=1
  AND ol_o_id >= GREATEST(1, (SELECT d_next_o_id FROM district WHERE d_w_id=1 AND d_id=1)-20)
  AND ol_o_id < (SELECT d_next_o_id FROM district WHERE d_w_id=1 AND d_id=1)
  AND s_quantity < 10"""

_TPCH_EXPLAINS = {
    6: """EXPLAIN FORMAT=JSON SELECT SUM(l_extendedprice*l_discount) AS revenue
          FROM lineitem WHERE l_shipdate >= DATE '1994-01-01'
          AND l_shipdate < DATE '1995-01-01' AND l_discount BETWEEN 0.05 AND 0.07
          AND l_quantity < 24""",
    11: """EXPLAIN FORMAT=JSON SELECT ps_partkey, SUM(ps_supplycost*ps_availqty) AS value
           FROM partsupp JOIN supplier ON ps_suppkey=s_suppkey
           JOIN nation ON s_nationkey=n_nationkey WHERE n_name='GERMANY'
           GROUP BY ps_partkey HAVING SUM(ps_supplycost*ps_availqty) >
           (SELECT SUM(ps_supplycost*ps_availqty)*0.00001 FROM partsupp
            JOIN supplier ON ps_suppkey=s_suppkey JOIN nation ON s_nationkey=n_nationkey
            WHERE n_name='GERMANY') ORDER BY value DESC""",
}

_HYBENCH_AP_EXPLAIN = """EXPLAIN FORMAT=JSON
SELECT cid,total_expense,SUM(ch.amount) AS checking_amount
FROM (SELECT co.companyid AS cid,SUM(tr.amount) AS total_expense
      FROM company co JOIN transfer tr ON co.companyid=tr.sourceid
      WHERE co.name LIKE 'software%' GROUP BY co.companyid
      ORDER BY total_expense DESC LIMIT 10) big_company
JOIN checking ch ON big_company.cid=ch.sourceid
GROUP BY cid,total_expense ORDER BY total_expense DESC,checking_amount DESC"""

_HYBENCH_TP_EXPLAIN = "EXPLAIN FORMAT=JSON SELECT * FROM customer WHERE custid=1"

_NATIVE_XP_RUNTIME_JAR = Path(
    "/home/anonymous/program/resource_adaptive_mysql/artifacts/"
    "mysql_xp_native_iq1_iq7_successor_v2/"
    "e3e3a32e6b095e10d5e229be5ec211131f626374bc82764d98ad3f910344e017/"
    "runtime/HyBench-native-IQ1-IQ7-v2.jar"
)
_THREE_WORKLOAD_FREEZE = Path(
    "/home/anonymous/program/resource_adaptive_mysql/config/formal/"
    "frozen_mysql_three_workloads_20260806_v1/workload_freeze.json"
)

# Medians (seconds) extracted from this workspace's completed formal events.
# They are scheduling hints, not acceptance evidence.  Unknown JOB files keep
# their deterministic filename order ahead of the known heavy tail.
JOB_DURATION_ESTIMATES_S = {
    "08_job_33a.sql": 0.64,
    "07_job_26a.sql": 4.45,
    "09_job_sensitive_title_cast_role5_movie100k.sql": 4.84,
    "01_job_1a.sql": 5.89,
    "04_job_12a.sql": 5.96,
    "02_job_5a.sql": 6.14,
    "05_job_16a.sql": 14.93,
    "03_job_9a.sql": 51.89,
    "06_job_20a.sql": 164.50,
    # Canonical Join Order Benchmark names for the same frozen statements.
    "33a.sql": 0.64,
    "26a.sql": 4.45,
    "1a.sql": 5.89,
    "12a.sql": 5.96,
    "5a.sql": 6.14,
    "16a.sql": 14.93,
    "9a.sql": 51.89,
    "20a.sql": 164.50,
}
TPCH_DURATION_ESTIMATES_S = {
    1: 173.57, 2: 4.57, 3: 292.09, 4: 94.82, 5: 90.10, 6: 60.14,
    7: 153.00, 8: 87.01, 9: 572.90, 10: 141.50, 11: 44.52,
    12: 80.92, 13: 84.35, 14: 81.15, 15: 338.16, 16: 31.35,
    17: 16.41, 18: 79.41, 19: 23.65, 20: 83.55, 21: 217.71, 22: 5.00,
}
HEAVY_QUERY_THRESHOLD_S = 30.0


def _job_estimates_from_manifest(job_manifest: Path) -> dict[str, float]:
    """Return runtime medians where known, otherwise a join-complexity proxy."""
    rows = json.loads(job_manifest.read_text(encoding="utf-8")).get("queries", [])
    estimates: dict[str, float] = {}
    for row in rows:
        path = Path(str(row["path"]))
        if path.name in JOB_DURATION_ESTIMATES_S:
            estimates[path.name] = JOB_DURATION_ESTIMATES_S[path.name]
            continue
        sql = path.read_text(encoding="utf-8")
        match = re.search(r"(?is)\bFROM\b(.*?)\bWHERE\b", sql)
        relation_count = 1
        if match:
            relation_count = max(1, len([part for part in match.group(1).split(",") if part.strip()]))
        # This is used only for ordering/admission.  Squaring makes high-join
        # queries conservative enough to enter the single-heavy-query lane.
        estimates[path.name] = float(relation_count * relation_count)
    return estimates


def discover_job_query_files(query_dir: Path) -> list[Path]:
    """Select the 113 JOB workload files and exclude schema/helper SQL."""
    return sorted(
        (path for path in query_dir.glob("*.sql")
         if re.fullmatch(r"\d+[a-z]\.sql", path.name)),
        key=lambda path: (int(re.match(r"\d+", path.name).group()), path.name),
    )


def _balanced_tpch_lanes(query_ids: list[int], concurrency: int) -> list[list[int]]:
    lane_count = min(max(1, concurrency), len(query_ids))
    lanes: list[list[int]] = [[] for _ in range(lane_count)]
    loads = [0.0] * lane_count
    for query_id in sorted(query_ids, key=TPCH_DURATION_ESTIMATES_S.get, reverse=True):
        lane = min(range(lane_count), key=lambda index: (loads[index], index))
        lanes[lane].append(query_id)
        loads[lane] += TPCH_DURATION_ESTIMATES_S[query_id]
    return [sorted(lane, key=TPCH_DURATION_ESTIMATES_S.get) for lane in lanes]


def run_job_tpch_parallel(
    *, job_manifest: Path, job_endpoint: MySQLEndpoint,
    tpch_endpoint: MySQLEndpoint, output_dir: Path, benchbase_jar: Path,
    clients: int, tpch_clients: int, scale_factor: float, timeout_s: float,
    target_wall_time_s: float = 900.0, job_heavy_clients: int = 4,
    formal_scope: str, workload_freeze: Path,
    capture_job_explain: bool = False,
    job_runner: Callable[..., dict[str, Any]] = run_job,
    tpch_runner: Callable[..., dict[str, Any]] = run_benchbase,
) -> dict[str, Any]:
    """Run the two finite AP suites concurrently and require full coverage."""
    if formal_scope not in {"mysql_only", "paired_cross_engine"}:
        raise ValueError("formal_scope must be mysql_only or paired_cross_engine")
    if (clients, tpch_clients, job_heavy_clients) != (16, 5, 4):
        raise ValueError("frozen JOB/TPC-H scheduling requires 16/5/4 workers")
    if target_wall_time_s != 900.0:
        raise ValueError("frozen JOB/TPC-H maximum wall time is exactly 900 seconds")
    freeze_path = Path(workload_freeze).resolve()
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    job_schedule = {
        "dedicated_heavy_pool": False,
        "heavy_start_stagger_s": 0.0,
        "heavy_query_order": "shortest_first",
    }
    workload_freeze_binding = {
        "path": str(freeze_path),
        "sha256": hashlib.sha256(freeze_path.read_bytes()).hexdigest(),
        "schema_version": freeze.get("schema_version"),
    }
    job_schedule_schema = freeze.get("schema_version")
    if job_schedule_schema in {
        "mysql_four_scene_job_staggered_schedule_successor_v2",
        "mysql_four_scene_job_staggered_schedule_successor_v3",
    }:
        predecessor = freeze.get("predecessor_workload_freeze") or {}
        predecessor_path = ROOT / str(predecessor.get("path") or "")
        job = freeze.get("job") or {}
        if (
            freeze.get("status") != "FROZEN"
            or freeze.get("formal_scope") != formal_scope
            or freeze.get("active_benchmarks") != ["job", "tpch", "hybench_xp"]
            or not predecessor_path.is_file()
            or hashlib.sha256(predecessor_path.read_bytes()).hexdigest()
            != predecessor.get("sha256")
            or job.get("required_count") != 113
            or job.get("executions_per_measurement") != 1
            or job.get("workers") != 16
            or job.get("long_query_threshold_seconds") != 30
            or job.get("maximum_concurrent_long_queries") != 4
            or job.get("long_query_workers") != 4
            or job.get("short_query_workers") != 12
            or job.get("long_query_order") != "longest_estimated_first"
            or job.get("short_query_order") != "shortest_estimated_first"
            or job.get("minimum_long_query_start_stagger_seconds")
            not in {1, 30}
            or job.get("schedule") not in {
                "dedicated_long_query_pool_with_start_stagger",
                "dedicated_long_query_pool_with_bounded_start_stagger",
            }
            or freeze.get("fresh_r0_required") is not True
            or freeze.get("predecessor_results_prohibited_as_successor_r0") is not True
        ):
            raise ValueError("JOB staggered schedule successor contract differs")
        workload_freeze_binding["job_schedule_predecessor"] = {
            "path": str(predecessor_path.resolve()),
            "sha256": predecessor["sha256"],
        }
        job_schedule = {
            "dedicated_heavy_pool": True,
            "heavy_start_stagger_s": float(
                job["minimum_long_query_start_stagger_seconds"]
            ),
            "heavy_query_order": "longest_first",
        }
        freeze = json.loads(predecessor_path.read_text(encoding="utf-8"))
        # v3 is an immutable successor of v2.  Validate the sealed v2 link,
        # but retain v3's bounded stagger instead of re-applying v2's
        # impossible 30-second gap to every admitted long query.
        if job_schedule_schema == "mysql_four_scene_job_staggered_schedule_successor_v3":
            v2_predecessor = freeze.get("predecessor_workload_freeze") or {}
            v2_predecessor_path = ROOT / str(v2_predecessor.get("path") or "")
            if (
                freeze.get("schema_version")
                != "mysql_four_scene_job_staggered_schedule_successor_v2"
                or not v2_predecessor_path.is_file()
                or hashlib.sha256(v2_predecessor_path.read_bytes()).hexdigest()
                != v2_predecessor.get("sha256")
            ):
                raise ValueError("JOB v3 predecessor chain differs")
            workload_freeze_binding["job_schedule_v2_predecessor"] = {
                "path": str(v2_predecessor_path.resolve()),
                "sha256": v2_predecessor["sha256"],
            }
            freeze = json.loads(v2_predecessor_path.read_text(encoding="utf-8"))
    if freeze.get("schema_version") == "mysql_three_workload_scale_successor_v1":
        predecessor = freeze.get("predecessor_workload_freeze") or {}
        predecessor_path = ROOT / str(predecessor.get("path") or "")
        if (
            not predecessor_path.is_file()
            or hashlib.sha256(predecessor_path.read_bytes()).hexdigest()
            != predecessor.get("sha256")
        ):
            raise ValueError("workload scale successor predecessor bytes differ")
        requested_scale = float((freeze.get("tpch") or {}).get("scale_factor"))
        if requested_scale != float(scale_factor):
            raise ValueError("TPC-H scale differs from frozen scale successor")
        if freeze.get("predecessor_tpch_sf10_results_prohibited") is not True:
            raise ValueError("TPC-H scale successor does not prohibit SF10 result reuse")
        predecessor_freeze = json.loads(predecessor_path.read_text(encoding="utf-8"))
        workload_freeze_binding["predecessor"] = {
            "path": str(predecessor_path.resolve()),
            "sha256": predecessor["sha256"],
        }
        workload_freeze_binding["tpch_scale_factor"] = requested_scale
        freeze = predecessor_freeze
    elif (freeze.get("tpch") or {}).get("scale_factor") is not None:
        if float(freeze["tpch"]["scale_factor"]) != float(scale_factor):
            raise ValueError("TPC-H scale differs from frozen workload")
    if (
        freeze.get("formal_scope") != formal_scope
        or freeze.get("active_benchmarks") != ["job", "tpch", "hybench_xp"]
        or freeze.get("job", {}).get("required_count") != 113
        or freeze.get("tpch", {}).get("lanes") != _balanced_tpch_lanes(list(range(1, 23)), 5)
    ):
        raise ValueError("workload freeze does not match the active full JOB/TPC-H contract")
    rows = json.loads(Path(job_manifest).read_text(encoding="utf-8")).get("queries", [])
    expected_job = {row["name"]: row["sha256"] for row in freeze["job"]["queries"]}
    actual_job = {}
    for row in rows:
        path = Path(str(row["path"])).resolve()
        if path.name in actual_job:
            raise ValueError(f"duplicate JOB query in manifest: {path.name}")
        actual_job[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_job != expected_job:
        raise ValueError("JOB manifest is not the exact frozen 113-query byte set")
    output_dir.mkdir(parents=True, exist_ok=True)
    if job_manifest.is_file():
        job_estimates = _job_estimates_from_manifest(job_manifest)
    else:
        # Supports injected offline runners; a real launch always has a sealed
        # manifest and takes the branch above.
        job_estimates = {
            name: duration for name, duration in JOB_DURATION_ESTIMATES_S.items()
            if re.fullmatch(r"\d{2}_job_.*\.sql", name)
        }
    started_epoch_ms = int(time.time() * 1000)
    started = time.monotonic()
    tpch_lanes = _balanced_tpch_lanes(list(range(1, 23)), tpch_clients)
    # Each lane is load-balanced by historical runtime and ordered short-first.
    # The five heaviest queries therefore do not all start at the same instant.
    with ThreadPoolExecutor(max_workers=1 + len(tpch_lanes), thread_name_prefix="job-tpch") as pool:
        job_future = pool.submit(
            job_runner, job_manifest, job_endpoint, output_dir / "job",
            concurrency=clients, repetitions=1, dry_run=False, timeout_s=timeout_s,
            estimated_duration_s=job_estimates,
            heavy_query_threshold_s=HEAVY_QUERY_THRESHOLD_S,
            max_concurrent_heavy_queries=job_heavy_clients,
            **job_schedule,
            capture_explain=capture_job_explain,
        )
        tpch_futures = [
            pool.submit(
                tpch_runner, "tpch", tpch_endpoint,
                output_dir / f"tpch_lane_{lane_index}",
                jar_path=benchbase_jar, terminals=1, scale_factor=scale_factor,
                dry_run=False, timeout_s=timeout_s, complete_query_set=True,
                query_order=lane,
            )
            for lane_index, lane in enumerate(tpch_lanes, 1)
        ]
        job_result = job_future.result()
        tpch_lane_results = [future.result() for future in tpch_futures]
    lane_coverages = [result.get("query_coverage", {}) for result in tpch_lane_results]
    tpch_completed: set[str] = set()
    for lane, coverage in zip(tpch_lanes, lane_coverages):
        required_lane = {f"Q{query_id}" for query_id in lane}
        completed_lane = set(coverage.get("completed_queries", []))
        if (
            coverage.get("coverage_complete") is not True
            or coverage.get("missing_queries")
            or coverage.get("unexpected_transaction_names")
            or completed_lane != required_lane
        ):
            raise RuntimeError(f"TPC-H lane coverage differs from frozen assignment {lane}")
        tpch_completed.update(completed_lane)
    tpch_required = {f"Q{query_id}" for query_id in range(1, 23)}
    tpch_missing = sorted(tpch_required - tpch_completed, key=lambda value: int(value[1:]))
    tpch_result = {
        "lanes": tpch_lane_results,
        # The five lanes execute concurrently, so their throughput is
        # additive.  Keep latency conservative because the adapter does not
        # currently expose every per-query sample at this aggregation layer.
        # Formal hard gates require a positive client-side QPS audit; omitting
        # aggregate metrics made every complete TPC-H run fail that gate.
        "metrics": {
            "qps": sum(
                float((result.get("metrics") or {}).get("qps", 0.0))
                for result in tpch_lane_results
            ),
            "latency_p95_ms": max(
                float((result.get("metrics") or {}).get("latency_p95_ms", 0.0))
                for result in tpch_lane_results
            ),
            "latency_p99_ms": max(
                float((result.get("metrics") or {}).get("latency_p99_ms", 0.0))
                for result in tpch_lane_results
            ),
            "explain_samples": [],
        },
        "query_coverage": {
            "required_queries": sorted(tpch_required, key=lambda value: int(value[1:])),
            "completed_queries": sorted(tpch_completed, key=lambda value: int(value[1:])),
            "missing_queries": tpch_missing,
            "coverage_complete": not tpch_missing,
        },
    }
    expected_job_names = set(expected_job)
    job_coverage = job_result.get("query_coverage", {})
    if (
        job_coverage.get("coverage_complete") is not True
        or job_coverage.get("missing_queries")
        or set(job_coverage.get("completed_queries", [])) != expected_job_names
    ):
        raise RuntimeError("JOB coverage differs from frozen 113-query set")
    for name, result in (("job", job_result), ("tpch", tpch_result)):
        coverage = result.get("query_coverage", {})
        if coverage.get("coverage_complete") is not True or coverage.get("missing_queries"):
            raise RuntimeError(f"{name} AP SQL coverage is incomplete")
    wall_time_s = time.monotonic() - started
    return {
        "benchmark": "job_tpch",
        "execution": "parallel",
        "started_epoch_ms": started_epoch_ms,
        "wall_time_s": wall_time_s,
        "target_wall_time_s": target_wall_time_s,
        "target_wall_time_met": wall_time_s <= target_wall_time_s,
        "formal_measurement_valid": wall_time_s <= target_wall_time_s,
        "failure_reasons": ([] if wall_time_s <= target_wall_time_s else ["wall_time_exceeded_900_seconds"]),
        "formal_scope": formal_scope,
        "formal_launch_authorized": False,
        "launch_class": "benchmark_workload_only",
        "cross_engine_comparison_prohibited": formal_scope == "mysql_only",
        "all_ap_sql_completed": True,
        "scheduling": {
            "policy": (
                "dedicated_longest_first_heavy_pool_bounded_start_stagger"
                if job_schedule["dedicated_heavy_pool"]
                else "shortest_estimated_first_heavy_tail_staggered"
            ),
            "heavy_query_threshold_s": HEAVY_QUERY_THRESHOLD_S,
            "job_query_count": len(job_estimates),
            "job_clients": clients,
            "job_maximum_concurrent_heavy_queries": job_heavy_clients,
            "tpch_configured_clients": tpch_clients,
            "tpch_effective_concurrency": len(tpch_lanes),
            "tpch_balanced_short_first_lanes": tpch_lanes,
            "tpch_historical_median_theoretical_makespan_s": max(
                sum(TPCH_DURATION_ESTIMATES_S[q] for q in lane) for lane in tpch_lanes
            ),
            "tpch_historical_median_total_s": sum(TPCH_DURATION_ESTIMATES_S.values()),
            "estimate_source": "TPC-H completed-event medians; JOB unknown queries use join-complexity admission proxy",
            "workload_freeze": str(freeze_path),
            "workload_freeze_sha256": hashlib.sha256(freeze_path.read_bytes()).hexdigest(),
            "workload_freeze_binding": workload_freeze_binding,
        },
        "workloads": {"job": job_result, "tpch": tpch_result},
    }


def _capture_explain(args: argparse.Namespace) -> list[str]:
    """Capture a read-only plan without modifying benchmark or shared files."""
    if args.benchmark == "tpcc":
        sql = _TPCC_EXPLAIN
    elif args.benchmark == "tpch":
        sql = _TPCH_EXPLAINS.get(args.tpch_query_id)
    elif args.benchmark == "hybench_tp":
        sql = _HYBENCH_TP_EXPLAIN
    elif args.benchmark in {"hybench_ap", "hybench_xp"}:
        sql = _HYBENCH_AP_EXPLAIN
    else:
        sql = None
    if not sql:
        return []
    env = dict(os.environ)
    env["MYSQL_PWD"] = args.password
    completed = subprocess.run(
        ["mysql", "--batch", "--raw", "--skip-column-names", "-h", args.host,
         "-P", str(args.port), "-u", args.user, args.database, "-e", sql],
        text=True, capture_output=True, timeout=60, check=False, env=env,
    )
    value = completed.stdout.strip()
    return [value] if completed.returncode == 0 and value else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=("tpcc", "tpch", "job", "job_tpch", "hybench_ap", "hybench_tp", "hybench_xp"), required=True)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--database")
    parser.add_argument("--job-database")
    parser.add_argument("--tpch-database")
    parser.add_argument("--job-host")
    parser.add_argument("--job-port", type=int)
    parser.add_argument("--tpch-host")
    parser.add_argument("--tpch-port", type=int)
    parser.add_argument("--formal-scope", choices=("mysql_only", "paired_cross_engine"))
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", default="")
    parser.add_argument(
        "--password-file",
        type=Path,
        help="private one-line password file; keeps credentials out of argv",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-sec", type=int, default=60)
    parser.add_argument("--clients", type=int, default=4)
    parser.add_argument("--job-clients", type=int, default=16)
    parser.add_argument("--job-heavy-clients", type=int, default=4)
    parser.add_argument("--tpch-clients", type=int, default=5)
    parser.add_argument("--target-wall-time-sec", type=float, default=900.0)
    parser.add_argument("--scale-factor", type=float, default=1.0)
    parser.add_argument("--tpch-query-id", type=int, choices=range(1, 23))
    parser.add_argument("--job-query-dir", type=Path,
                        default=Path("/home/anonymous/dataset/pg_data/join-order-benchmark"))
    parser.add_argument("--job-query-files", default="",
                        help="optional comma-separated deterministic JOB SQL filenames")
    parser.add_argument("--benchbase-jar", type=Path, default=Path("/home/anonymous/benchbase-mysql/benchbase.jar"))
    parser.add_argument("--hybench-home", type=Path, default=Path("/home/anonymous/HyBench-2024-master"))
    parser.add_argument("--hybench-runtime-jar", type=Path, default=_NATIVE_XP_RUNTIME_JAR)
    parser.add_argument("--workload-freeze", type=Path, default=_THREE_WORKLOAD_FREEZE)
    parser.add_argument("--hybench-scale", choices=("1x", "10x", "100x"), default="10x")
    args = parser.parse_args()
    if args.password_file is not None:
        if args.password:
            parser.error("choose either --password CHANGE_ME --password-file")
        if (
            not args.password_file.is_file()
            or args.password_file.is_symlink()
            or args.password_file.stat().st_mode & 0o077
        ):
            parser.error("--password-file must be a private regular file")
        args.password = args.password_file.read_text(encoding="utf-8").rstrip("\r\n")
        if not args.password:
            parser.error("--password-file is empty")
    if args.benchmark == "job_tpch":
        if not args.job_database or not args.tpch_database or not args.formal_scope:
            parser.error("job_tpch requires --job-database, --tpch-database, and --formal-scope")
        if not (args.job_host or args.host) or not (args.job_port or args.port):
            parser.error("job_tpch requires a JOB host/port (specific or common)")
        if not (args.tpch_host or args.host) or not (args.tpch_port or args.port):
            parser.error("job_tpch requires a TPC-H host/port (specific or common)")
        if args.job_query_files or args.tpch_query_id:
            parser.error("job_tpch always runs the complete JOB and TPC-H query sets")
    elif not args.database or not args.host or not args.port:
        parser.error("--host, --port, and --database are required unless --benchmark=job_tpch")
    if args.benchmark == "job_tpch":
        query_files = discover_job_query_files(args.job_query_dir)
        if not query_files:
            parser.error("job_tpch selected no JOB workload SQL")
        manifest = build_job_manifest(query_files, args.output_dir / "manifest")
        result = run_job_tpch_parallel(
            job_manifest=manifest,
            job_endpoint=MySQLEndpoint(
                args.job_host or args.host, args.job_port or args.port,
                args.job_database, args.user, args.password,
            ),
            tpch_endpoint=MySQLEndpoint(
                args.tpch_host or args.host, args.tpch_port or args.port,
                args.tpch_database, args.user, args.password,
            ),
            output_dir=args.output_dir, benchbase_jar=args.benchbase_jar,
            clients=args.job_clients, tpch_clients=args.tpch_clients,
            job_heavy_clients=args.job_heavy_clients,
            target_wall_time_s=args.target_wall_time_sec,
            scale_factor=args.scale_factor,
            timeout_s=max(300.0, args.target_wall_time_sec), formal_scope=args.formal_scope,
            workload_freeze=args.workload_freeze,
        )
        print(json.dumps(result, separators=(",", ":")), flush=True)
        return 0 if result["target_wall_time_met"] else 2
    endpoint = MySQLEndpoint(args.host, args.port, args.database, args.user, args.password)
    if args.benchmark in {"tpcc", "tpch"}:
        weights = None
        if args.benchmark == "tpch" and args.tpch_query_id:
            weights = [0.0] * 22
            weights[args.tpch_query_id - 1] = 100.0
        result = run_benchbase(
            args.benchmark, endpoint, args.output_dir, jar_path=args.benchbase_jar,
            duration_s=args.duration_sec, terminals=args.clients,
            scale_factor=args.scale_factor, transaction_weights=weights, dry_run=False,
        )
    elif args.benchmark == "job":
        selected_names = [name.strip() for name in args.job_query_files.split(",") if name.strip()]
        if selected_names:
            if len(selected_names) != len(set(selected_names)) or any(
                    not re.fullmatch(r"\d+[a-z]\.sql", name) for name in selected_names):
                raise SystemExit("invalid or duplicate --job-query-files entry")
            query_files = [args.job_query_dir / name for name in selected_names]
            missing = [str(path) for path in query_files if not path.is_file()]
            if missing:
                raise SystemExit(f"missing JOB query files: {missing}")
        else:
            query_files = sorted(
                path for path in args.job_query_dir.glob("*.sql")
                if re.fullmatch(r"\d+[a-z]\.sql", path.name)
            )
        manifest = build_job_manifest(query_files, args.output_dir / "manifest")
        result = run_job(
            manifest, endpoint, args.output_dir, concurrency=args.clients,
            repetitions=max(1, (args.duration_sec + 59) // 60), dry_run=False,
        )
    else:
        result = run_hybench(
            args.benchmark.removeprefix("hybench_"), endpoint, args.output_dir,
            hybench_home=args.hybench_home, duration_s=args.duration_sec,
            clients=args.clients, extra_properties={"sf": args.hybench_scale}, dry_run=False,
            runtime_jar=args.hybench_runtime_jar if args.benchmark == "hybench_xp" else None,
            timeout_s=max(900, args.duration_sec * 20),
        )
    metrics = dict(result["metrics"])
    if args.benchmark in {"tpcc", "tpch", "hybench_ap", "hybench_tp", "hybench_xp"}:
        metrics["explain_samples"] = _capture_explain(args)
    print(json.dumps(metrics, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
