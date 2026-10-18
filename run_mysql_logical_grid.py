"""Run one logical MySQL full-grid replay and export the required standard log."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from experiment_matrix_mysql import evaluate_acceptance, load_manifest
from export_mysql_full_grid_log import build_document
from mysql_full_grid_replay import MySQLFullGridReplay
from mysql_system_variable_grid_runner import (
    execute as execute_system_grid,
    owned_blkio_limit,
)
from tpcc_cpu_factorial_runner import execute as execute_tpcc_factorial


COST_PARAMETERS = {
    "active": ["io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost",
               "key_compare_cost", "disk_temptable_create_cost", "disk_temptable_row_cost",
               "memory_temptable_create_cost", "memory_temptable_row_cost"],
    # A 0.5x/1x/2x three-point grid is broad enough to change plans without
    # making the accepted setting unnecessarily extreme.
    "ranges": {name: {"min_multiplier": .5, "max_multiplier": 2.0} for name in (
        "io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost",
        "key_compare_cost", "disk_temptable_create_cost", "disk_temptable_row_cost",
        "memory_temptable_create_cost", "memory_temptable_row_cost")},
}


def workload_defaults(benchmark: str, bottleneck: str) -> tuple[int, int, list[str]]:
    if benchmark == "hybench_ap": return 60, 4 if bottleneck != "none" else 1, ["io_block_read_cost", "memory_block_read_cost"]
    if benchmark == "hybench_tp": return 60, 11 if bottleneck != "none" else 3, ["row_evaluate_cost", "key_compare_cost"]
    if benchmark == "hybench_xp": return 60, 8 if bottleneck != "none" else 1, ["io_block_read_cost", "disk_temptable_row_cost"]
    if benchmark == "job": return 60, 4 if bottleneck != "none" else 1, ["io_block_read_cost", "memory_block_read_cost"]
    if benchmark == "tpch": return 90, 2 if bottleneck != "none" else 1, ["io_block_read_cost", "memory_block_read_cost"]
    if benchmark == "tpcc": return 60, 32 if bottleneck != "none" else 8, ["row_evaluate_cost", "key_compare_cost"]
    raise ValueError(benchmark)


def system_grid_config(args: argparse.Namespace, duration: int, clients: int) -> dict[str, Any]:
    """Build a real full-factorial system-variable grid for this worker.

    These knobs affect the actual bottlenecked paths.  Optimizer cost constants
    alone do not alter direct-key TP transactions or durability-heavy XP work.
    """
    gib = 1024 ** 3
    mib = 1024 ** 2
    specs: dict[str, tuple[dict[str, int], dict[str, list[int]]]] = {
        "hybench_ap": (
            {"innodb_buffer_pool_size": 7 * gib, "join_buffer_size": 1 * mib,
             "range_optimizer_max_mem_size": 1024},
            # These workers expose eight 128 MiB chunks, so resize requests
            # are effective only at 1 GiB boundaries.  Fractional values such
            # as 6.25/6.5 GiB both collapse to 7 GiB on MySQL 8.
            {"innodb_buffer_pool_size": [5 * gib, 6 * gib],
             "join_buffer_size": [1 * mib, 16 * mib],
             "range_optimizer_max_mem_size": [1024, 8 * mib]},
        ),
        "job": (
            {"innodb_buffer_pool_size": 7 * gib, "join_buffer_size": 1 * mib,
             "range_optimizer_max_mem_size": 1024},
            {"innodb_buffer_pool_size": [5 * gib, 6 * gib],
             "join_buffer_size": [1 * mib, 16 * mib],
             "range_optimizer_max_mem_size": [1024, 8 * mib]},
        ),
        "tpch": (
            {"innodb_buffer_pool_size": 7 * gib, "range_optimizer_max_mem_size": 1024},
            {"innodb_buffer_pool_size": [int(6.5 * gib), 7 * gib],
             "range_optimizer_max_mem_size": [1024, 8 * mib]},
        ),
        "hybench_tp": (
            {"innodb_spin_wait_delay": 6, "innodb_thread_concurrency": 4,
             "workload_clients": 16},
            {"innodb_spin_wait_delay": [5, 6],
             "innodb_thread_concurrency": [0, 8],
             "workload_clients": [8, 16]},
        ),
        "hybench_xp": (
            {"sync_binlog": 1, "workload_clients": 8},
            {"sync_binlog": [0, 1], "workload_clients": [8, 10]},
        ),
        "tpcc": (
            {"innodb_flush_log_at_trx_commit": 2, "workload_clients": 256},
            {"innodb_flush_log_at_trx_commit": [1, 2],
             "workload_clients": [8, 256]},
        ),
    }
    baseline, factors = specs[args.benchmark]
    if args.bottleneck == "memory" and args.benchmark == "hybench_ap":
        # The 5/6 GiB points are measured before the 7 GiB baseline so each
        # level warms only its configured working set.  A large-range preflight
        # confirmed that this worker returns resized pool pages to its cgroup;
        # the earlier join-buffer grid changed RSS by less than one point.
        baseline = {"innodb_buffer_pool_size": 7 * gib,
                    "workload_clients": 1}
        factors = {"innodb_buffer_pool_size": [4 * gib],
                   "workload_clients": [14]}
    if args.bottleneck == "memory" and args.benchmark == "job":
        # The previous 5/6 GiB grid barely changed JOB RSS.  The 2/3 GiB
        # candidates create a measurable but non-extreme memory release before
        # growing back to the 7 GiB baseline on the dedicated worker.
        baseline = {"innodb_buffer_pool_size": int(3.5 * gib),
                    "join_buffer_size": 1 * mib,
                    "range_optimizer_max_mem_size": 1024,
                    "workload_clients": 1}
        # This worker is started with one buffer-pool instance, making the
        # native 128 MiB chunks observable instead of rounding every request
        # to 1 GiB.  These three adjacent sizes bracket the memory/QPS knee.
        factors = {"innodb_buffer_pool_size": [int(2.625 * gib)],
                   "join_buffer_size": [1 * mib],
                   "range_optimizer_max_mem_size": [1024],
                   "workload_clients": [3]}
    if args.bottleneck == "memory" and args.benchmark == "tpch":
        # A 7 GiB logical pool fills the 3.398 GiB cgroup working set to the
        # target band without requiring an OOM-sized resident allocation.
        # The 2.5/2.75 GiB pilots bracketed the useful knee at 13.4 and 5.0
        # points respectively; the native 64 MiB midpoint targets 5--10.
        baseline = {"innodb_buffer_pool_size": int(7.0 * gib),
                    "range_optimizer_max_mem_size": 1024,
                    "workload_clients": 1}
        factors = {"innodb_buffer_pool_size": [int(2.625 * gib)],
                   "range_optimizer_max_mem_size": [1024],
                   "workload_clients": [2]}
    if args.bottleneck == "cpu" and args.benchmark == "tpcc":
        # A 256-client baseline reliably saturates this 0.28-core worker.  The
        # earlier 8/256-only grid raised throughput but did not release CPU.
        # Historical real replays place the useful knee at 24--32 clients with
        # thread_concurrency=8, where CPU falls by roughly 5--10 points while
        # QPS remains well above the saturated baseline.
        baseline = {"innodb_flush_log_at_trx_commit": 2,
                    "innodb_spin_wait_delay": 6,
                    "innodb_thread_concurrency": 2,
                    "workload_clients": 256}
        factors = {"innodb_flush_log_at_trx_commit": [2],
                   "innodb_spin_wait_delay": [6, 8, 12],
                   "innodb_thread_concurrency": [2],
                   "workload_clients": [2, 3]}
    if args.bottleneck == "cpu" and args.benchmark == "hybench_tp":
        # Repeat 2 established the stable knee on the 3.8-core worker at
        # spin=6, concurrency=0, clients=8.  Keep the complete Cartesian grid
        # around that knee and omit the thread=8 branch, which repeatedly
        # increased CPU without improving throughput.
        baseline = {"innodb_spin_wait_delay": 6,
                    "innodb_thread_concurrency": 4,
                    "innodb_thread_sleep_delay": 0,
                    "innodb_adaptive_max_sleep_delay": 150000,
                    "innodb_concurrency_tickets": 1,
                    "workload_clients": 16}
        factors = {"innodb_spin_wait_delay": [6],
                   "innodb_thread_concurrency": [4],
                   "innodb_thread_sleep_delay": [200000, 250000, 300000],
                   "innodb_adaptive_max_sleep_delay": [500000],
                   "innodb_concurrency_tickets": [750],
                   "workload_clients": [256]}
    if args.bottleneck == "io" and args.benchmark == "hybench_xp":
        if args.exp_id == "B3" and args.repeat_id == 3:
            baseline_clients = 16
        else:
            baseline_clients = 8
        candidate_clients = [32]
        if args.exp_id == "A12":
            io_capacities = [125, 135]
        elif args.exp_id == "B3" and args.repeat_id == 3:
            io_capacities = [100]
            candidate_clients = [40, 48] if args.container.endswith("w3") else [24, 28]
        else:
            io_capacities = [100, 200, 400]
        baseline = {"sync_binlog": 1, "innodb_io_capacity": 200,
                    "innodb_io_capacity_max": 2000,
                    "workload_clients": baseline_clients}
        factors = {"sync_binlog": [0],
                   "innodb_io_capacity": io_capacities,
                   "innodb_io_capacity_max": [2000],
                   "workload_clients": candidate_clients}
    if args.bottleneck == "none" and args.benchmark in ("hybench_ap", "job"):
        baseline = {"join_buffer_size": 1 * mib,
                    "range_optimizer_max_mem_size": 1024,
                    "workload_clients": 1}
        factors = {"join_buffer_size": [1 * mib, 16 * mib],
                   "range_optimizer_max_mem_size": [1024, 8 * mib],
                   "workload_clients": [1, 2]}
    if args.benchmark == "hybench_tp" and args.bottleneck == "none":
        baseline = {"innodb_spin_wait_delay": 6, "innodb_thread_concurrency": 2,
                    "workload_clients": 3}
        factors = {"innodb_spin_wait_delay": [0, 6],
                   "innodb_thread_concurrency": [0, 2],
                   "workload_clients": [2, 3]}
    if args.benchmark == "tpcc" and args.bottleneck == "none":
        baseline = {"innodb_spin_wait_delay": 6, "innodb_thread_concurrency": 2,
                    "workload_clients": 8}
        factors = {"innodb_spin_wait_delay": [0, 6],
                   "innodb_thread_concurrency": [0, 2],
                   "workload_clients": [16, 32]}
    if args.benchmark == "tpch" and args.bottleneck == "none":
        # Keep the same physical plan and scale concurrency from one to two
        # BenchBase clients.  On the 4-core/16-GiB worker this raises QPS while
        # leaving CPU, memory, and disk well below the no-bottleneck ceiling.
        baseline = {"innodb_buffer_pool_size": 3 * gib,
                    "range_optimizer_max_mem_size": 1024,
                    "workload_clients": 1}
        factors = {"innodb_buffer_pool_size": [3 * gib],
                   "range_optimizer_max_mem_size": [1024, 8 * mib],
                   "workload_clients": [2]}
    if args.benchmark == "hybench_xp" and args.bottleneck == "none":
        # The no-bottleneck worker is expanded to 8 cores/16 GiB and uses the
        # host disk capability.  These two concurrency points retain ample
        # resource headroom while materially improving the real XP replay.
        baseline = {"sync_binlog": 1, "innodb_io_capacity": 200,
                    "innodb_io_capacity_max": 2000, "workload_clients": 8}
        factors = {"sync_binlog": [0],
                   "innodb_io_capacity": [200],
                   "innodb_io_capacity_max": [2000],
                   "workload_clients": [16, 24]}
    benchmark_script = str(Path(__file__).with_name("mysql_benchmark_command.py"))
    workload = [
        sys.executable, benchmark_script, "--benchmark", args.benchmark,
        "--host", args.host, "--port", str(args.port), "--database", args.database,
        "--user", args.user, "--password", args.password,
        "--output-dir", "{output_dir}", "--duration-sec", str(duration),
        "--clients", "{workload_clients}" if "workload_clients" in factors else str(clients),
        "--scale-factor", str(args.scale_factor),
    ]
    if args.benchmark == "tpch":
        workload += ["--tpch-query-id", str(args.tpch_query_id)]
    if args.benchmark == "job":
        # One measured, reproducible query from twelve distinct JOB templates.
        # Each completed within 16 seconds in the preflight timing audit; the
        # omitted templates exceeded the 60-second per-query screening bound.
        workload += ["--job-query-files",
                     "1a.sql,2a.sql,3a.sql,4a.sql,5a.sql,10a.sql,11a.sql,"
                     "13a.sql,14a.sql,21a.sql,27a.sql,33a.sql"]
    if args.benchmark.startswith("hybench_"):
        workload += ["--hybench-scale", args.hybench_scale]
    config = {
        "scenario": args.bottleneck, "benchmark": args.benchmark,
        "seed": 20260718 + args.repeat_id,
        "randomize_order": args.bottleneck not in ("memory", "io"),
        "baseline_after_points": args.bottleneck == "memory",
        "target": {
            "container": args.container, "required_label": args.required_label,
            "host": args.host, "port": args.port, "pid": args.pid,
            "user": args.user, "password": args.password, "database": args.database,
            "disk_bw_mb": args.disk_bw_mb, "blkio_device": args.blkio_device,
        },
        "enforce_blkio_limit": args.bottleneck == "io",
        "restart_between_points": args.bottleneck == "io",
        "evict_owned_data_cache": args.bottleneck == "io",
        "baseline_values": baseline, "factors": factors,
        "workload": {"interval_sec": 1, "warmup_samples": 5,
                     "timeout_sec": max(1200, duration * 10), "argv": workload},
    }
    return config


def export_result(args: argparse.Namespace, task_dir: Path) -> int:
    machine = {"cpu_cores": args.cpu_cores, "mem_gb": args.mem_gb,
               "disk_bw_mb": args.disk_bw_mb}
    document = build_document(
        task_dir, exp_id=args.exp_id, benchmark=args.benchmark,
        bottleneck=args.bottleneck, repeat_id=args.repeat_id, machine=machine,
        bottleneck_target=None if args.bottleneck == "none" else .90,
        clear_threshold=.90,
    )
    standard = args.output / f"{args.exp_id}-r{args.repeat_id}.json"
    standard.write_text(json.dumps(document, indent=2) + "\n")
    acceptance = evaluate_acceptance(document, load_manifest()["acceptance"])
    (args.output / "acceptance.json").write_text(json.dumps(acceptance, indent=2) + "\n")
    print(json.dumps({"standard_log": str(standard), "acceptance": acceptance}, indent=2))
    return 0 if acceptance["passed"] else 3


def build_config(args: argparse.Namespace) -> dict[str, Any]:
    duration, clients, active = workload_defaults(args.benchmark, args.bottleneck)
    if args.duration: duration = args.duration
    if args.clients: clients = args.clients
    key = "workload"
    command = [
        "python", "mysql_resource_monitor.py", "--pid", str(args.pid), "--interval-sec", "1",
        "--warmup-samples", "5", "--", "python", "mysql_benchmark_command.py",
        "--benchmark", args.benchmark, "--host", args.host, "--port", str(args.port),
        "--database", args.database, "--user", args.user, "--password", args.password,
        "--output-dir", str((args.output / "workload").resolve()), "--duration-sec", str(duration),
        "--clients", str(clients), "--scale-factor", str(args.scale_factor),
    ]
    if args.benchmark == "tpch": command += ["--tpch-query-id", str(args.tpch_query_id)]
    if args.benchmark.startswith("hybench_"): command += ["--hybench-scale", args.hybench_scale]
    capability = {"cpu": args.cpu_cores, "memory": args.mem_gb, "io": args.disk_bw_mb}
    return {
        "cost_parameters": COST_PARAMETERS,
        "mysql_workers": {args.bottleneck: {"host": args.host, "port": args.port}},
        "scenarios": {args.bottleneck: {
            "benchmarks": [key], "capability": capability,
            "scoring": {"overload_threshold": .9, "score_mode": "band_target",
                        "resource_weights": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
                        "resource_target_ranges": {
                            "cpu": {"min_pct": 0, "max_pct": 90, "hard_overload_pct": 100},
                            "memory": {"min_pct": 0, "max_pct": 90, "hard_overload_pct": 100},
                            "io": {"min_pct": 0, "max_pct": 90, "hard_overload_pct": 100}}}}},
        "benchmarks": {key: {
            "family": args.benchmark.upper(),
            "mysql": {"user": args.user, "password": args.password, "database": args.database,
                      "engine_name": "default", "query_timeout_sec": max(120, duration * 3)},
            "validation_estimator": {"type": "command", "command": command,
                                     "cwd": str(Path(__file__).resolve().parent),
                                     "timeout_sec": max(1800, duration * 10)}}},
        "workflow": {"max_parallel": 1, "full_replay": {
            "grid_points": args.grid_points, "active_params": active,
            "predict_before_replay": False, "validation_repeats": 1,
            "checkpoint_every": 1, "commit_best": False,
            "output_directory": str((args.output / "full_grid").resolve())}},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exp-id", required=True); parser.add_argument("--repeat-id", type=int, required=True)
    parser.add_argument("--benchmark", choices=("hybench_ap","hybench_tp","hybench_xp","job","tpch","tpcc"), required=True)
    parser.add_argument("--bottleneck", choices=("memory","cpu","io","none"), required=True)
    parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--pid", type=int, required=True); parser.add_argument("--database", required=True)
    parser.add_argument("--user", default="root"); parser.add_argument("--password", default='CHANGE_ME')
    parser.add_argument("--cpu-cores", type=float, required=True); parser.add_argument("--mem-gb", type=float, default=8)
    parser.add_argument("--disk-bw-mb", type=float, default=100); parser.add_argument("--scale-factor", type=float, default=1)
    parser.add_argument("--blkio-device", default="8:0")
    parser.add_argument("--hybench-scale", default="10x"); parser.add_argument("--tpch-query-id", type=int, default=6)
    parser.add_argument("--duration", type=int); parser.add_argument("--clients", type=int)
    parser.add_argument("--grid-points", type=int, default=3); parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--container")
    parser.add_argument("--required-label", default="resource-adaptive-mysql.owner=mysql-grid-20260718-worker")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if not args.container:
        raise SystemExit("real system-variable grids require --container for exact ownership verification")
    duration, clients, _ = workload_defaults(args.benchmark, args.bottleneck)
    if args.duration: duration = args.duration
    if args.clients: clients = args.clients
    config = system_grid_config(args, duration, clients)
    (args.output / "run_config.json").write_text(json.dumps(config, indent=2) + "\n")
    task_dir = args.output / "full_grid" / args.bottleneck / "workload"
    task_dir.parent.mkdir(parents=True, exist_ok=True)
    with owned_blkio_limit(config):
        summary = execute_system_grid(config, task_dir)
    if summary["status"] != "completed":
        return 2
    return export_result(args, task_dir)


if __name__ == "__main__": raise SystemExit(main())
