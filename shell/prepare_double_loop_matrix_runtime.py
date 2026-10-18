#!/usr/bin/env python3
"""Materialize one isolated VM runtime for the formal double-loop matrix."""

from __future__ import annotations

import argparse
import copy
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]

from run_pg_benchmark_bottleneck_matrix import (  # noqa: E402
    PgConn,
    build_hybench_statement_profile,
    update_benchbase_xml,
    update_hybench_props,
)


REFERENCE_CONFIG = ROOT / "config/binary_plan_tuning_hybenchxp_vm_fullgrid_round1_v4_20260716.json"
HYBENCH_ROOT = Path("/home/anonymous/HyBench-2024-master")
BENCHBASE_ROOT = Path("/home/anonymous/benchbase-postgres")
JOB_SQL_DIR = Path("/home/anonymous/dataset/pg_data/join-order-benchmark")


DB_NAMES = {
    "hybench_ap": "hybench_sf10x",
    "hybench_tp": "hybench_sf10x",
    "hybench_xp": "hybench_sf10x",
    "job": "imdbload",
    "tpch": "benchbase_tpch",
    "tpcc": "benchbase_tpcc",
}


TEMPLATE_DB_NAMES = {
    "hybench_ap": "hybench_sf10x_base",
    "hybench_tp": "hybench_sf10x_base",
    "hybench_xp": "hybench_sf10x_base",
    "job": "template_imdbload",
    "tpch": "template_benchbase_tpch",
    "tpcc": "template_benchbase_tpcc",
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def guest_mem_total_gib(host: str, ssh_port: int = 22) -> float:
    """Read the allocatable guest memory used by the runtime monitor."""
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=5",
        "-p",
        str(ssh_port),
        f"anonymous@{host}",
        "awk '/^MemTotal:/ {print $2}' /proc/meminfo",
    ]
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"failed to read guest MemTotal from {host}: {completed.stdout.strip()}"
        )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip().isdigit()]
    if not lines:
        raise RuntimeError(f"guest MemTotal from {host} is invalid: {completed.stdout!r}")
    mem_total_kib = int(lines[-1])
    if mem_total_kib <= 0:
        raise RuntimeError(f"guest MemTotal from {host} must be positive")
    return mem_total_kib / (1024.0 * 1024.0)


def benchmark_command(args: argparse.Namespace, run_dir: Path, conn: PgConn) -> str:
    benchmark = args.benchmark
    if benchmark.startswith("hybench_"):
        hybench_root = args.hybench_root.resolve()
        props_path = run_dir / f"{benchmark}.props"
        statement_path = run_dir / f"{benchmark}_statements.toml"
        tp_percent_mix = [
            int(item.strip())
            for item in str(args.hybench_tp_percent_mix).split(",")
            if item.strip()
        ]
        if benchmark == "hybench_xp" and any(
            str(value or "").strip()
            for value in (
                args.hybench_ap_query_ids,
                args.hybench_ap_query_weights,
                args.hybench_ap_query_sequence,
            )
        ):
            raise ValueError(
                "native HyBench XP forbids AP query ids, weights, and fixed sequences; "
                "configure only TP/AP client concurrency and the native TP transaction mix"
            )
        if tp_percent_mix and (
            len(tp_percent_mix) != 18 or sum(tp_percent_mix) != 100
        ):
            raise ValueError(
                "--hybench-tp-percent-mix requires 18 non-negative integers "
                "whose sum is 100"
            )
        if any(value < 0 for value in tp_percent_mix):
            raise ValueError("--hybench-tp-percent-mix values cannot be negative")
        tp_mix_props = {
            f"tp{index}_percent": str(value)
            for index, value in enumerate(tp_percent_mix, start=1)
        }
        update_hybench_props(
            template_path=hybench_root / "conf/pg.props",
            output_path=props_path,
            conn=conn,
            benchmark=benchmark,
            run_mins=args.run_mins,
            tp_clients=args.tp_clients,
            ap_clients=args.ap_clients,
            random_seed=args.random_seed,
            ap_query_ids=args.hybench_ap_query_ids or None if benchmark == "hybench_ap" else None,
            ap_query_weights=args.hybench_ap_query_weights or None if benchmark == "hybench_ap" else None,
            ap_query_sequence=args.hybench_ap_query_sequence or None if benchmark == "hybench_ap" else None,
            statement_timeout_ms=args.statement_timeout_ms,
            work_mem_mb=(
                args.hybench_ap_work_mem_mb
                if benchmark == "hybench_ap" and args.target == "memory"
                else 0
            ),
            max_parallel_workers_per_gather=(
                args.hybench_ap_max_parallel_workers_per_gather
                if benchmark == "hybench_ap" and args.target == "memory"
                # ``-1`` is the update_hybench_props sentinel for "do not
                # override PostgreSQL".  Passing 0 here used to serialize the
                # AP side of HyBench XP (and every other non-memory HyBench
                # workload), which made an IO pilot look non-bottlenecked for
                # a workload-generation reason rather than an optimizer one.
                else -1
            ),
            extra_props=tp_mix_props,
        )
        statement_path = build_hybench_statement_profile(
            base_path=hybench_root / "conf/stmt_postgres.toml",
            output_path=statement_path,
            raw_overrides=[],
        )
        mode = {
            "hybench_ap": "runap",
            "hybench_tp": "runtp",
            "hybench_xp": "runxp",
        }[benchmark]
        command = [
            "bash",
            "hybench",
            "-t",
            mode,
            "-c",
            str(props_path),
            "-f",
            str(statement_path),
        ]
        return "cd " + shlex.quote(str(hybench_root)) + " && " + shlex.join(command)

    if benchmark in {"tpcc", "tpch"}:
        template_name = {
            "tpcc": "sample_tpcc_config.xml",
            "tpch": "sample_tpch_config.xml",
        }[benchmark]
        xml_path = run_dir / f"{benchmark}.xml"
        update_benchbase_xml(
            template_path=BENCHBASE_ROOT / "config/postgres" / template_name,
            output_path=xml_path,
            benchmark=benchmark,
            conn=conn,
            time_sec=args.run_mins * 60,
            terminals=args.benchbase_terminals,
            scalefactor=args.tpcc_scale if benchmark == "tpcc" else args.tpch_scale,
            work_mem_mb=(
                args.benchbase_work_mem_mb
                if args.benchbase_work_mem_mb > 0
                else (96 if args.target == "memory" else 0)
            ),
            max_parallel_workers_per_gather=(
                args.benchbase_max_parallel_workers_per_gather
                if args.target == "memory"
                else -1
            ),
            weights=(
                "0,0,0,0,100"
                if benchmark == "tpcc" and args.target == "cpu"
                else None
            ),
            serial=(
                False
                # A serial TPC-H run completes one 22-query pass and exits
                # before its declared observation horizon.  That leaves a
                # single short, plan-sensitive sample per replay, so the
                # current-run Top15 resource/ranking gate cannot establish a
                # reliable surrogate.  Memory already uses the timed
                # non-serial form; None must use the same complete workload
                # rather than a shorter variant of TPC-H.
                if benchmark == "tpch" and args.target in {"memory", "none"}
                else None
            ),
        )
        return shlex.join(
            [
                str(args.python),
                str(ROOT / "shell/run_benchmark_capture.py"),
                "benchbase",
                "--root",
                str(BENCHBASE_ROOT),
                "--benchmark",
                benchmark,
                "--config",
                str(xml_path),
                "--java-xmx",
                args.java_xmx,
            ]
        )

    if benchmark == "job":
        job_work_mem_mb = (
            args.job_work_mem_mb
            if args.job_work_mem_mb > 0
            else (96 if args.target == "memory" else 0)
        )
        job_workers = (
            args.job_workers_override
            if args.job_workers_override > 0
            else (24 if args.target == "memory" else 1)
        )
        return shlex.join(
            [
                str(args.python),
                str(ROOT / "shell/run_benchmark_capture.py"),
                "job",
                "--dsn",
                conn.dsn(),
                "--sql-dir",
                str(JOB_SQL_DIR),
                "--rounds",
                str(args.job_rounds),
                "--statement-timeout-ms",
                str(args.statement_timeout_ms),
                "--work-mem-mb",
                str(job_work_mem_mb),
                "--workers",
                str(job_workers),
            ]
        )
    raise ValueError(f"unsupported benchmark: {benchmark}")


def scoring_target_ranges(target: str) -> Dict[str, Dict[str, float]]:
    result: Dict[str, Dict[str, float]] = {}
    for resource in ("cpu", "memory", "io"):
        if resource == target:
            if resource == "io":
                # IO throughput can sit just below a fixed percentage of the
                # directional throttle even while latency clearly shows an IO
                # bottleneck.  Leave the physical band unspecified so the
                # live runner derives the required 5%--10% sweet band from
                # this invocation's fresh real baseline.  This avoids making
                # the disk throttle more extreme merely to pass preflight.
                result[resource] = {}
            else:
                result[resource] = {"min_pct": 85.0, "max_pct": 90.0}
        else:
            # A non-target resource keeps positive utility up to its physical
            # capacity; this implements "prefer higher, but it is optional".
            result[resource] = {"max_pct": 100.0}
    return result


def build_runtime(args: argparse.Namespace) -> Dict[str, str]:
    run_dir = args.output_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    reference = json.loads(REFERENCE_CONFIG.read_text(encoding="utf-8"))
    dbname = args.dbname or DB_NAMES[args.benchmark]
    template_db = args.template_db or TEMPLATE_DB_NAMES[args.benchmark]
    conn = PgConn(args.host, args.port, args.user, args.password, dbname)
    command = benchmark_command(args, run_dir, conn)
    io_capacity = float(args.read_mb + args.write_mb)
    measured_memory_gib = (
        float(args.measured_mem_gb)
        if args.measured_mem_gb > 0.0
        else guest_mem_total_gib(args.host, args.ssh_port)
    )
    capacity = {
        "cpu": float(args.cpu_cores),
        "memory": measured_memory_gib,
        "io": io_capacity,
    }
    scenario_config_path = run_dir / "scenario_config.json"
    worker_config_path = run_dir / "worker_config.json"
    runtime_seconds = max(args.run_mins * 60, args.statement_timeout_ms / 1000.0)
    if args.benchmark == "job" and args.target == "memory":
        # A JOB replay contains several complete rounds.  The per-statement
        # timeout bounds one SQL, not the entire parallel round sequence.
        runtime_seconds = max(
            runtime_seconds,
            args.job_rounds * args.statement_timeout_ms / 1000.0,
        )
    if args.benchmark == "job" and args.job_fixed_horizon_s > 0:
        # JOB contains a few extremely long-tail SQLs.  Formal optimization
        # compares every parameter vector over one common physical observation
        # window instead of letting a single query's statement_timeout define
        # the replay duration.  The monitor treats this owned fixed horizon as
        # a successful measured run after collecting resource samples.
        runtime_seconds = float(args.job_fixed_horizon_s)
    if args.workload_fixed_horizon_s > 0:
        # Some benchmarks finish their declared measurement windows before
        # long-running client futures drain.  Compare candidates over the
        # declared physical observation window and keep tail cleanup outside
        # the resource/QPS measurement.
        runtime_seconds = float(args.workload_fixed_horizon_s)
    explicit_fixed_horizon_s = float(
        args.workload_fixed_horizon_s
        or (args.job_fixed_horizon_s if args.benchmark == "job" else 0)
    )
    workload_concurrency = {
        "hybench_ap": args.ap_clients,
        "hybench_tp": args.tp_clients,
        "hybench_xp": args.tp_clients + args.ap_clients,
        "tpcc": args.benchbase_terminals,
        "tpch": args.benchbase_terminals,
        "job": 1,
    }[args.benchmark]
    if args.vm_shm_mb < 0:
        raise ValueError("--vm-shm-mb cannot be negative")
    drop_cache_parts = []
    shared_buffers_mb = 0
    if args.target == "memory":
        if args.benchmark == "hybench_ap":
            shared_buffers_mb = int(args.hybench_ap_shared_buffers_mb)
        elif args.benchmark in {"tpch", "tpcc"}:
            shared_buffers_mb = int(args.benchbase_shared_buffers_mb)
    if shared_buffers_mb > 0:
        shared_buffers = f"{shared_buffers_mb}MB"
        drop_cache_parts.extend(
            [
                (
                    "sudo -n -u postgres psql -p 25432 "
                    "-d postgres -v ON_ERROR_STOP=1 "
                    f"-c \"ALTER SYSTEM SET shared_buffers = '{shared_buffers}'\""
                ),
                (
                    "sudo -n systemctl restart postgresql17-resource-adaptive"
                ),
                "sleep 5",
            ]
        )
    # Keep the cache eviction self-contained.  Some clean rootless seeds do
    # not carry the historical helper script, while passwordless guest sudo
    # and the kernel drop_caches interface are part of the validated image.
    drop_cache_parts.append(
        "sudo -n sync && sudo -n sh -c 'echo 3 > /proc/sys/vm/drop_caches'"
    )
    if args.vm_shm_mb > 0:
        drop_cache_parts.append(
            f"sudo -n mount -o remount,size={args.vm_shm_mb}M /dev/shm"
        )
    if shared_buffers_mb > 0:
        drop_cache_parts.extend(
            [
                "sleep 10",
                (
                    f"PGPASSWORD={shlex.quote(args.password)} psql -h 127.0.0.1 "
                    # This command executes inside the guest over SSH.  The
                    # host-forwarded controller port is not reachable there;
                    # PostgreSQL always listens on its guest-local 25432.
                    f"-p 25432 -U {shlex.quote(args.user)} -d postgres "
                    "-v ON_ERROR_STOP=1 -At "
                    "-c 'SELECT 1'"
                ),
            ]
        )
    drop_cache_command = " && ".join(drop_cache_parts)
    param_ranges = copy.deepcopy(reference["param_ranges"])
    if (
        args.benchmark == "job"
        and args.target == "memory"
        and args.job_min_seq_page_cost > 0.0
    ):
        seq_range = param_ranges["seq_page_cost"]
        if args.job_min_seq_page_cost > float(seq_range["max_value"]):
            raise ValueError("JOB seq_page_cost lower bound exceeds its maximum")
        seq_range["min_value"] = max(
            float(seq_range["min_value"]),
            float(args.job_min_seq_page_cost),
        )

    scenario_config: Dict[str, Any] = {
        "experiment_runtime": {
            "benchmark": args.benchmark,
            "target": args.target,
            "vm_name": args.vm_name,
            "formal_run": True,
            "memory_capacity_provenance": "guest_proc_meminfo_MemTotal",
            "nominal_vm_memory_gib": float(args.mem_gb),
            "measured_guest_memory_gib": measured_memory_gib,
            "hybench_ap_shared_buffers_mb": int(
                args.hybench_ap_shared_buffers_mb
                if args.benchmark == "hybench_ap" and args.target == "memory"
                else 0
            ),
            "benchbase_work_mem_mb": int(
                args.benchbase_work_mem_mb
                if args.benchmark in {"tpch", "tpcc"}
                else 0
            ),
            "benchbase_shared_buffers_mb": int(
                shared_buffers_mb
                if args.benchmark in {"tpch", "tpcc"}
                else 0
            ),
            "benchbase_max_parallel_workers_per_gather": int(
                args.benchbase_max_parallel_workers_per_gather
                if args.benchmark in {"tpch", "tpcc"} and args.target == "memory"
                else -1
            ),
            "job_fixed_horizon_s": int(
                args.job_fixed_horizon_s if args.benchmark == "job" else 0
            ),
            "job_min_seq_page_cost": float(
                args.job_min_seq_page_cost if args.benchmark == "job" else 0.0
            ),
            "workload_fixed_horizon_s": int(args.workload_fixed_horizon_s),
        },
        "postgres": {
            "host": args.host,
            "port": args.port,
            "user": args.user,
            "password": args.password,
            "dbname": dbname,
        },
        "initial_params": copy.deepcopy(reference["initial_params"]),
        "param_ranges": param_ranges,
        "plan_capture": copy.deepcopy(reference.get("plan_capture") or {}),
        "capability": capacity,
        "resource_capacity": capacity,
        "scoring": {
            "overload_threshold": 1.0,
            "score_mode": "band_target",
            "resource_target_ranges": scoring_target_ranges(args.target),
        },
        "metrics": {
            "plan_estimator": copy.deepcopy(
                ((reference.get("metrics") or {}).get("plan_estimator") or {})
            ),
            "vm_resource": {
                "vm_name": args.vm_name,
                "cpu": args.cpu_cores,
                "memory_gb": args.mem_gb,
                "memory_capacity_gb": measured_memory_gib,
                "read_mb": args.read_mb,
                "write_mb": args.write_mb,
                "disk_dev": "vda",
                "qmp_socket": str(args.qmp_socket or ""),
            },
            "vm_runtime": {
                "host": args.host,
                "ssh_target": f"anonymous@{args.host}",
                "ssh_port": int(args.ssh_port),
                "cpu_mode": "vm_busy",
                "memory_mode": "vm_used",
                "io_mode": "disk_device",
                "io_capability_mode": "sum_directional_limits",
                "disk_devices": ["vda"],
                "interval_sec": 1.0,
                "warmup_sec": 10.0,
                "connect_timeout_sec": 5.0,
                "max_runtime_s": (
                    explicit_fixed_horizon_s
                    if explicit_fixed_horizon_s > 0.0
                    else runtime_seconds + 45.0
                ),
                "fixed_horizon_success": True,
                "fixed_horizon_grace_s": 10.0,
                "ssh_control_master": True,
                "light_remote_script": True,
                "interval_correct_sleep": True,
            },
        },
        "benchmark": {
            "name": args.benchmark,
            "workload_cmd": command,
        },
        "pre_benchmark_recovery": {
            "snapshot_restore": {
                "enabled": True,
                "template_db": template_db,
                # PostgreSQL 17 FILE_COPY preserves the clean-template reset
                # semantics while avoiding WAL logging of every copied block.
                "strategy": "file_copy",
                # Keep the immutable seed for task-level reset, but do not
                # rebuild the whole host QEMU guest before every Top-K replay.
                # The host sudo/PAM path is shared with unrelated experiments
                # and can block for minutes.  FILE_COPY from the immutable
                # template plus guest-local PostgreSQL restart/cache eviction
                # provides a clean replay without changing the optimizer.
                "seeded_qemu_reset": {
                    "enabled": False,
                    "disabled_reason": "host_control_plane_removed_from_replay_hot_path",
                    "task_seed_overlay": (
                        str(args.seed_overlay.resolve()) if args.seed_overlay else ""
                    ),
                    "task_work_overlay": (
                        str(args.work_overlay.resolve()) if args.work_overlay else ""
                    ),
                },
                "restore_io_boost": (
                    {"enabled": True, "read_mb": 800, "write_mb": 800}
                    if args.target == "io"
                    else {"enabled": False}
                ),
            },
            "vm_drop_cache": {
                "enabled": True,
                "required": True,
                "command": drop_cache_command,
            },
        },
        "binary_plan_tuning": {
            "workload_name": args.benchmark,
            "workload_context": {
                "workload_family": args.benchmark,
                "concurrency": workload_concurrency,
                "feature_window_seconds": 10,
            },
            # Deliberately use the fresh baseline's pg_stat_statements rows.
            # This keeps the feature-pruning workload benchmark-specific.
            "workload": {"name": args.benchmark},
            "endpoint_screening": copy.deepcopy(
                ((reference.get("binary_plan_tuning") or {}).get("endpoint_screening") or {})
            ),
            "validation": {
                "vm_replay": {
                    "enabled": True,
                    "snapshot_restore_timeout_s": 900,
                    "drop_cache_timeout_s": 180,
                    "workload_timeout_s": runtime_seconds + 180.0,
                    "worker_configs": {args.target: str(worker_config_path)},
                }
            },
        },
    }
    worker_config = {
        "enabled": True,
        "worker_id": args.vm_name,
        "vm_name": args.vm_name,
        "scenarios": [args.target],
        "scenario_configs": {args.target: str(scenario_config_path)},
        "scenario_overrides": {args.target: {}},
        "config_override": {
            "postgres": scenario_config["postgres"],
            "metrics": {"vm_runtime": scenario_config["metrics"]["vm_runtime"]},
            "resource_capacity": capacity,
            "resource_capability": capacity,
        },
        "vm_resource": {
            "vm_name": args.vm_name,
            "cpu": args.cpu_cores,
            "memory_gb": args.mem_gb,
            "memory_capacity_gb": measured_memory_gib,
            "read_mb": args.read_mb,
            "write_mb": args.write_mb,
            "disk_dev": "vda",
            "qmp_socket": str(args.qmp_socket or ""),
        },
        "disk_isolation_id": args.disk_isolation_id,
        "disk_isolation_verified": bool(args.disk_isolation_id),
    }
    write_json(scenario_config_path, scenario_config)
    write_json(worker_config_path, worker_config)
    result = {
        "scenario_config": str(scenario_config_path),
        "worker_config": str(worker_config_path),
        "workload_cmd": command,
        "database": dbname,
        "template_database": template_db,
    }
    write_json(run_dir / "runtime_manifest.json", result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=tuple(DB_NAMES), required=True)
    parser.add_argument("--target", choices=("cpu", "memory", "io", "none"), required=True)
    parser.add_argument("--vm-name", required=True)
    parser.add_argument("--exp-id", default="")
    parser.add_argument("--repeat-id", type=int, default=1)
    parser.add_argument("--mac", default="")
    parser.add_argument("--seed-overlay", type=Path)
    parser.add_argument("--work-overlay", type=Path)
    parser.add_argument("--host", required=True)
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cpu-cores", type=int, required=True)
    parser.add_argument("--mem-gb", type=float, required=True)
    parser.add_argument("--measured-mem-gb", type=float, default=0.0)
    parser.add_argument("--read-mb", type=float, required=True)
    parser.add_argument("--write-mb", type=float, required=True)
    parser.add_argument("--disk-isolation-id", default="")
    parser.add_argument("--qmp-socket", default="")
    parser.add_argument("--port", type=int, default=25432)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default='CHANGE_ME')
    parser.add_argument("--dbname", default="")
    parser.add_argument("--template-db", default="")
    parser.add_argument("--run-mins", type=int, default=3)
    parser.add_argument("--tp-clients", type=int, default=32)
    parser.add_argument("--ap-clients", type=int, default=4)
    parser.add_argument("--hybench-ap-work-mem-mb", type=int, default=64)
    parser.add_argument("--hybench-ap-shared-buffers-mb", type=int, default=0)
    parser.add_argument("--hybench-ap-query-ids", default="")
    parser.add_argument("--hybench-ap-query-weights", default="")
    parser.add_argument("--hybench-ap-query-sequence", default="")
    parser.add_argument("--hybench-tp-percent-mix", default="")
    parser.add_argument(
        "--hybench-root",
        type=Path,
        default=HYBENCH_ROOT,
        help="isolated HyBench runtime root used by this formal task",
    )
    parser.add_argument(
        "--hybench-ap-max-parallel-workers-per-gather",
        type=int,
        default=-1,
    )
    parser.add_argument(
        "--vm-shm-mb",
        type=int,
        default=0,
        help="remount the owned guest /dev/shm to this size after every seeded reset",
    )
    parser.add_argument("--benchbase-terminals", type=int, default=16)
    parser.add_argument("--benchbase-work-mem-mb", type=int, default=0)
    parser.add_argument("--benchbase-shared-buffers-mb", type=int, default=0)
    parser.add_argument(
        "--benchbase-max-parallel-workers-per-gather",
        type=int,
        default=-1,
    )
    parser.add_argument("--tpcc-scale", type=float, default=1.0)
    parser.add_argument("--tpch-scale", type=float, default=3.0)
    parser.add_argument("--job-rounds", type=int, default=1)
    parser.add_argument("--job-workers-override", type=int, default=0)
    parser.add_argument("--job-work-mem-mb", type=int, default=0)
    parser.add_argument("--job-min-seq-page-cost", type=float, default=0.0)
    parser.add_argument("--job-fixed-horizon-s", type=int, default=0)
    parser.add_argument("--workload-fixed-horizon-s", type=int, default=0)
    parser.add_argument("--statement-timeout-ms", type=int, default=900000)
    parser.add_argument("--java-xmx", default="8g")
    parser.add_argument("--random-seed", type=int, default=20260718)
    parser.add_argument(
        "--python",
        type=Path,
        default=Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"),
    )
    args = parser.parse_args()
    if min(args.cpu_cores, args.mem_gb, args.read_mb, args.write_mb, args.run_mins) <= 0:
        parser.error("resource capacities and run duration must be positive")
    if min(
        args.hybench_ap_shared_buffers_mb,
        args.benchbase_work_mem_mb,
        args.benchbase_shared_buffers_mb,
    ) < 0:
        parser.error("work_mem/shared_buffers overrides cannot be negative")
    if args.benchbase_max_parallel_workers_per_gather < -1:
        parser.error(
            "--benchbase-max-parallel-workers-per-gather must be -1 or non-negative"
        )
    if not (args.hybench_root / "hybench").is_file():
        parser.error(f"HyBench launcher is missing: {args.hybench_root / 'hybench'}")
    if min(
        args.job_workers_override,
        args.job_work_mem_mb,
        args.job_min_seq_page_cost,
        args.job_fixed_horizon_s,
        args.workload_fixed_horizon_s,
    ) < 0:
        parser.error("workload fixed-horizon and JOB overrides cannot be negative")
    if args.hybench_ap_shared_buffers_mb >= args.mem_gb * 1024.0 * 0.75:
        parser.error(
            "--hybench-ap-shared-buffers-mb must stay below 75% of nominal VM memory"
        )
    if args.benchbase_shared_buffers_mb >= args.mem_gb * 1024.0 * 0.75:
        parser.error(
            "--benchbase-shared-buffers-mb must stay below 75% of nominal VM memory"
        )
    return args


def main() -> int:
    print(json.dumps(build_runtime(parse_args()), ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
