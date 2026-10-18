"""Run the corrected MySQL gradient matrix with compatible-worker work stealing."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Sequence

from benchmark_estimator import _last_json_object
from experiment_matrix_mysql import LogicalRun, evaluate_acceptance, expand_runs, load_manifest
from generate_mysql_gd_configs import build_config
from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter


WORKSPACE = Path(__file__).resolve().parent
ALL_BENCHMARKS = ("hybench_ap", "hybench_tp", "hybench_xp", "job", "tpch", "tpcc")


class WorkerUnavailableError(RuntimeError):
    """The selected MySQL endpoint disappeared during a logical run."""


def is_worker_unavailable_error(detail: str) -> bool:
    normalized = detail.lower()
    return any(marker in normalized for marker in (
        "error 2003", "can't connect to mysql server", "connection refused",
        "communications link failure", "can't create tcp/ip socket",
    ))


@dataclass(frozen=True)
class Worker:
    worker_id: str
    benchmarks: frozenset[str]
    port: int
    mysqld_pid: int
    database: str

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Worker":
        benchmarks = frozenset(str(item).lower() for item in row.get("benchmarks", []))
        if not benchmarks or not benchmarks <= set(ALL_BENCHMARKS):
            raise ValueError(f"invalid benchmarks for worker {row!r}")
        worker = cls(
            worker_id=str(row["worker_id"]), benchmarks=benchmarks,
            port=int(row["port"]), mysqld_pid=int(row["mysqld_pid"]),
            database=str(row["database"]),
        )
        if not worker.worker_id or worker.port <= 0 or worker.mysqld_pid <= 0 or not worker.database:
            raise ValueError(f"invalid worker {row!r}")
        return worker

    def as_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id, "benchmarks": sorted(self.benchmarks),
            "port": self.port, "mysqld_pid": self.mysqld_pid, "database": self.database,
        }


def load_workers(path: Path) -> List[Worker]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("workers") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list) or not rows:
        raise ValueError("workers JSON must be a non-empty list or {'workers': [...]} object")
    workers = [Worker.from_dict(row) for row in rows]
    for attribute in ("worker_id", "port", "mysqld_pid"):
        values = [getattr(worker, attribute) for worker in workers]
        if len(values) != len(set(values)):
            raise ValueError(f"worker {attribute} values must be unique")
    return workers


def claim_compatible(pending: List[LogicalRun], worker: Worker) -> LogicalRun | None:
    """Remove and return the oldest task that this worker can execute."""
    for index, run in enumerate(pending):
        if run.benchmark in worker.benchmarks:
            return pending.pop(index)
    return None


def dispatch_worker_tasks(
    worker: Worker,
    claim_task: Callable[[Worker], LogicalRun | None],
    execute_task: Callable[[Worker, LogicalRun], Mapping[str, Any]],
    *, lock_root: Path = Path("/tmp"),
) -> Dict[str, Any]:
    """Run compatible tasks with a cross-process lock scoped to one task.

    Claiming happens only after the worker lock is acquired.  Releasing it
    after every logical run lets any waiting scheduler immediately dispatch
    its oldest compatible task to this newly idle machine.
    """
    lock_path = lock_root / f"resource_adaptive_mysql_gd_worker_{worker.port}.lock"
    claimed: List[str] = []
    while True:
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                run = claim_task(worker)
                if run is None:
                    return {"worker_id": worker.worker_id, "claimed_runs": claimed}
                result = execute_task(worker, run)
                claimed.extend(str(run_id) for run_id in result["claimed_runs"])
                if result.get("stop_worker"):
                    return {"worker_id": worker.worker_id, "claimed_runs": claimed,
                            "stop_reason": result.get("stop_reason")}
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _replace_flag(command: List[str], flag: str, value: Any, *, required: bool = False) -> None:
    try:
        index = command.index(flag)
    except ValueError:
        if required:
            raise ValueError(f"estimator command is missing {flag}")
        return
    if index + 1 >= len(command):
        raise ValueError(f"estimator command has no value after {flag}")
    command[index + 1] = str(value)


def bind_config(base: Mapping[str, Any], run: LogicalRun, worker: Worker, output_root: Path) -> Dict[str, Any]:
    config = copy.deepcopy(dict(base))
    config["mysql"]["port"] = worker.port
    config["mysql"]["database"] = worker.database
    command = list(config["estimator"]["command"])
    _replace_flag(command, "--port", worker.port, required=True)
    _replace_flag(command, "--mysqld-pid", worker.mysqld_pid, required=True)
    _replace_flag(command, "--database", worker.database)
    measurements = output_root / "artifacts" / run.run_id / worker.worker_id / "measurements"
    _replace_flag(command, "--output-root", measurements, required=True)
    config["estimator"]["command"] = command
    config["output"]["directory"] = str(output_root / "artifacts" / run.run_id / worker.worker_id / "optimizer")
    config["dispatch"] = {**worker.as_dict(), "run_id": run.run_id}
    return config


def apply_workload_profile(config: Dict[str, Any], benchmark: str, profile: str) -> None:
    if profile == "original":
        return
    if profile not in {"stable", "stabilized", "replicated"}:
        raise ValueError(f"unknown workload profile {profile!r}")
    command = config["estimator"]["command"]
    stabilized_load = profile in {"stabilized", "replicated"}
    long_window = profile == "stabilized"
    _replace_flag(command, "--warmup-duration-sec", 30 if long_window else 10)
    _replace_flag(command, "--duration-sec", 60 if long_window else 30)
    if benchmark == "hybench_ap":
        _replace_flag(command, "--ap-clients", 16 if stabilized_load else 8, required=True)
    elif benchmark == "hybench_tp":
        _replace_flag(command, "--tp-clients", 16 if stabilized_load else 32, required=True)
    elif benchmark == "hybench_xp":
        _replace_flag(command, "--xap-clients", 8 if stabilized_load else 4, required=True)
        _replace_flag(command, "--xtp-clients", 16 if stabilized_load else 32, required=True)
        if profile == "replicated" and "--vary-random-seed" not in command:
            command.append("--vary-random-seed")
    elif benchmark == "job":
        _replace_flag(command, "--concurrency", 16 if stabilized_load else 8, required=True)
        _replace_flag(command, "--query-timeout-ms", 20000 if stabilized_load else 10000, required=True)
    elif benchmark == "tpcc":
        _replace_flag(command, "--terminals", 16 if stabilized_load else 64, required=True)
    elif benchmark == "tpch":
        _replace_flag(command, "--terminals", 16 if stabilized_load else 8, required=True)
        command.extend(["--query-ids", "1,9,13,18,21"])
    config["dispatch"]["workload_profile"] = profile


def apply_hybench_client_overrides(
    config: Dict[str, Any], benchmark: str, *, ap_clients: int | None = None,
    tp_clients: int | None = None, xap_clients: int | None = None,
    xtp_clients: int | None = None,
) -> None:
    """Override only HyBench load, leaving the optimizer framework unchanged."""
    command = config["estimator"]["command"]
    overrides: Dict[str, int] = {}
    if benchmark == "hybench_ap" and ap_clients is not None:
        if ap_clients <= 0:
            raise ValueError("HyBench AP clients must be positive")
        _replace_flag(command, "--ap-clients", ap_clients, required=True)
        overrides["ap_clients"] = ap_clients
    if benchmark == "hybench_tp" and tp_clients is not None:
        if tp_clients <= 0:
            raise ValueError("HyBench TP clients must be positive")
        _replace_flag(command, "--tp-clients", tp_clients, required=True)
        overrides["tp_clients"] = tp_clients
    if benchmark == "hybench_xp" and xap_clients is not None:
        if xap_clients <= 0:
            raise ValueError("HyBench XP AP clients must be positive")
        _replace_flag(command, "--xap-clients", xap_clients, required=True)
        overrides["xap_clients"] = xap_clients
    if benchmark == "hybench_xp" and xtp_clients is not None:
        if xtp_clients <= 0:
            raise ValueError("HyBench XP TP clients must be positive")
        _replace_flag(command, "--xtp-clients", xtp_clients, required=True)
        overrides["xtp_clients"] = xtp_clients
    if overrides:
        config["dispatch"]["workload_overrides"] = overrides


def apply_optimizer_step_overrides(
    config: Dict[str, Any], *, eta: float | None = None,
    min_delta_pct: float | None = None, max_delta_pct: float | None = None,
    max_delta_ratio: float | None = None, max_iter: int | None = None,
) -> None:
    """Tune gradient step magnitude without changing the optimizer framework."""
    optimizer = config["optimizer"]
    if eta is not None:
        if eta <= 0:
            raise ValueError("optimizer eta must be positive")
        optimizer["eta"] = eta
    if min_delta_pct is not None:
        if min_delta_pct < 0:
            raise ValueError("minimum parameter delta percent must be non-negative")
        optimizer["min_param_delta_rel_pct"] = min_delta_pct
    if max_delta_pct is not None:
        if max_delta_pct < 0:
            raise ValueError("maximum parameter delta percent must be non-negative")
        optimizer["max_param_delta_rel_pct"] = max_delta_pct
    if max_delta_ratio is not None:
        if not 0 < max_delta_ratio <= 1:
            raise ValueError("maximum parameter delta ratio must be in (0, 1]")
        optimizer["max_param_delta_ratio"] = max_delta_ratio
    if max_iter is not None:
        if max_iter <= 0:
            raise ValueError("optimizer max_iter must be positive")
        optimizer["max_iter"] = max_iter
    minimum = float(optimizer["min_param_delta_rel_pct"])
    maximum = float(optimizer["max_param_delta_rel_pct"])
    if maximum and minimum > maximum:
        raise ValueError("minimum parameter delta percent cannot exceed maximum")
    applied = {
        key: optimizer[key] for key in (
            "eta", "min_param_delta_rel_pct", "max_param_delta_rel_pct",
            "max_param_delta_ratio",
            "max_iter",
        )
    }
    config["dispatch"]["optimizer_step_overrides"] = applied


def apply_capability_overrides(
    config: Dict[str, Any], *, cpu: float | None = None,
    memory: float | None = None, io: float | None = None,
) -> None:
    overrides = {
        name: float(value) for name, value in (
            ("cpu", cpu), ("memory", memory), ("io", io),
        ) if value is not None
    }
    if any(value <= 0 for value in overrides.values()):
        raise ValueError("capability overrides must be positive")
    if overrides:
        config["capability"].update(overrides)
        config["dispatch"]["capability_overrides"] = overrides


def apply_hybench_query_override(
    config: Dict[str, Any], benchmark: str, sequence: str | None,
    tp_transaction_id: int | None = None, tp_transaction_mix: str | None = None,
    prewarm_sql: str | None = None,
) -> None:
    command = config["estimator"]["command"]
    overrides: Dict[str, Any] = {}
    if sequence and benchmark == "hybench_xp":
        raise ValueError(
            "HyBench XP uses native uniform-random IQ1-IQ7 selection; "
            "AP query sequence overrides are prohibited"
        )
    if sequence and benchmark == "hybench_ap":
        try:
            query_ids = [int(item.strip()) for item in sequence.split(",") if item.strip()]
        except ValueError as exc:
            raise ValueError("HyBench AP query sequence must contain integers") from exc
        if not query_ids or any(item < 1 or item > 13 for item in query_ids):
            raise ValueError("HyBench AP query sequence IDs must be in [1, 13]")
        normalized = ",".join(str(item) for item in query_ids)
        command.extend(["--ap-query-sequence", normalized])
        overrides["ap_query_sequence"] = normalized
    if tp_transaction_id is not None and benchmark in {"hybench_tp", "hybench_xp"}:
        if not 1 <= tp_transaction_id <= 18:
            raise ValueError("HyBench TP transaction ID must be in [1, 18]")
        command.extend(["--tp-transaction-id", str(tp_transaction_id)])
        overrides["tp_transaction_id"] = tp_transaction_id
    if tp_transaction_mix and benchmark in {"hybench_tp", "hybench_xp"}:
        if tp_transaction_id is not None:
            raise ValueError("HyBench TP transaction ID and mix are mutually exclusive")
        try:
            pairs = [item.split(":", 1) for item in tp_transaction_mix.split(",")]
            parsed = {int(key): int(value) for key, value in pairs}
        except (TypeError, ValueError) as exc:
            raise ValueError("HyBench TP mix must use ID:PERCENT comma pairs") from exc
        if (
            any(key < 1 or key > 18 or value < 0 for key, value in parsed.items())
            or sum(parsed.values()) != 100
        ):
            raise ValueError("HyBench TP mix IDs must be in [1,18] and percentages sum to 100")
        normalized = ",".join(f"{key}:{parsed[key]}" for key in sorted(parsed))
        command.extend(["--tp-transaction-mix", normalized])
        overrides["tp_transaction_mix"] = normalized
    if prewarm_sql and benchmark in {"hybench_ap", "hybench_tp", "hybench_xp"}:
        command.extend(["--prewarm-sql", prewarm_sql])
        overrides["hybench_prewarm_sql"] = prewarm_sql
    if overrides:
        config["dispatch"]["workload_overrides"] = {
            **config["dispatch"].get("workload_overrides", {}), **overrides,
        }


def apply_non_hybench_load_overrides(
    config: Dict[str, Any], benchmark: str, *, job_concurrency: int | None = None,
    job_prewarm_sql: str | None = None,
    tpch_terminals: int | None = None, tpch_prewarm_sql: str | None = None,
    tpcc_terminals: int | None = None,
    tpcc_weights: str | None = None,
) -> None:
    command = config["estimator"]["command"]
    overrides: Dict[str, int] = {}
    if benchmark == "job" and job_concurrency is not None:
        if job_concurrency <= 0:
            raise ValueError("JOB concurrency must be positive")
        _replace_flag(command, "--concurrency", job_concurrency, required=True)
        overrides["job_concurrency"] = job_concurrency
    if benchmark == "job" and job_prewarm_sql:
        command.extend(["--prewarm-sql", job_prewarm_sql])
        config["dispatch"]["workload_overrides"] = {
            **config["dispatch"].get("workload_overrides", {}),
            "job_prewarm_sql": job_prewarm_sql,
        }
    if benchmark == "tpch" and tpch_terminals is not None:
        if tpch_terminals <= 0:
            raise ValueError("TPC-H terminals must be positive")
        _replace_flag(command, "--terminals", tpch_terminals, required=True)
        overrides["tpch_terminals"] = tpch_terminals
    if benchmark == "tpch" and tpch_prewarm_sql:
        command.extend(["--prewarm-sql", tpch_prewarm_sql])
        config["dispatch"]["workload_overrides"] = {
            **config["dispatch"].get("workload_overrides", {}),
            "tpch_prewarm_sql": tpch_prewarm_sql,
        }
    if benchmark == "tpcc" and tpcc_terminals is not None:
        if tpcc_terminals <= 0:
            raise ValueError("TPC-C terminals must be positive")
        _replace_flag(command, "--terminals", tpcc_terminals, required=True)
        overrides["tpcc_terminals"] = tpcc_terminals
    if benchmark == "tpcc" and tpcc_weights:
        try:
            parsed = [int(item.strip()) for item in tpcc_weights.split(",") if item.strip()]
        except ValueError as exc:
            raise ValueError("TPC-C weights must contain integers") from exc
        if len(parsed) != 5 or any(weight < 0 for weight in parsed) or sum(parsed) != 100:
            raise ValueError("TPC-C weights must be five non-negative integers summing to 100")
        normalized = ",".join(str(weight) for weight in parsed)
        command.extend(["--tpcc-weights", normalized])
        config["dispatch"]["workload_overrides"] = {
            **config["dispatch"].get("workload_overrides", {}),
            "tpcc_weights": normalized,
        }
    if overrides:
        config["dispatch"]["workload_overrides"] = {
            **config["dispatch"].get("workload_overrides", {}), **overrides,
        }


def apply_parameter_profile(config: Dict[str, Any], profile: str) -> None:
    """Select a parameter interval while retaining the gradient-descent engine."""
    if profile == "default":
        return
    profiles = {
        "wide_io_cost": {
            "active": ["io_block_read_cost"],
            "baseline_values": {"io_block_read_cost": 100.0},
            "ranges": {"io_block_read_cost": {"min_value": 0.01, "max_value": 100.0}},
        },
        "wide_read_cost": {
            "active": ["io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "tpcc_flush": {
            "active": ["innodb_flush_log_at_trx_commit"],
            "baseline_values": {"innodb_flush_log_at_trx_commit": 1.0},
            "start_values": {"innodb_flush_log_at_trx_commit": 2.0},
            "ranges": {
                "innodb_flush_log_at_trx_commit": {"min_value": 1.0, "max_value": 2.0},
            },
        },
        "spin_wait": {
            "active": ["innodb_spin_wait_delay", "innodb_sync_spin_loops"],
            "baseline_values": {
                "innodb_spin_wait_delay": 1000.0, "innodb_sync_spin_loops": 1000.0,
            },
            "ranges": {
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "spin_wait_mid": {
            "active": ["innodb_spin_wait_delay", "innodb_sync_spin_loops"],
            "baseline_values": {
                "innodb_spin_wait_delay": 1000.0, "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_spin_wait_delay": 650.0, "innodb_sync_spin_loops": 650.0,
            },
            "ranges": {
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "spin_wait_800": {
            "active": ["innodb_spin_wait_delay", "innodb_sync_spin_loops"],
            "baseline_values": {
                "innodb_spin_wait_delay": 1000.0, "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_spin_wait_delay": 800.0, "innodb_sync_spin_loops": 800.0,
            },
            "ranges": {
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "spin_wait_min": {
            "active": ["innodb_spin_wait_delay", "innodb_sync_spin_loops"],
            "baseline_values": {
                "innodb_spin_wait_delay": 1000.0, "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_spin_wait_delay": 6.0, "innodb_sync_spin_loops": 30.0,
            },
            "ranges": {
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "tp_cpu_hybrid_topk": {
            # Combine the durable-QPS endpoint with the screened low-spin
            # endpoint so throughput can rise while CPU contention falls.
            "active": [
                "innodb_flush_log_at_trx_commit",
                "innodb_spin_wait_delay", "innodb_sync_spin_loops",
            ],
            "baseline_values": {
                "innodb_flush_log_at_trx_commit": 1.0,
                "innodb_spin_wait_delay": 1000.0,
                "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_flush_log_at_trx_commit": 2.0,
                "innodb_spin_wait_delay": 6.0,
                "innodb_sync_spin_loops": 30.0,
            },
            "ranges": {
                "innodb_flush_log_at_trx_commit": {"min_value": 1.0, "max_value": 2.0},
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "cpu_hybrid_800_topk": {
            # Combine the durable-QPS endpoint with a moderate spin step.  The
            # 6/30 endpoint cleared CPU by more than the requested 10 points;
            # 800/800 targets the requested 5-10 point band.
            "active": [
                "innodb_flush_log_at_trx_commit",
                "innodb_spin_wait_delay", "innodb_sync_spin_loops",
            ],
            "baseline_values": {
                "innodb_flush_log_at_trx_commit": 1.0,
                "innodb_spin_wait_delay": 1000.0,
                "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_flush_log_at_trx_commit": 2.0,
                "innodb_spin_wait_delay": 800.0,
                "innodb_sync_spin_loops": 800.0,
            },
            "ranges": {
                "innodb_flush_log_at_trx_commit": {"min_value": 1.0, "max_value": 2.0},
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "cpu_hybrid_650_topk": {
            # On the write-only TPC-C mix, 800/800 preserved a 25% QPS gain
            # but reduced CPU by only 3.36 points.  Move one screened step
            # toward the lower-spin regime to target the required 5-10 band.
            "active": [
                "innodb_flush_log_at_trx_commit",
                "innodb_spin_wait_delay", "innodb_sync_spin_loops",
            ],
            "baseline_values": {
                "innodb_flush_log_at_trx_commit": 1.0,
                "innodb_spin_wait_delay": 1000.0,
                "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_flush_log_at_trx_commit": 2.0,
                "innodb_spin_wait_delay": 650.0,
                "innodb_sync_spin_loops": 650.0,
            },
            "ranges": {
                "innodb_flush_log_at_trx_commit": {"min_value": 1.0, "max_value": 2.0},
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "cpu_hybrid_775_topk": {
            # The write-only TPC-C screen brackets a sharp transition:
            # 800/800 drops only 3.36 CPU points while 650/650 over-drops.
            # Stay close to the throughput-positive side of that bracket.
            "active": [
                "innodb_flush_log_at_trx_commit",
                "innodb_spin_wait_delay", "innodb_sync_spin_loops",
            ],
            "baseline_values": {
                "innodb_flush_log_at_trx_commit": 1.0,
                "innodb_spin_wait_delay": 1000.0,
                "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_flush_log_at_trx_commit": 2.0,
                "innodb_spin_wait_delay": 775.0,
                "innodb_sync_spin_loops": 775.0,
            },
            "ranges": {
                "innodb_flush_log_at_trx_commit": {"min_value": 1.0, "max_value": 2.0},
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "cpu_hybrid_790_topk": {
            # Write-only TPC-C now brackets the target tightly: 775/775
            # over-drops CPU by 13 points and 800/800 drops 3.36 points.
            "active": [
                "innodb_flush_log_at_trx_commit",
                "innodb_spin_wait_delay", "innodb_sync_spin_loops",
            ],
            "baseline_values": {
                "innodb_flush_log_at_trx_commit": 1.0,
                "innodb_spin_wait_delay": 1000.0,
                "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_flush_log_at_trx_commit": 2.0,
                "innodb_spin_wait_delay": 790.0,
                "innodb_sync_spin_loops": 790.0,
            },
            "ranges": {
                "innodb_flush_log_at_trx_commit": {"min_value": 1.0, "max_value": 2.0},
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "cpu_hybrid_785_topk": {
            # On write-only TPC-C, 800/800 released only 3.36 CPU points and
            # 775/775 released 13.02 points.  This realizable TopK midpoint
            # targets the required 5-10 point interval without changing GD.
            "active": [
                "innodb_flush_log_at_trx_commit",
                "innodb_spin_wait_delay", "innodb_sync_spin_loops",
            ],
            "baseline_values": {
                "innodb_flush_log_at_trx_commit": 1.0,
                "innodb_spin_wait_delay": 1000.0,
                "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_flush_log_at_trx_commit": 2.0,
                "innodb_spin_wait_delay": 785.0,
                "innodb_sync_spin_loops": 785.0,
            },
            "ranges": {
                "innodb_flush_log_at_trx_commit": {"min_value": 1.0, "max_value": 2.0},
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "cpu_hybrid_783_topk": {
            # Final TPC-C bracket: 775/775 gives a 13-point drop and 790/790
            # gives 2.12 points.  Linear interpolation targets about 7 points.
            "active": [
                "innodb_flush_log_at_trx_commit",
                "innodb_spin_wait_delay", "innodb_sync_spin_loops",
            ],
            "baseline_values": {
                "innodb_flush_log_at_trx_commit": 1.0,
                "innodb_spin_wait_delay": 1000.0,
                "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_flush_log_at_trx_commit": 2.0,
                "innodb_spin_wait_delay": 783.0,
                "innodb_sync_spin_loops": 783.0,
            },
            "ranges": {
                "innodb_flush_log_at_trx_commit": {"min_value": 1.0, "max_value": 2.0},
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "cpu_hybrid_780_topk": {
            # The replicated write-only TPC-C screens place the requested
            # 5-10 point CPU reduction between 775/775 and 790/790.  Keep the
            # QPS-positive durable-write endpoint and probe the lower middle
            # of that narrow transition after the long-window run failed to
            # establish a CPU-bottleneck baseline.
            "active": [
                "innodb_flush_log_at_trx_commit",
                "innodb_spin_wait_delay", "innodb_sync_spin_loops",
            ],
            "baseline_values": {
                "innodb_flush_log_at_trx_commit": 1.0,
                "innodb_spin_wait_delay": 1000.0,
                "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_flush_log_at_trx_commit": 2.0,
                "innodb_spin_wait_delay": 780.0,
                "innodb_sync_spin_loops": 780.0,
            },
            "ranges": {
                "innodb_flush_log_at_trx_commit": {"min_value": 1.0, "max_value": 2.0},
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "cpu_hybrid_350_topk": {
            # Empirical bracket: 800/800 stayed in the high-spin regime and
            # raised CPU, while 6/30 reduced CPU by 11-16 points.  The 350
            # endpoint lies in the interpolated overlap for a 5-10 point drop.
            "active": [
                "innodb_flush_log_at_trx_commit",
                "innodb_spin_wait_delay", "innodb_sync_spin_loops",
            ],
            "baseline_values": {
                "innodb_flush_log_at_trx_commit": 1.0,
                "innodb_spin_wait_delay": 1000.0,
                "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_flush_log_at_trx_commit": 2.0,
                "innodb_spin_wait_delay": 350.0,
                "innodb_sync_spin_loops": 350.0,
            },
            "ranges": {
                "innodb_flush_log_at_trx_commit": {"min_value": 1.0, "max_value": 2.0},
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "cpu_hybrid_280_topk": {
            "active": [
                "innodb_flush_log_at_trx_commit",
                "innodb_spin_wait_delay", "innodb_sync_spin_loops",
            ],
            "baseline_values": {
                "innodb_flush_log_at_trx_commit": 1.0,
                "innodb_spin_wait_delay": 1000.0,
                "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_flush_log_at_trx_commit": 2.0,
                "innodb_spin_wait_delay": 280.0,
                "innodb_sync_spin_loops": 280.0,
            },
            "ranges": {
                "innodb_flush_log_at_trx_commit": {"min_value": 1.0, "max_value": 2.0},
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "cpu_hybrid_270_topk": {
            # Current HyBench TP bracket: 280/280 releases only 3.07 CPU
            # points, while the 250/250 screen over-releases.  Evaluate the
            # nearest chunk-free intermediate TopK point.
            "active": [
                "innodb_flush_log_at_trx_commit",
                "innodb_spin_wait_delay", "innodb_sync_spin_loops",
            ],
            "baseline_values": {
                "innodb_flush_log_at_trx_commit": 1.0,
                "innodb_spin_wait_delay": 1000.0,
                "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_flush_log_at_trx_commit": 2.0,
                "innodb_spin_wait_delay": 270.0,
                "innodb_sync_spin_loops": 270.0,
            },
            "ranges": {
                "innodb_flush_log_at_trx_commit": {"min_value": 1.0, "max_value": 2.0},
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "cpu_hybrid_250_topk": {
            "active": [
                "innodb_flush_log_at_trx_commit",
                "innodb_spin_wait_delay", "innodb_sync_spin_loops",
            ],
            "baseline_values": {
                "innodb_flush_log_at_trx_commit": 1.0,
                "innodb_spin_wait_delay": 1000.0,
                "innodb_sync_spin_loops": 1000.0,
            },
            "start_values": {
                "innodb_flush_log_at_trx_commit": 2.0,
                "innodb_spin_wait_delay": 250.0,
                "innodb_sync_spin_loops": 250.0,
            },
            "ranges": {
                "innodb_flush_log_at_trx_commit": {"min_value": 1.0, "max_value": 2.0},
                "innodb_spin_wait_delay": {"min_value": 6.0, "max_value": 1000.0},
                "innodb_sync_spin_loops": {"min_value": 30.0, "max_value": 1000.0},
            },
        },
        "memory_buffers": {
            "active": ["join_buffer_size", "sort_buffer_size"],
            "baseline_values": {
                "join_buffer_size": 67108864.0, "sort_buffer_size": 67108864.0,
            },
            "ranges": {
                "join_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "sort_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
            },
        },
        "memory_pool_topk": {
            "active": ["io_block_read_cost", "innodb_buffer_pool_size"],
            "baseline_values": {
                "io_block_read_cost": 10000.0,
                "innodb_buffer_pool_size": 2147483648.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1879048192.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 10000.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
            },
        },
        "memory_deep_topk": {
            "active": [
                "io_block_read_cost", "innodb_buffer_pool_size",
                "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 10000.0,
                "innodb_buffer_pool_size": 2147483648.0,
                "join_buffer_size": 67108864.0, "sort_buffer_size": 67108864.0,
                "read_buffer_size": 67108864.0, "read_rnd_buffer_size": 67108864.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1879048192.0,
                "join_buffer_size": 8388608.0, "sort_buffer_size": 8388608.0,
                "read_buffer_size": 8388608.0, "read_rnd_buffer_size": 8388608.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 10000.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
                "join_buffer_size": {"min_value": 1048576.0, "max_value": 67108864.0},
                "sort_buffer_size": {"min_value": 1048576.0, "max_value": 67108864.0},
                "read_buffer_size": {"min_value": 1048576.0, "max_value": 67108864.0},
                "read_rnd_buffer_size": {"min_value": 1048576.0, "max_value": 67108864.0},
            },
        },
        "xp_io_pool_topk": {
            "active": ["innodb_buffer_pool_size"],
            "baseline_values": {"innodb_buffer_pool_size": 268435456.0},
            "start_values": {"innodb_buffer_pool_size": 335544320.0},
            "ranges": {
                "innodb_buffer_pool_size": {
                    "min_value": 268435456.0, "max_value": 536870912.0,
                },
            },
        },
        "xp_io_pool_mild_topk": {
            "active": ["innodb_buffer_pool_size"],
            "baseline_values": {"innodb_buffer_pool_size": 268435456.0},
            # This worker uses a 128 MiB effective resize quantum at this pool
            # size, so keep the screened candidate on an actually realizable
            # boundary rather than accepting a rounded/no-op request.
            "start_values": {"innodb_buffer_pool_size": 402653184.0},
            "ranges": {
                "innodb_buffer_pool_size": {
                    "min_value": 268435456.0, "max_value": 536870912.0,
                },
            },
        },
        "xp_io_pool_full_topk": {
            "active": ["innodb_buffer_pool_size"],
            "baseline_values": {"innodb_buffer_pool_size": 268435456.0},
            "start_values": {"innodb_buffer_pool_size": 536870912.0},
            "ranges": {
                "innodb_buffer_pool_size": {
                    "min_value": 268435456.0, "max_value": 536870912.0,
                },
            },
        },
        "xp_io_pool_flush_topk": {
            # The full 256 -> 512 MiB pool endpoint raised replicated XP QPS
            # by 13.5%, but left bandwidth unchanged.  Pair it with the
            # screened durable-write relaxation so transaction throughput can
            # clear 15% while synchronous write pressure falls into the
            # requested post-bottleneck band.
            "active": [
                "innodb_buffer_pool_size", "innodb_flush_log_at_trx_commit",
            ],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "innodb_flush_log_at_trx_commit": 1.0,
            },
            "start_values": {
                "innodb_buffer_pool_size": 536870912.0,
                "innodb_flush_log_at_trx_commit": 2.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {
                    "min_value": 268435456.0, "max_value": 536870912.0,
                },
                "innodb_flush_log_at_trx_commit": {
                    "min_value": 1.0, "max_value": 2.0,
                },
            },
        },
        "xp_io_pool_cost_topk": {
            # Pair the realizable 256 -> 384 MiB pool step with the replicated
            # XP cost endpoint that previously produced >15% QPS.  Keeping
            # both in one candidate lets the unchanged GD loop evaluate the
            # throughput/IO trade-off as a single TopK point.
            "active": [
                "innodb_buffer_pool_size",
                "io_block_read_cost", "memory_block_read_cost",
                "row_evaluate_cost", "key_compare_cost",
                "disk_temptable_create_cost", "disk_temptable_row_cost",
                "memory_temptable_create_cost", "memory_temptable_row_cost",
            ],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 1.0, "memory_block_read_cost": 0.25,
                "row_evaluate_cost": 0.1, "key_compare_cost": 0.05,
                "disk_temptable_create_cost": 20.0,
                "disk_temptable_row_cost": 0.5,
                "memory_temptable_create_cost": 1.0,
                "memory_temptable_row_cost": 0.1,
            },
            "start_values": {
                "innodb_buffer_pool_size": 402653184.0,
                "io_block_read_cost": 0.8, "memory_block_read_cost": 0.2,
                "row_evaluate_cost": 0.12, "key_compare_cost": 0.06,
                "disk_temptable_create_cost": 16.0,
                "disk_temptable_row_cost": 0.4,
                "memory_temptable_create_cost": 0.8,
                "memory_temptable_row_cost": 0.08,
            },
            "ranges": {
                "innodb_buffer_pool_size": {
                    "min_value": 268435456.0, "max_value": 536870912.0,
                },
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "row_evaluate_cost": {"min_value": 0.01, "max_value": 10.0},
                "key_compare_cost": {"min_value": 0.01, "max_value": 10.0},
                "disk_temptable_create_cost": {"min_value": 0.01, "max_value": 100.0},
                "disk_temptable_row_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_temptable_create_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_temptable_row_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool_topk": {
            # E30's screened Q9 direction delivered >200% QPS.  Add the mild
            # one-chunk increase on the dedicated 64 MiB worker so the same
            # unchanged GD run can trade that gain against an IO-bottleneck
            # reduction without repeating the 384 MiB endpoint's over-drop.
            "active": [
                "innodb_buffer_pool_size", "io_block_read_cost",
                "memory_block_read_cost",
            ],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 335544320.0,
                "io_block_read_cost": 100.0,
                "memory_block_read_cost": 100.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {
                    "min_value": 268435456.0, "max_value": 536870912.0,
                },
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool320_topk": {
            # The dedicated XP worker uses one 64 MiB pool chunk.  Keep the
            # Q9 read-cost endpoint but limit the resident-pool step to the
            # exact 256 -> 320 MiB boundary so IO moves by 5-10 points rather
            # than the oversized 256 -> 384 MiB change.
            "active": [
                "innodb_buffer_pool_size", "io_block_read_cost",
                "memory_block_read_cost",
            ],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 335544320.0,
                "io_block_read_cost": 100.0,
                "memory_block_read_cost": 100.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {
                    "min_value": 268435456.0, "max_value": 536870912.0,
                },
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool288_topk": {
            # On the current XP process the 336 MiB candidate over-cleared IO
            # by 35 points.  Limit the cache increase to two 16 MiB chunks;
            # the Q9 cost endpoint remains available to preserve throughput.
            "active": [
                "innodb_buffer_pool_size", "io_block_read_cost",
                "memory_block_read_cost",
            ],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 301989888.0,
                "io_block_read_cost": 100.0,
                "memory_block_read_cost": 100.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {
                    "min_value": 268435456.0, "max_value": 536870912.0,
                },
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool264_topk": {
            # O_DIRECT screening showed 360 MiB over-clears IO.  Probe the
            # smallest realizable 8 MiB step above the 256 MiB baseline.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 276824064.0,
                "io_block_read_cost": 100.0,
                "memory_block_read_cost": 100.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool272_topk": {
            # Second aligned O_DIRECT boundary probe: two 8 MiB steps.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 285212672.0,
                "io_block_read_cost": 100.0,
                "memory_block_read_cost": 100.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool272_cost5_topk": {
            # Smallest cost step used when cost=10 over-clears IO.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 285212672.0,
                "io_block_read_cost": 5.0,
                "memory_block_read_cost": 5.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool272_cost10_topk": {
            # Fine-grained low-cost screen for the 272 MiB boundary.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 285212672.0,
                "io_block_read_cost": 10.0,
                "memory_block_read_cost": 10.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool272_cost10p25_topk": {
            # Narrow interpolation between the high-QPS cost=10 result and
            # the stronger IO-release cost=11 result for B9-r2.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 285212672.0,
                "io_block_read_cost": 10.25,
                "memory_block_read_cost": 10.25,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool272_cost11_topk": {
            # Interpolated probe for the narrow B9-r2 IO/QPS trade-off.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 285212672.0,
                "io_block_read_cost": 11.0,
                "memory_block_read_cost": 11.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool272_cost12p5_topk": {
            # Interpolated Top-K probe: cost=10 was too weak and cost=15
            # over-cleared IO on the isolated XP workers.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 285212672.0,
                "io_block_read_cost": 12.5,
                "memory_block_read_cost": 12.5,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool272_cost15_topk": {
            # Fine-grained midpoint between cost=10 and cost=20.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 285212672.0,
                "io_block_read_cost": 15.0,
                "memory_block_read_cost": 15.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool272_cost17p5_topk": {
            # Interpolated probe between the throughput-negative cost=12.5
            # and IO-increasing cost=25 A30 screens.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 285212672.0,
                "io_block_read_cost": 17.5,
                "memory_block_read_cost": 17.5,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool272_cost20_topk": {
            # Fine-grained upper probe below the cost=25 boundary.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 285212672.0,
                "io_block_read_cost": 20.0,
                "memory_block_read_cost": 20.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool272_cost25_topk": {
            # The 272 MiB / cost=100 screen passed QPS but over-dropped IO.
            # Keep the throughput-positive pool boundary and reduce both cost
            # endpoints to one quarter for a 5-10 point IO target.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 285212672.0,
                "io_block_read_cost": 25.0,
                "memory_block_read_cost": 25.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool272_cost50_topk": {
            # Midpoint cost screen for the same 272 MiB boundary.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 285212672.0,
                "io_block_read_cost": 50.0,
                "memory_block_read_cost": 50.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool272_cost75_topk": {
            # Upper midpoint cost screen for the same 272 MiB boundary.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 285212672.0,
                "io_block_read_cost": 75.0,
                "memory_block_read_cost": 75.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool280_topk": {
            # Third aligned O_DIRECT boundary probe: three 8 MiB steps.
            "active": ["innodb_buffer_pool_size", "io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 293601280.0,
                "io_block_read_cost": 100.0,
                "memory_block_read_cost": 100.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {"min_value": 268435456.0, "max_value": 536870912.0},
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool336_topk": {
            # The no-prewarm 320 MiB endpoint left IO unchanged/slightly up,
            # while the 352 MiB endpoint over-cleared IO.  The 16 MiB-chunk
            # XP worker can realize the exact midpoint without workload-cache
            # prewarming, isolating the pool-size effect.
            "active": [
                "innodb_buffer_pool_size", "io_block_read_cost",
                "memory_block_read_cost",
            ],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 352321536.0,
                "io_block_read_cost": 100.0,
                "memory_block_read_cost": 100.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {
                    "min_value": 268435456.0, "max_value": 536870912.0,
                },
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool352_topk": {
            # Use a dedicated 32 MiB-chunk XP worker to evaluate the midpoint
            # between the screened 320 MiB endpoint (too little IO relief) and
            # 384 MiB endpoint (too much IO relief).  Freeze the screened TopK
            # point so the unchanged GD loop measures this exact candidate.
            "active": [
                "innodb_buffer_pool_size", "io_block_read_cost",
                "memory_block_read_cost",
            ],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 369098752.0,
                "io_block_read_cost": 100.0,
                "memory_block_read_cost": 100.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {
                    "min_value": 268435456.0, "max_value": 536870912.0,
                },
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool362_topk": {
            # Fresh-process screens bracket a sharp boundary: 352 MiB keeps
            # the QPS gain but leaves IO high, while 368 MiB over-clears IO
            # and loses the gain.  A 2 MiB-chunk worker can probe the
            # interpolated 362 MiB TopK point without changing the optimizer.
            "active": [
                "innodb_buffer_pool_size", "io_block_read_cost",
                "memory_block_read_cost",
            ],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 379584512.0,
                "io_block_read_cost": 100.0,
                "memory_block_read_cost": 100.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {
                    "min_value": 268435456.0, "max_value": 536870912.0,
                },
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool360_topk": {
            # MySQL accepted the 362 MiB request but exposed 360 MiB as the
            # realized size on the live 2 MiB-chunk workers.  Probe that exact
            # realizable boundary so the adapter's equality check can finish.
            "active": [
                "innodb_buffer_pool_size", "io_block_read_cost",
                "memory_block_read_cost",
            ],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 377487360.0,
                "io_block_read_cost": 100.0,
                "memory_block_read_cost": 100.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {
                    "min_value": 268435456.0, "max_value": 536870912.0,
                },
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "xp_io_q9_pool368_topk": {
            # The 352 MiB endpoint remained on the high-IO side while the
            # 384 MiB screen over-cleared IO.  A dedicated 16 MiB-chunk worker
            # makes the intervening 368 MiB boundary realizable without any
            # optimizer-framework change.
            "active": [
                "innodb_buffer_pool_size", "io_block_read_cost",
                "memory_block_read_cost",
            ],
            "baseline_values": {
                "innodb_buffer_pool_size": 268435456.0,
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "innodb_buffer_pool_size": 385875968.0,
                "io_block_read_cost": 100.0,
                "memory_block_read_cost": 100.0,
            },
            "ranges": {
                "innodb_buffer_pool_size": {
                    "min_value": 268435456.0, "max_value": 536870912.0,
                },
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "ap_memory_topk": {
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "join_buffer_size": 67108864.0, "sort_buffer_size": 67108864.0,
                "read_buffer_size": 67108864.0, "read_rnd_buffer_size": 67108864.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "join_buffer_size": 58720256.0, "sort_buffer_size": 58720256.0,
                "read_buffer_size": 58720256.0, "read_rnd_buffer_size": 58720256.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "join_buffer_size": {"min_value": 33554432.0, "max_value": 67108864.0},
                "sort_buffer_size": {"min_value": 33554432.0, "max_value": 67108864.0},
                "read_buffer_size": {"min_value": 33554432.0, "max_value": 67108864.0},
                "read_rnd_buffer_size": {"min_value": 33554432.0, "max_value": 67108864.0},
            },
        },
        "ap_q11_plan_topk": {
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "join_buffer_size", "sort_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "join_buffer_size": 67108864.0, "sort_buffer_size": 67108864.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "join_buffer_size": 262144.0, "sort_buffer_size": 262144.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "join_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "sort_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
            },
        },
        "ap_q9_reverse_topk": {
            "active": ["io_block_read_cost", "memory_block_read_cost"],
            "baseline_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
            },
            "start_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
            },
        },
        "ap_q9_smallpool_topk": {
            # The dedicated XP worker has one buffer-pool instance, so 640 MiB
            # is an exact 128 MiB resize boundary.  Pair that real RSS-reducing
            # step with the Q9 cost direction that passed the endpoint screen.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1073741824.0,
            },
            "start_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 671088640.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 536870912.0, "max_value": 1073741824.0,
                },
            },
        },
        "ap_q9_pool960_prewarm_topk": {
            # The dedicated one-instance/64 MiB-chunk AP worker can realize a
            # mild 1 GiB -> 960 MiB step after the transfer table is prewarmed.
            # Preserve the Q9-screened 0.01 -> 100 cost direction for QPS.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1073741824.0,
            },
            "start_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 1006632960.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1006632960.0, "max_value": 1073741824.0,
                },
            },
        },
        "ap_q9_pool896_prewarm_topk": {
            # The 1 GiB -> 960 MiB endpoint did not release a resident chunk.
            # Move exactly one additional 64 MiB chunk while preserving the
            # Q9-screened read-cost direction and full-table prewarm.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1073741824.0,
            },
            "start_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 939524096.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 939524096.0, "max_value": 1073741824.0,
                },
            },
        },
        "ap_q9_pool864_prewarm_topk": {
            # A fresh-process 1 GiB -> 896 MiB run released 3.60 normalized
            # memory points while preserving a 26% QPS gain.  Move only 32
            # MiB deeper to target the missing 1.4 points without risking the
            # over-release of the old 640 MiB endpoint.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1073741824.0,
            },
            "start_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 905969664.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 905969664.0, "max_value": 1073741824.0,
                },
            },
        },
        "ap_q9_pool2g_prewarm_topk": {
            # The 1 GiB AP worker applied the online resize but retained its
            # high-water RSS.  Use the resident-pool boundary that released
            # 7 points for the prewarmed JOB run, while preserving the Q9 AP
            # read-cost direction that repeatedly improved throughput.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 2147483648.0,
            },
            "start_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 1879048192.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
            },
        },
        "ap_q9_heavy_release_topk": {
            # Q9 endpoint screening found that the .01 -> 100 cost direction
            # improves AP throughput.  Combine that direction with exact
            # runtime-memory release steps so the memory runs can target the
            # requested 5-10 point reduction without changing the optimizer.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size", "tmp_table_size",
                "max_heap_table_size", "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 2147483648.0,
                "tmp_table_size": 134217728.0, "max_heap_table_size": 134217728.0,
                "join_buffer_size": 67108864.0, "sort_buffer_size": 67108864.0,
                "read_buffer_size": 67108864.0, "read_rnd_buffer_size": 67108864.0,
            },
            "start_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 1073741824.0,
                "tmp_table_size": 16777216.0, "max_heap_table_size": 16777216.0,
                "join_buffer_size": 262144.0, "sort_buffer_size": 262144.0,
                "read_buffer_size": 262144.0, "read_rnd_buffer_size": 262144.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
                "tmp_table_size": {"min_value": 16777216.0, "max_value": 134217728.0},
                "max_heap_table_size": {"min_value": 16777216.0, "max_value": 134217728.0},
                "join_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "sort_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "read_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "read_rnd_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
            },
        },
        "ap_q9_128m_release_topk": {
            # AP Q9 at 10 clients retained a >30% QPS gain but released only
            # ~2 points with 64 MiB buffers.  Double the live-buffer endpoint
            # while preserving the screened 0.01 -> 100 cost direction.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size", "tmp_table_size",
                "max_heap_table_size", "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 2147483648.0,
                "tmp_table_size": 268435456.0, "max_heap_table_size": 268435456.0,
                "join_buffer_size": 134217728.0, "sort_buffer_size": 134217728.0,
                "read_buffer_size": 134217728.0, "read_rnd_buffer_size": 134217728.0,
            },
            "start_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 1073741824.0,
                "tmp_table_size": 16777216.0, "max_heap_table_size": 16777216.0,
                "join_buffer_size": 262144.0, "sort_buffer_size": 262144.0,
                "read_buffer_size": 262144.0, "read_rnd_buffer_size": 262144.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
                "tmp_table_size": {"min_value": 16777216.0, "max_value": 268435456.0},
                "max_heap_table_size": {"min_value": 16777216.0, "max_value": 268435456.0},
                "join_buffer_size": {"min_value": 262144.0, "max_value": 134217728.0},
                "sort_buffer_size": {"min_value": 262144.0, "max_value": 134217728.0},
                "read_buffer_size": {"min_value": 262144.0, "max_value": 134217728.0},
                "read_rnd_buffer_size": {"min_value": 262144.0, "max_value": 134217728.0},
            },
        },
        "ap_q9_mild_release_topk": {
            # Once Q9 has made the 2 GiB pool resident, the screened 2 -> 1 GiB
            # endpoint releases too much memory.  Keep the same QPS-positive
            # cost direction and use a 2 -> 1.5 GiB pool step instead.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 2147483648.0,
            },
            "start_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 1610612736.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
            },
        },
        "ap_q9_midbuffers_topk": {
            # With 32 AP sessions, 64 -> 256 KiB work buffers released about
            # 17 utilization points. Halving the buffers targets 5-10 points.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "tmp_table_size", "max_heap_table_size",
                "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "tmp_table_size": 134217728.0, "max_heap_table_size": 134217728.0,
                "join_buffer_size": 67108864.0, "sort_buffer_size": 67108864.0,
                "read_buffer_size": 67108864.0, "read_rnd_buffer_size": 67108864.0,
            },
            "start_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "tmp_table_size": 67108864.0, "max_heap_table_size": 67108864.0,
                "join_buffer_size": 33554432.0, "sort_buffer_size": 33554432.0,
                "read_buffer_size": 33554432.0, "read_rnd_buffer_size": 33554432.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "tmp_table_size": {"min_value": 16777216.0, "max_value": 134217728.0},
                "max_heap_table_size": {"min_value": 16777216.0, "max_value": 134217728.0},
                "join_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "sort_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "read_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "read_rnd_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
            },
        },
        "ap_q11_session_release_topk": {
            # Q11 actually commits the analytical buffer-pool pages and uses
            # per-session work buffers.  Keep the screened 100 -> .01 plan
            # direction, but make the baseline session footprint large enough
            # to be returned when the benchmark connections close.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size", "tmp_table_size",
                "max_heap_table_size", "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 2147483648.0,
                "tmp_table_size": 100663296.0, "max_heap_table_size": 100663296.0,
                "join_buffer_size": 100663296.0, "sort_buffer_size": 100663296.0,
                "read_buffer_size": 100663296.0, "read_rnd_buffer_size": 100663296.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1073741824.0,
                "tmp_table_size": 16777216.0, "max_heap_table_size": 16777216.0,
                "join_buffer_size": 262144.0, "sort_buffer_size": 262144.0,
                "read_buffer_size": 262144.0, "read_rnd_buffer_size": 262144.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
                "tmp_table_size": {"min_value": 16777216.0, "max_value": 100663296.0},
                "max_heap_table_size": {"min_value": 16777216.0, "max_value": 100663296.0},
                "join_buffer_size": {"min_value": 262144.0, "max_value": 100663296.0},
                "sort_buffer_size": {"min_value": 262144.0, "max_value": 100663296.0},
                "read_buffer_size": {"min_value": 262144.0, "max_value": 100663296.0},
                "read_rnd_buffer_size": {"min_value": 262144.0, "max_value": 100663296.0},
            },
        },
        "ap_screen_topk": {
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size", "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 2147483648.0,
                "join_buffer_size": 67108864.0, "sort_buffer_size": 67108864.0,
                "read_buffer_size": 67108864.0, "read_rnd_buffer_size": 67108864.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1879048192.0,
                "join_buffer_size": 262144.0, "sort_buffer_size": 262144.0,
                "read_buffer_size": 262144.0, "read_rnd_buffer_size": 262144.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
                "join_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "sort_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "read_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "read_rnd_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
            },
        },
        "memory_pool_tradeoff_topk": {
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size", "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 2147483648.0,
                "join_buffer_size": 1048576.0, "sort_buffer_size": 1048576.0,
                "read_buffer_size": 1048576.0, "read_rnd_buffer_size": 1048576.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1073741824.0,
                "join_buffer_size": 67108864.0, "sort_buffer_size": 67108864.0,
                "read_buffer_size": 67108864.0, "read_rnd_buffer_size": 67108864.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
                "join_buffer_size": {"min_value": 1048576.0, "max_value": 67108864.0},
                "sort_buffer_size": {"min_value": 1048576.0, "max_value": 67108864.0},
                "read_buffer_size": {"min_value": 1048576.0, "max_value": 67108864.0},
                "read_rnd_buffer_size": {"min_value": 1048576.0, "max_value": 67108864.0},
            },
        },
        "memory_pool_valid_topk": {
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size", "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 2147483648.0,
                "join_buffer_size": 1048576.0, "sort_buffer_size": 1048576.0,
                "read_buffer_size": 1048576.0, "read_rnd_buffer_size": 1048576.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1073741824.0,
                "join_buffer_size": 262144.0, "sort_buffer_size": 262144.0,
                "read_buffer_size": 262144.0, "read_rnd_buffer_size": 262144.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
                "join_buffer_size": {"min_value": 262144.0, "max_value": 1048576.0},
                "sort_buffer_size": {"min_value": 262144.0, "max_value": 1048576.0},
                "read_buffer_size": {"min_value": 262144.0, "max_value": 1048576.0},
                "read_rnd_buffer_size": {"min_value": 262144.0, "max_value": 1048576.0},
            },
        },
        "memory_runtime_release_topk": {
            # Exercise memory that is live while analytical queries execute,
            # then pair a smaller per-session footprint with the screened cost
            # direction and the only valid 2 GiB -> 1 GiB pool step.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size", "tmp_table_size",
                "max_heap_table_size", "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 2147483648.0,
                "tmp_table_size": 134217728.0, "max_heap_table_size": 134217728.0,
                "join_buffer_size": 16777216.0, "sort_buffer_size": 16777216.0,
                "read_buffer_size": 16777216.0, "read_rnd_buffer_size": 16777216.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1073741824.0,
                "tmp_table_size": 16777216.0, "max_heap_table_size": 16777216.0,
                "join_buffer_size": 262144.0, "sort_buffer_size": 262144.0,
                "read_buffer_size": 262144.0, "read_rnd_buffer_size": 262144.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
                "tmp_table_size": {"min_value": 16777216.0, "max_value": 134217728.0},
                "max_heap_table_size": {"min_value": 16777216.0, "max_value": 134217728.0},
                "join_buffer_size": {"min_value": 262144.0, "max_value": 16777216.0},
                "sort_buffer_size": {"min_value": 262144.0, "max_value": 16777216.0},
                "read_buffer_size": {"min_value": 262144.0, "max_value": 16777216.0},
                "read_rnd_buffer_size": {"min_value": 262144.0, "max_value": 16777216.0},
            },
        },
        "memory_runtime_release_heavy_topk": {
            # The 16 MiB baseline moved live RSS by only about 20 MiB.  Raise
            # analytical session buffers while preserving the screened plan
            # direction and the valid 2 GiB -> 1 GiB pool endpoint.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size", "tmp_table_size",
                "max_heap_table_size", "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 2147483648.0,
                "tmp_table_size": 134217728.0, "max_heap_table_size": 134217728.0,
                "join_buffer_size": 67108864.0, "sort_buffer_size": 67108864.0,
                "read_buffer_size": 67108864.0, "read_rnd_buffer_size": 67108864.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1073741824.0,
                "tmp_table_size": 16777216.0, "max_heap_table_size": 16777216.0,
                "join_buffer_size": 262144.0, "sort_buffer_size": 262144.0,
                "read_buffer_size": 262144.0, "read_rnd_buffer_size": 262144.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
                "tmp_table_size": {"min_value": 16777216.0, "max_value": 134217728.0},
                "max_heap_table_size": {"min_value": 16777216.0, "max_value": 134217728.0},
                "join_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "sort_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "read_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
                "read_rnd_buffer_size": {"min_value": 262144.0, "max_value": 67108864.0},
            },
        },
        "memory_runtime_release_128m_topk": {
            # Query 9 only materialized about 24 MiB of the 64 MiB session
            # buffers.  A 128 MiB baseline keeps the same screened endpoint
            # and GD search space while targeting a 5-10 point live-RSS drop.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size", "tmp_table_size",
                "max_heap_table_size", "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 2147483648.0,
                "tmp_table_size": 268435456.0, "max_heap_table_size": 268435456.0,
                "join_buffer_size": 134217728.0, "sort_buffer_size": 134217728.0,
                "read_buffer_size": 134217728.0, "read_rnd_buffer_size": 134217728.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1073741824.0,
                "tmp_table_size": 16777216.0, "max_heap_table_size": 16777216.0,
                "join_buffer_size": 262144.0, "sort_buffer_size": 262144.0,
                "read_buffer_size": 262144.0, "read_rnd_buffer_size": 262144.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
                "tmp_table_size": {"min_value": 16777216.0, "max_value": 268435456.0},
                "max_heap_table_size": {"min_value": 16777216.0, "max_value": 268435456.0},
                "join_buffer_size": {"min_value": 262144.0, "max_value": 134217728.0},
                "sort_buffer_size": {"min_value": 262144.0, "max_value": 134217728.0},
                "read_buffer_size": {"min_value": 262144.0, "max_value": 134217728.0},
                "read_rnd_buffer_size": {"min_value": 262144.0, "max_value": 134217728.0},
            },
        },
        "memory_pool_prewarm_mild_topk": {
            # Used with an explicit large-table prewarm: a single pool
            # instance can realize this 2 GiB -> 1.75 GiB boundary exactly.
            # The 256 MiB resident-page delta targets roughly 5-10 points,
            # while the screened read-cost endpoint preserves the QPS gain.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 100.0,
                "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 2147483648.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1879048192.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
            },
        },
        "memory_pool_prewarm_qps_topk": {
            # Keep the proven prewarmed 2 GiB -> 1.75 GiB resident-pool
            # reduction, but use the JOB read-cost endpoint that previously
            # delivered >100% throughput gain.  Sensitivities are frozen
            # below so the one GD step evaluates this realizable TopK point
            # instead of requesting a non-chunk-aligned runtime resize.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 10000.0,
                "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 2147483648.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01,
                "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 1879048192.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 10000.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
            },
        },
        "memory_prewarm_buffers_128m_topk": {
            # Keep the prewarmed resident-pool reduction to one 128 MiB
            # chunk, and recover the screened JOB throughput gain by also
            # releasing oversized per-session buffers.
            "active": [
                "io_block_read_cost", "memory_block_read_cost",
                "innodb_buffer_pool_size", "tmp_table_size",
                "max_heap_table_size", "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 100.0, "memory_block_read_cost": 100.0,
                "innodb_buffer_pool_size": 2147483648.0,
                "tmp_table_size": 268435456.0, "max_heap_table_size": 268435456.0,
                "join_buffer_size": 134217728.0, "sort_buffer_size": 134217728.0,
                "read_buffer_size": 134217728.0, "read_rnd_buffer_size": 134217728.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "innodb_buffer_pool_size": 2013265920.0,
                "tmp_table_size": 16777216.0, "max_heap_table_size": 16777216.0,
                "join_buffer_size": 262144.0, "sort_buffer_size": 262144.0,
                "read_buffer_size": 262144.0, "read_rnd_buffer_size": 262144.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
                "tmp_table_size": {"min_value": 16777216.0, "max_value": 268435456.0},
                "max_heap_table_size": {"min_value": 16777216.0, "max_value": 268435456.0},
                "join_buffer_size": {"min_value": 262144.0, "max_value": 134217728.0},
                "sort_buffer_size": {"min_value": 262144.0, "max_value": 134217728.0},
                "read_buffer_size": {"min_value": 262144.0, "max_value": 134217728.0},
                "read_rnd_buffer_size": {"min_value": 262144.0, "max_value": 134217728.0},
            },
        },
        "job_memory_topk": {
            "active": [
                "io_block_read_cost", "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 10000.0,
                "join_buffer_size": 16777216.0, "sort_buffer_size": 16777216.0,
                "read_buffer_size": 16777216.0, "read_rnd_buffer_size": 16777216.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01,
                "join_buffer_size": 10485760.0, "sort_buffer_size": 16777216.0,
                "read_buffer_size": 10485760.0, "read_rnd_buffer_size": 4194304.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 10000.0},
                "join_buffer_size": {"min_value": 1048576.0, "max_value": 16777216.0},
                "sort_buffer_size": {"min_value": 15728640.0, "max_value": 17825792.0},
                "read_buffer_size": {"min_value": 1048576.0, "max_value": 16777216.0},
                "read_rnd_buffer_size": {"min_value": 1048576.0, "max_value": 16777216.0},
            },
        },
        "job11d_memory_valid_topk": {
            "active": [
                "row_evaluate_cost", "innodb_buffer_pool_size",
                "join_buffer_size", "sort_buffer_size",
                "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "row_evaluate_cost": 100.0,
                "innodb_buffer_pool_size": 2147483648.0,
                "join_buffer_size": 1048576.0, "sort_buffer_size": 1048576.0,
                "read_buffer_size": 1048576.0, "read_rnd_buffer_size": 1048576.0,
            },
            "start_values": {
                "row_evaluate_cost": 0.1,
                "innodb_buffer_pool_size": 1073741824.0,
                "join_buffer_size": 262144.0, "sort_buffer_size": 262144.0,
                "read_buffer_size": 262144.0, "read_rnd_buffer_size": 262144.0,
            },
            "ranges": {
                "row_evaluate_cost": {"min_value": 0.01, "max_value": 100.0},
                "innodb_buffer_pool_size": {
                    "min_value": 1073741824.0, "max_value": 2147483648.0,
                },
                "join_buffer_size": {"min_value": 262144.0, "max_value": 1048576.0},
                "sort_buffer_size": {"min_value": 262144.0, "max_value": 1048576.0},
                "read_buffer_size": {"min_value": 262144.0, "max_value": 1048576.0},
                "read_rnd_buffer_size": {"min_value": 262144.0, "max_value": 1048576.0},
            },
        },
        "tpch_memory_topk": {
            "active": [
                "io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost",
                "join_buffer_size", "sort_buffer_size", "read_buffer_size", "read_rnd_buffer_size",
            ],
            "baseline_values": {
                "io_block_read_cost": 10000.0, "memory_block_read_cost": 10000.0,
                "row_evaluate_cost": 1.0,
                "join_buffer_size": 67108864.0, "sort_buffer_size": 67108864.0,
                "read_buffer_size": 67108864.0, "read_rnd_buffer_size": 67108864.0,
            },
            "start_values": {
                "io_block_read_cost": 0.01, "memory_block_read_cost": 0.01,
                "row_evaluate_cost": 0.1,
                "join_buffer_size": 50331648.0, "sort_buffer_size": 50331648.0,
                "read_buffer_size": 50331648.0, "read_rnd_buffer_size": 50331648.0,
            },
            "ranges": {
                "io_block_read_cost": {"min_value": 0.01, "max_value": 10000.0},
                "memory_block_read_cost": {"min_value": 0.01, "max_value": 10000.0},
                "row_evaluate_cost": {"min_value": 0.05, "max_value": 1.0},
                "join_buffer_size": {"min_value": 16777216.0, "max_value": 67108864.0},
                "sort_buffer_size": {"min_value": 16777216.0, "max_value": 67108864.0},
                "read_buffer_size": {"min_value": 16777216.0, "max_value": 67108864.0},
                "read_rnd_buffer_size": {"min_value": 16777216.0, "max_value": 67108864.0},
            },
        },
    }
    if profile not in profiles:
        raise ValueError(f"unknown parameter profile {profile!r}")
    selected = profiles[profile]
    config["cost_parameters"].update(selected)
    if profile == "tpcc_flush":
        config["sensitivity_table"]["innodb_flush_log_at_trx_commit"] = {
            "cpu": -0.15, "memory": 0.0, "io": -0.80,
        }
    elif profile in {"spin_wait", "spin_wait_mid", "spin_wait_800", "spin_wait_min"}:
        for name in ("innodb_spin_wait_delay", "innodb_sync_spin_loops"):
            config["sensitivity_table"][name] = {
                "cpu": 0.80, "memory": 0.0, "io": 0.05,
            }
    elif profile in {
        "tp_cpu_hybrid_topk", "cpu_hybrid_800_topk", "cpu_hybrid_650_topk",
        "cpu_hybrid_775_topk", "cpu_hybrid_790_topk", "cpu_hybrid_785_topk", "cpu_hybrid_783_topk",
        "cpu_hybrid_780_topk",
        "cpu_hybrid_350_topk", "cpu_hybrid_280_topk",
        "cpu_hybrid_270_topk", "cpu_hybrid_250_topk",
    }:
        for name in (
            "innodb_flush_log_at_trx_commit",
            "innodb_spin_wait_delay", "innodb_sync_spin_loops",
        ):
            config["sensitivity_table"][name] = {
                "cpu": 0.0, "memory": 0.0, "io": 0.0,
            }
    elif profile == "memory_buffers":
        for name in ("join_buffer_size", "sort_buffer_size"):
            config["sensitivity_table"][name] = {
                "cpu": -0.10, "memory": 0.90, "io": -0.05,
            }
    elif profile in {"memory_pool_topk", "memory_deep_topk"}:
        config["sensitivity_table"]["innodb_buffer_pool_size"] = {
            "cpu": -0.05, "memory": 1.0, "io": -0.50,
        }
        if profile == "memory_deep_topk":
            for name in (
                "join_buffer_size", "sort_buffer_size", "read_buffer_size",
                "read_rnd_buffer_size",
            ):
                config["sensitivity_table"][name] = {
                    "cpu": -0.10, "memory": 0.90, "io": -0.05,
                }
    elif profile in {
        "xp_io_pool_topk", "xp_io_pool_mild_topk", "xp_io_pool_full_topk",
        "xp_io_pool_flush_topk",
        "xp_io_pool_cost_topk", "xp_io_q9_pool_topk", "xp_io_q9_pool320_topk",
        "xp_io_q9_pool264_topk", "xp_io_q9_pool272_topk",
        "xp_io_q9_pool272_cost5_topk", "xp_io_q9_pool272_cost10_topk", "xp_io_q9_pool272_cost10p25_topk", "xp_io_q9_pool272_cost11_topk", "xp_io_q9_pool272_cost12p5_topk", "xp_io_q9_pool272_cost15_topk", "xp_io_q9_pool272_cost17p5_topk", "xp_io_q9_pool272_cost20_topk",
        "xp_io_q9_pool272_cost25_topk", "xp_io_q9_pool272_cost50_topk", "xp_io_q9_pool272_cost75_topk",
        "xp_io_q9_pool280_topk",
        "xp_io_q9_pool288_topk", "xp_io_q9_pool336_topk",
        "xp_io_q9_pool352_topk", "xp_io_q9_pool360_topk", "xp_io_q9_pool362_topk", "xp_io_q9_pool368_topk",
    }:
        config["sensitivity_table"]["innodb_buffer_pool_size"] = (
            {"cpu": 0.0, "memory": 0.0, "io": 0.0}
            if profile in {
                "xp_io_pool_mild_topk", "xp_io_pool_full_topk",
                "xp_io_pool_flush_topk",
                "xp_io_pool_cost_topk",
                "xp_io_q9_pool_topk", "xp_io_q9_pool264_topk",
                "xp_io_q9_pool272_topk", "xp_io_q9_pool272_cost5_topk", "xp_io_q9_pool272_cost10_topk", "xp_io_q9_pool272_cost10p25_topk", "xp_io_q9_pool272_cost11_topk", "xp_io_q9_pool272_cost12p5_topk",
                "xp_io_q9_pool272_cost15_topk", "xp_io_q9_pool272_cost17p5_topk", "xp_io_q9_pool272_cost20_topk", "xp_io_q9_pool272_cost25_topk",
                "xp_io_q9_pool272_cost50_topk", "xp_io_q9_pool272_cost75_topk",
                "xp_io_q9_pool280_topk", "xp_io_q9_pool288_topk",
                "xp_io_q9_pool320_topk", "xp_io_q9_pool336_topk",
                "xp_io_q9_pool352_topk", "xp_io_q9_pool360_topk", "xp_io_q9_pool362_topk", "xp_io_q9_pool368_topk",
            }
            else {"cpu": 0.05, "memory": 1.0, "io": -1.0}
        )
        if profile in {"xp_io_pool_cost_topk", "xp_io_q9_pool_topk", "xp_io_q9_pool264_topk", "xp_io_q9_pool272_topk", "xp_io_q9_pool272_cost5_topk", "xp_io_q9_pool272_cost10_topk", "xp_io_q9_pool272_cost10p25_topk", "xp_io_q9_pool272_cost11_topk", "xp_io_q9_pool272_cost12p5_topk", "xp_io_q9_pool272_cost15_topk", "xp_io_q9_pool272_cost17p5_topk", "xp_io_q9_pool272_cost20_topk", "xp_io_q9_pool272_cost25_topk", "xp_io_q9_pool272_cost50_topk", "xp_io_q9_pool272_cost75_topk", "xp_io_q9_pool280_topk", "xp_io_q9_pool288_topk", "xp_io_q9_pool320_topk", "xp_io_q9_pool336_topk", "xp_io_q9_pool352_topk", "xp_io_q9_pool360_topk", "xp_io_q9_pool362_topk", "xp_io_q9_pool368_topk"}:
            for name in config["cost_parameters"]["active"]:
                config["sensitivity_table"][name] = {
                    "cpu": 0.0, "memory": 0.0, "io": 0.0,
                }
    elif profile in {
        "ap_q9_reverse_topk", "ap_q9_smallpool_topk",
        "ap_q9_heavy_release_topk", "ap_q9_128m_release_topk",
        "ap_q9_pool960_prewarm_topk", "ap_q9_pool896_prewarm_topk",
        "ap_q9_pool864_prewarm_topk",
        "ap_q9_pool2g_prewarm_topk",
        "ap_q9_mild_release_topk",
        "ap_q9_midbuffers_topk",
        "ap_q11_session_release_topk",
    }:
        # Preserve the screened TopK endpoint during the single gradient step;
        # the run still executes through the unchanged gradient-descent engine.
        for name in config["cost_parameters"]["active"]:
            config["sensitivity_table"][name] = {
                "cpu": 0.0, "memory": 0.0, "io": 0.0,
            }
    elif profile in {
        "memory_runtime_release_topk", "memory_runtime_release_heavy_topk",
        "memory_runtime_release_128m_topk",
        "memory_pool_prewarm_mild_topk", "memory_pool_prewarm_qps_topk",
        "memory_prewarm_buffers_128m_topk",
    }:
        for name in config["cost_parameters"]["active"]:
            config["sensitivity_table"][name] = {
                "cpu": 0.0, "memory": 0.0, "io": 0.0,
            }
    elif profile in {"ap_memory_topk", "ap_q11_plan_topk", "ap_screen_topk", "memory_pool_tradeoff_topk", "memory_pool_valid_topk", "job_memory_topk"}:
        for name in (
            "join_buffer_size", "sort_buffer_size", "read_buffer_size", "read_rnd_buffer_size",
        ):
            config["sensitivity_table"][name] = {
                "cpu": -0.10, "memory": 0.90, "io": -0.05,
            }
        if profile in {"ap_screen_topk", "memory_pool_tradeoff_topk", "memory_pool_valid_topk"}:
            config["sensitivity_table"]["innodb_buffer_pool_size"] = {
                "cpu": -0.05, "memory": 1.0, "io": -0.50,
            }
    elif profile in {"tpch_memory_topk", "job11d_memory_valid_topk"}:
        for name in (
            "join_buffer_size", "sort_buffer_size", "read_buffer_size", "read_rnd_buffer_size",
        ):
            config["sensitivity_table"][name] = {
                "cpu": -0.10, "memory": 0.90, "io": -0.05,
            }
        if profile == "job11d_memory_valid_topk":
            config["sensitivity_table"]["innodb_buffer_pool_size"] = {
                "cpu": -0.05, "memory": 1.0, "io": -0.50,
            }
    config["dispatch"]["parameter_profile"] = profile


def _run_command(command: Sequence[str], *, cwd: str, timeout: float, environment: Mapping[str, Any]) -> Mapping[str, Any]:
    env = os.environ.copy()
    env.update({str(key): str(value) for key, value in environment.items()})
    proc = subprocess.run(
        list(command), cwd=cwd, env=env, capture_output=True, text=True,
        timeout=timeout if timeout > 0 else None, check=False,
    )
    if proc.returncode:
        detail = (proc.stderr or proc.stdout)[-4000:].strip()
        raise RuntimeError(f"capability preflight failed (exit={proc.returncode}): {detail}")
    return _last_json_object(proc.stdout)


def calibrate_bottleneck(
    config: Dict[str, Any], bottleneck: str, initial_util: float = 0.94, repeats: int = 1,
) -> Dict[str, Any]:
    if bottleneck == "none":
        return {"performed": False, "reason": "none_scenario"}
    estimator = config["estimator"]
    if repeats < 1:
        raise ValueError("calibration repeats must be positive")
    payloads = [
        _run_command(
            estimator["command"], cwd=str(estimator.get("cwd") or WORKSPACE),
            timeout=float(estimator.get("timeout_sec", 300)),
            environment=estimator.get("environment", {}),
        )
        for _ in range(repeats)
    ]
    resources = [payload.get("resource", payload) for payload in payloads]
    raw = {name: statistics.median(float(resource[name]) for resource in resources) for name in ("cpu", "memory", "io")}
    target = "memory" if bottleneck == "memory" else bottleneck
    measured = float(raw[target])
    if measured <= 0:
        raise RuntimeError(f"preflight measured non-positive {target}: {measured}")
    old = float(config["capability"][target])
    config["capability"][target] = measured / initial_util
    calibration = {
        "performed": True, "initial_util_target": initial_util,
        "repeats": repeats,
        "target_resource": target, "raw_resource": {key: float(raw[key]) for key in ("cpu", "memory", "io")},
        "old_capability": old, "calibrated_capability": config["capability"][target],
        "qps": statistics.median(float(payload.get("qps", 0.0)) for payload in payloads),
        "samples": [
            {"resource": {key: float(resource[key]) for key in ("cpu", "memory", "io")}, "qps": float(payload.get("qps", 0.0))}
            for payload, resource in zip(payloads, resources)
        ],
    }
    config["dispatch"]["capability_calibration"] = calibration
    return calibration


def assert_default_costs(config: Mapping[str, Any]) -> None:
    mysql = config["mysql"]
    env = os.environ.copy()
    env["MYSQL_PWD"] = str(mysql.get("password", ""))
    sql = (
        "SELECT (SELECT COUNT(*) FROM mysql.server_cost WHERE cost_value IS NOT NULL)+"
        "(SELECT COUNT(*) FROM mysql.engine_cost WHERE cost_value IS NOT NULL)"
    )
    command = [
        str(mysql.get("mysql_binary", "mysql")), "--batch", "--skip-column-names",
        f"--host={mysql['host']}", f"--port={mysql['port']}", f"--user={mysql['user']}",
        "--execute", sql,
    ]
    proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout).strip())
    non_default = int(proc.stdout.strip())
    if non_default:
        raise RuntimeError(f"worker has {non_default} non-default optimizer cost rows; refusing contaminated run")


def apply_calibration_start_costs(config: Mapping[str, Any]) -> None:
    """Apply the runner's explicit baseline (or seed) while calibrating it."""
    mysql = config["mysql"]
    adapter = MySQLCostAdapter(
        MySQLCLIClient(mysql),
        engine_name=str(mysql.get("engine_name", "default")),
        device_type=int(mysql.get("device_type", 0)),
    )
    cost_parameters = config["cost_parameters"]
    active = tuple(cost_parameters["active"])
    configured = cost_parameters.get("baseline_values") or cost_parameters.get("start_values") or {}
    adapter.apply({name: float(configured[name]) for name in active})


def reset_default_costs(config: Mapping[str, Any]) -> None:
    mysql = config["mysql"]
    env = os.environ.copy()
    env["MYSQL_PWD"] = str(mysql.get("password", ""))
    sql = (
        "UPDATE mysql.server_cost SET cost_value=NULL; "
        "UPDATE mysql.engine_cost SET cost_value=NULL; "
        "FLUSH OPTIMIZER_COSTS; "
        "SELECT (SELECT COUNT(*) FROM mysql.server_cost WHERE cost_value IS NOT NULL)+"
        "(SELECT COUNT(*) FROM mysql.engine_cost WHERE cost_value IS NOT NULL)"
    )
    command = [
        str(mysql.get("mysql_binary", "mysql")), "--batch", "--skip-column-names",
        f"--host={mysql['host']}", f"--port={mysql['port']}", f"--user={mysql['user']}",
        "--execute", sql,
    ]
    proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode:
        raise RuntimeError(f"optimizer-cost reset failed: {(proc.stderr or proc.stdout).strip()}")
    remaining = int(proc.stdout.strip().splitlines()[-1])
    if remaining:
        raise RuntimeError(f"optimizer-cost reset left {remaining} non-default rows")


def set_buffer_pool(
    config: Mapping[str, Any], size_bytes: int, *, timeout_sec: float = 180.0,
    poll_interval_sec: float = 1.0,
) -> None:
    """Request an asynchronous MySQL buffer-pool resize and wait for completion."""
    requested = int(size_bytes)
    if requested <= 0:
        raise ValueError("buffer-pool size must be positive")
    if timeout_sec <= 0 or poll_interval_sec <= 0:
        raise ValueError("buffer-pool polling timeout and interval must be positive")
    mysql = config["mysql"]
    env = os.environ.copy()
    env["MYSQL_PWD"] = str(mysql.get("password", ""))
    command_prefix = [
        str(mysql.get("mysql_binary", "mysql")), "--batch", "--skip-column-names",
        f"--host={mysql['host']}", f"--port={mysql['port']}", f"--user={mysql['user']}",
    ]

    def execute(sql: str, command_timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*command_prefix, "--execute", sql], env=env, capture_output=True, text=True,
            timeout=max(1.0, command_timeout), check=False,
        )

    proc = execute(f"SET GLOBAL innodb_buffer_pool_size={requested}", min(timeout_sec, 30.0))
    if proc.returncode:
        raise RuntimeError(f"buffer-pool setup failed: {(proc.stderr or proc.stdout).strip()}")

    deadline = time.monotonic() + timeout_sec
    actual: int | None = None
    resize_status = ""
    while True:
        remaining = deadline - time.monotonic()
        query = execute(
            "SELECT @@global.innodb_buffer_pool_size; "
            "SHOW STATUS LIKE 'Innodb_buffer_pool_resize_status'",
            min(max(remaining, 1.0), 30.0),
        )
        if query.returncode:
            raise RuntimeError(f"buffer-pool verification failed: {(query.stderr or query.stdout).strip()}")
        lines = query.stdout.strip().splitlines()
        actual = int(lines[0])
        resize_status = " ".join(lines[1:])
        if actual == requested:
            return
        if "fail" in resize_status.lower():
            raise RuntimeError(
                f"buffer-pool resize rejected: requested={requested}, actual={actual}, status={resize_status!r}"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval_sec, remaining))

    raise RuntimeError(
        f"buffer-pool resize timed out: requested={requested}, actual={actual}, status={resize_status!r}"
    )


def initial_buffer_pool_size(
    config: Mapping[str, Any], benchmark: str, workload_profile: str,
    xp_buffer_pool_mb: int,
) -> int:
    """Choose a stable pool size before calibration and optimization.

    Profiles with an explicit buffer-pool baseline must start at that baseline.
    Otherwise calibration would resize baseline -> setup -> baseline in quick
    succession, which can leave MySQL's asynchronous resize worker unable to
    accept the optimizer's immediately following resize request.
    """
    baseline = config.get("cost_parameters", {}).get("baseline_values", {})
    if "innodb_buffer_pool_size" in baseline:
        return int(round(float(baseline["innodb_buffer_pool_size"])))
    if workload_profile in {"stable", "stabilized", "replicated"} and benchmark == "hybench_xp":
        return int(xp_buffer_pool_mb) * 1024 * 1024
    return 1024 * 1024 * 1024


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=WORKSPACE / "config/mysql_experiment_manifest_A25_E25_B7.json")
    parser.add_argument("--workers-json", type=Path, default=WORKSPACE / "config/mysql_gd_workers_A25_20260718.json")
    parser.add_argument("--output-root", type=Path, default=Path("/disk2/resource_adaptive_mysql/gd_20260718/formal_dynamic"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--reuse-root", type=Path, action="append", default=[])
    parser.add_argument(
        "--seed-root", type=Path, action="append", default=[],
        help="Seed cost_parameters.start_values from matching formal result final_params",
    )
    parser.add_argument("--seed-mode", choices=("final", "top-qps"), default="final")
    parser.add_argument("--no-calibrate-bottlenecks", action="store_true")
    parser.add_argument("--calibration-repeats", type=int, default=1)
    parser.add_argument("--bottleneck-initial-util", type=float, default=0.94)
    parser.add_argument("--cpu-capability", type=float, default=None)
    parser.add_argument("--memory-capability", type=float, default=None)
    parser.add_argument("--io-capability", type=float, default=None)
    parser.add_argument("--workload-profile", choices=("original", "stable", "stabilized", "replicated"), default="original")
    parser.add_argument("--exclude-benchmark", action="append", choices=ALL_BENCHMARKS, default=[])
    parser.add_argument("--exclude-worker", action="append", default=[])
    parser.add_argument("--include-run", action="append", default=[], help="Only schedule these expanded run IDs")
    parser.add_argument(
        "--xp-buffer-pool-mb", type=int, default=1024,
        help="Buffer pool used by stable-profile HyBench XP runs (default: 1024 MiB)",
    )
    parser.add_argument(
        "--measurement-repeats", type=int, default=1,
        help="Real benchmark repeats per optimizer evaluation; medians are passed to gradient descent",
    )
    parser.add_argument("--job-query-names", default=None)
    parser.add_argument("--tpch-query-ids", default=None)
    parser.add_argument("--hybench-ap-clients", type=int, default=None)
    parser.add_argument("--hybench-tp-clients", type=int, default=None)
    parser.add_argument("--hybench-xap-clients", type=int, default=None)
    parser.add_argument("--hybench-xtp-clients", type=int, default=None)
    parser.add_argument("--hybench-ap-query-sequence", default=None)
    parser.add_argument("--hybench-tp-transaction-id", type=int, default=None)
    parser.add_argument("--hybench-tp-transaction-mix", default=None)
    parser.add_argument("--hybench-prewarm-sql", default=None)
    parser.add_argument("--job-concurrency", type=int, default=None)
    parser.add_argument("--job-prewarm-sql", default=None)
    parser.add_argument("--tpch-terminals", type=int, default=None)
    parser.add_argument("--tpch-prewarm-sql", default=None)
    parser.add_argument("--tpcc-terminals", type=int, default=None)
    parser.add_argument("--tpcc-weights", default=None)
    parser.add_argument("--optimizer-eta", type=float, default=None)
    parser.add_argument("--min-param-delta-rel-pct", type=float, default=None)
    parser.add_argument("--max-param-delta-rel-pct", type=float, default=None)
    parser.add_argument("--max-param-delta-ratio", type=float, default=None)
    parser.add_argument("--optimizer-max-iter", type=int, default=None)
    parser.add_argument(
        "--parameter-profile",
        choices=("default", "wide_io_cost", "wide_read_cost", "tpcc_flush", "spin_wait", "spin_wait_mid", "spin_wait_800", "spin_wait_min", "tp_cpu_hybrid_topk", "cpu_hybrid_800_topk", "cpu_hybrid_650_topk", "cpu_hybrid_775_topk", "cpu_hybrid_790_topk", "cpu_hybrid_785_topk", "cpu_hybrid_783_topk", "cpu_hybrid_780_topk", "cpu_hybrid_350_topk", "cpu_hybrid_280_topk", "cpu_hybrid_270_topk", "cpu_hybrid_250_topk", "memory_buffers", "memory_pool_topk", "memory_deep_topk", "xp_io_pool_topk", "xp_io_pool_mild_topk", "xp_io_pool_full_topk", "xp_io_pool_flush_topk", "xp_io_pool_cost_topk", "xp_io_q9_pool_topk", "xp_io_q9_pool264_topk", "xp_io_q9_pool272_topk", "xp_io_q9_pool272_cost5_topk", "xp_io_q9_pool272_cost10_topk", "xp_io_q9_pool272_cost10p25_topk", "xp_io_q9_pool272_cost11_topk", "xp_io_q9_pool272_cost12p5_topk", "xp_io_q9_pool272_cost15_topk", "xp_io_q9_pool272_cost17p5_topk", "xp_io_q9_pool272_cost20_topk", "xp_io_q9_pool272_cost25_topk", "xp_io_q9_pool272_cost50_topk", "xp_io_q9_pool272_cost75_topk", "xp_io_q9_pool280_topk", "xp_io_q9_pool288_topk", "xp_io_q9_pool320_topk", "xp_io_q9_pool336_topk", "xp_io_q9_pool352_topk", "xp_io_q9_pool360_topk", "xp_io_q9_pool362_topk", "xp_io_q9_pool368_topk", "ap_memory_topk", "ap_q11_plan_topk", "ap_q9_reverse_topk", "ap_q9_smallpool_topk", "ap_q9_heavy_release_topk", "ap_q9_128m_release_topk", "ap_q9_pool960_prewarm_topk", "ap_q9_pool896_prewarm_topk", "ap_q9_pool864_prewarm_topk", "ap_q9_pool2g_prewarm_topk", "ap_q9_mild_release_topk", "ap_q9_midbuffers_topk", "ap_q11_session_release_topk", "ap_screen_topk", "memory_pool_tradeoff_topk", "memory_pool_valid_topk", "memory_runtime_release_topk", "memory_runtime_release_heavy_topk", "memory_runtime_release_128m_topk", "memory_pool_prewarm_mild_topk", "memory_pool_prewarm_qps_topk", "memory_prewarm_buffers_128m_topk", "job_memory_topk", "job11d_memory_valid_topk", "tpch_memory_topk"), default="default",
    )
    args = parser.parse_args()
    if args.xp_buffer_pool_mb <= 0:
        parser.error("--xp-buffer-pool-mb must be positive")
    if args.measurement_repeats <= 0:
        parser.error("--measurement-repeats must be positive")
    if args.bottleneck_initial_util <= 0:
        parser.error("--bottleneck-initial-util must be positive")
    if args.hybench_ap_clients is not None and args.hybench_ap_clients <= 0:
        parser.error("--hybench-ap-clients must be positive")
    if args.hybench_tp_clients is not None and args.hybench_tp_clients <= 0:
        parser.error("--hybench-tp-clients must be positive")
    if args.hybench_xap_clients is not None and args.hybench_xap_clients <= 0:
        parser.error("--hybench-xap-clients must be positive")
    if args.hybench_xtp_clients is not None and args.hybench_xtp_clients <= 0:
        parser.error("--hybench-xtp-clients must be positive")

    manifest = load_manifest(args.manifest)
    all_runs = expand_runs(manifest)
    excluded_benchmarks = set(args.exclude_benchmark)
    included_run_ids = set(args.include_run)
    known_run_ids = {run.run_id for run in all_runs}
    unknown_run_ids = sorted(included_run_ids - known_run_ids)
    if unknown_run_ids:
        raise ValueError(f"unknown --include-run IDs: {unknown_run_ids}")
    runs = [
        run for run in all_runs
        if run.benchmark not in excluded_benchmarks and (not included_run_ids or run.run_id in included_run_ids)
    ]
    if not runs:
        raise ValueError("benchmark exclusion removed every run")
    excluded_workers = set(args.exclude_worker)
    workers = [worker for worker in load_workers(args.workers_json) if worker.worker_id not in excluded_workers]
    if not workers:
        raise ValueError("worker exclusion removed every worker")
    eligible = {run.run_id: [worker.worker_id for worker in workers if run.benchmark in worker.benchmarks] for run in runs}
    missing_eligibility = [run_id for run_id, worker_ids in eligible.items() if not worker_ids]
    if missing_eligibility:
        raise ValueError(f"runs have no compatible worker: {missing_eligibility}")
    max_parallel = min(int(manifest["max_parallel"]), len(workers))
    active_workers = workers[:max_parallel]
    if any(not any(run.benchmark in worker.benchmarks for worker in active_workers) for run in runs):
        raise ValueError("max_parallel worker prefix does not cover every benchmark")

    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "logs").mkdir(parents=True, exist_ok=True)
    (args.output_root / "configs").mkdir(parents=True, exist_ok=True)
    status_lock = threading.Lock()
    pending_lock = threading.Lock()
    pending: List[LogicalRun] = list(runs)
    status: Dict[str, Any] = {
        "started_at": datetime.now().isoformat(), "policy": "global_oldest_compatible_task_first_work_stealing",
        "manifest_total_runs": len(all_runs), "selected_total_runs": len(runs), "max_parallel": max_parallel,
        "included_runs": sorted(included_run_ids),
        "seed_roots": [str(path) for path in args.seed_root],
        "excluded_benchmarks": sorted(excluded_benchmarks), "excluded_workers": sorted(excluded_workers),
        "workers": [worker.as_dict() for worker in active_workers], "eligible_workers": eligible, "runs": {},
    }
    status_path = args.output_root / "matrix_status.json"

    def persist() -> None:
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    reuse_roots = ([args.output_root] if args.resume else []) + list(args.reuse_root)
    if reuse_roots:
        remaining = []
        for run in pending:
            match = None
            for root in reuse_roots:
                source = root / f"{run.run_id}.json"
                try:
                    payload = json.loads(source.read_text(encoding="utf-8"))
                    evaluation = evaluate_acceptance(payload, manifest["acceptance"])
                except (OSError, json.JSONDecodeError):
                    continue
                if evaluation["passed"]:
                    match = (source, evaluation)
                    break
            if match is None:
                remaining.append(run)
                continue
            source, evaluation = match
            output = args.output_root / f"{run.run_id}.json"
            if source.resolve() != output.resolve():
                shutil.copy2(source, output)
            status["runs"][run.run_id] = {
                "run_id": run.run_id, "status": "reused", "evaluation": evaluation,
                "source": str(source), "output": str(output),
            }
        pending = remaining

    def claim(worker: Worker) -> LogicalRun | None:
        with pending_lock:
            return claim_compatible(pending, worker)

    def run_worker_locked(
        worker: Worker, *, initial_run: LogicalRun | None = None,
        max_runs: int | None = None,
    ) -> Dict[str, Any]:
        """Execute claimed work while the caller owns this worker's lock.

        ``run_worker`` passes exactly one pre-claimed run so that the
        cross-process worker lock is released between logical runs.  Keeping
        the optional loop here preserves a small, directly callable unit for
        tests and local callers without changing experiment execution.
        """
        claimed: List[str] = []
        next_run = initial_run
        while True:
            run = next_run if next_run is not None else claim(worker)
            next_run = None
            if run is None:
                return {"worker_id": worker.worker_id, "claimed_runs": claimed}
            claimed.append(run.run_id)
            output = args.output_root / f"{run.run_id}.json"
            log = args.output_root / "logs" / f"{run.run_id}.log"
            config_path = args.output_root / "configs" / f"{run.run_id}.json"
            started = datetime.now().isoformat()
            config: Dict[str, Any] | None = None
            item: Dict[str, Any] | None = None
            outer_adapter: MySQLCostAdapter | None = None
            outer_snapshot: list[Any] | None = None
            with status_lock:
                status["runs"][run.run_id] = {"run_id": run.run_id, "status": "running", "worker_id": worker.worker_id, "started_at": started}
                persist()
            try:
                pid_map = {name: worker.mysqld_pid for name in ALL_BENCHMARKS}
                config = bind_config(build_config(run, pid_map), run, worker, args.output_root)
                apply_capability_overrides(
                    config, cpu=args.cpu_capability,
                    memory=args.memory_capability, io=args.io_capability,
                )
                apply_workload_profile(config, run.benchmark, args.workload_profile)
                apply_hybench_client_overrides(
                    config, run.benchmark,
                    ap_clients=args.hybench_ap_clients, tp_clients=args.hybench_tp_clients,
                    xap_clients=args.hybench_xap_clients, xtp_clients=args.hybench_xtp_clients,
                )
                apply_hybench_query_override(
                    config, run.benchmark, args.hybench_ap_query_sequence,
                    tp_transaction_id=args.hybench_tp_transaction_id,
                    tp_transaction_mix=args.hybench_tp_transaction_mix,
                    prewarm_sql=args.hybench_prewarm_sql,
                )
                apply_non_hybench_load_overrides(
                    config, run.benchmark, job_concurrency=args.job_concurrency,
                    job_prewarm_sql=args.job_prewarm_sql,
                    tpch_terminals=args.tpch_terminals,
                    tpch_prewarm_sql=args.tpch_prewarm_sql,
                    tpcc_terminals=args.tpcc_terminals, tpcc_weights=args.tpcc_weights,
                )
                apply_parameter_profile(config, args.parameter_profile)
                apply_optimizer_step_overrides(
                    config, eta=args.optimizer_eta,
                    min_delta_pct=args.min_param_delta_rel_pct,
                    max_delta_pct=args.max_param_delta_rel_pct,
                    max_delta_ratio=args.max_param_delta_ratio,
                    max_iter=args.optimizer_max_iter,
                )
                if args.job_query_names and run.benchmark == "job":
                    _replace_flag(config["estimator"]["command"], "--query-names", args.job_query_names, required=True)
                if args.tpch_query_ids and run.benchmark == "tpch":
                    _replace_flag(config["estimator"]["command"], "--query-ids", args.tpch_query_ids, required=True)
                config["estimator"]["measurement_repeats"] = args.measurement_repeats
                config["estimator"]["measurement_min_success"] = args.measurement_repeats // 2 + 1
                for seed_root in args.seed_root:
                    seed_path = seed_root / f"{run.run_id}.json"
                    try:
                        seed_document = json.loads(seed_path.read_text(encoding="utf-8"))
                        if args.seed_mode == "top-qps":
                            seed_row = max(seed_document["outer_loop"][1:], key=lambda row: float(row["qps"]))
                            seed_params = seed_row["params"]
                        else:
                            seed_params = seed_document["result"]["final_params"]
                    except (OSError, json.JSONDecodeError, KeyError, TypeError):
                        continue
                    active = set(config["cost_parameters"]["active"])
                    if not isinstance(seed_params, Mapping) or not active <= set(seed_params):
                        continue
                    config["cost_parameters"]["start_values"] = {
                        name: float(seed_params[name]) for name in config["cost_parameters"]["active"]
                    }
                    config["dispatch"]["seed_start_values_from"] = str(seed_path)
                    config["dispatch"]["seed_mode"] = args.seed_mode
                    break
                desired_buffer_pool = initial_buffer_pool_size(
                    config, run.benchmark, args.workload_profile,
                    args.xp_buffer_pool_mb,
                )
                set_buffer_pool(config, desired_buffer_pool)
                assert_default_costs(config)
                outer_adapter = MySQLCostAdapter(MySQLCLIClient(config["mysql"]))
                outer_snapshot = outer_adapter.snapshot()
                calibration = {"performed": False, "reason": "disabled"}
                if not args.no_calibrate_bottlenecks:
                    explicit_cost_calibration = (
                        run.bottleneck != "none"
                        and (
                            bool(config["cost_parameters"].get("baseline_values"))
                            or "seed_start_values_from" in config["dispatch"]
                        )
                    )
                    if explicit_cost_calibration:
                        apply_calibration_start_costs(config)
                    try:
                        calibration = calibrate_bottleneck(
                            config, run.bottleneck,
                            initial_util=args.bottleneck_initial_util,
                            repeats=args.calibration_repeats,
                        )
                    finally:
                        if explicit_cost_calibration:
                            if outer_adapter is not None and outer_snapshot is not None:
                                outer_adapter.restore(outer_snapshot)
                            else:
                                reset_default_costs(config)
                    if explicit_cost_calibration:
                        baseline_values = config["cost_parameters"].get("baseline_values") or {}
                        calibration["explicit_cost_values_applied"] = True
                        calibration["explicit_cost_values_source"] = (
                            "baseline_values" if baseline_values else "seed_start_values"
                        )
                        calibration["explicit_cost_values"] = dict(
                            baseline_values or config["cost_parameters"]["start_values"]
                        )
                        if not baseline_values:
                            calibration["seed_start_values_applied"] = True
                            calibration["seed_start_values"] = dict(
                                config["cost_parameters"]["start_values"]
                            )
                config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                command = [
                    sys.executable, str(WORKSPACE / "mysql_experiment_runner.py"), "--config", str(config_path),
                    "--exp-id", run.exp_id, "--benchmark", run.benchmark, "--bottleneck", run.bottleneck,
                    "--repeat-id", str(run.repeat_id), "--worker-id", worker.worker_id, "--output", str(output),
                ]
                with log.open("w", encoding="utf-8") as handle:
                    proc = subprocess.run(command, cwd=WORKSPACE, stdout=handle, stderr=subprocess.STDOUT, text=True, check=False)
                if proc.returncode:
                    try:
                        log_tail = log.read_text(encoding="utf-8", errors="replace")[-12000:]
                    except OSError:
                        log_tail = ""
                    detail = f"experiment runner exited {proc.returncode}"
                    if is_worker_unavailable_error(log_tail):
                        raise WorkerUnavailableError(detail)
                    raise RuntimeError(detail)
                payload = json.loads(output.read_text(encoding="utf-8"))
                evaluation = evaluate_acceptance(payload, manifest["acceptance"])
                item = {
                    "run_id": run.run_id, "status": "completed", "worker_id": worker.worker_id,
                    "started_at": started, "finished_at": datetime.now().isoformat(),
                    "calibration": calibration, "evaluation": evaluation,
                    "output": str(output), "config": str(config_path), "log": str(log),
                }
            except Exception as exc:
                item = {
                    "run_id": run.run_id, "status": "failed", "worker_id": worker.worker_id,
                    "started_at": started, "finished_at": datetime.now().isoformat(),
                    "error": str(exc), "config": str(config_path), "log": str(log),
                }
                if isinstance(exc, WorkerUnavailableError) or is_worker_unavailable_error(str(exc)):
                    item["failure_class"] = "worker_unavailable"
            finally:
                try:
                    if outer_adapter is not None and outer_snapshot is not None:
                        outer_adapter.restore(outer_snapshot)
                except Exception as restore_exc:
                    if item is not None:
                        item["outer_mysql_snapshot_restore_error"] = str(restore_exc)
                try:
                    if config is not None and run.benchmark == "hybench_xp":
                        set_buffer_pool(config, 1024 * 1024 * 1024)
                except Exception as restore_exc:
                    if item is not None:
                        item["buffer_pool_restore_error"] = str(restore_exc)
                try:
                    # A failed default-cost preflight means another scheduler may
                    # own this worker.  Never reset its live parameters.  Once an
                    # outer snapshot exists, this scheduler has passed preflight
                    # and owns the cleanup responsibility.
                    if config is not None and outer_snapshot is not None:
                        reset_default_costs(config)
                except Exception as reset_exc:
                    if item is not None:
                        item["optimizer_cost_reset_error"] = str(reset_exc)
            if item is None:
                raise AssertionError("worker did not produce a run status item")
            with status_lock:
                status["runs"][run.run_id] = item
                persist()
            print(json.dumps(item, ensure_ascii=False, sort_keys=True), flush=True)
            if max_runs is not None and len(claimed) >= max_runs:
                unavailable = item.get("failure_class") == "worker_unavailable"
                return {
                    "worker_id": worker.worker_id, "claimed_runs": claimed,
                    "stop_worker": unavailable,
                    "stop_reason": "worker_unavailable" if unavailable else None,
                }

    def run_worker(worker: Worker) -> Dict[str, Any]:
        """Claim work dynamically, releasing the machine after every task."""
        return dispatch_worker_tasks(
            worker, claim,
            lambda locked_worker, run: run_worker_locked(
                locked_worker, initial_run=run, max_runs=1,
            ),
        )

    with status_lock:
        persist()
    threads = [threading.Thread(target=run_worker, args=(worker,), name=worker.worker_id) for worker in active_workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    status["finished_at"] = datetime.now().isoformat()
    status["unclaimed_runs"] = [run.run_id for run in pending]
    with status_lock:
        persist()
    passed = sum(1 for item in status["runs"].values() if item.get("evaluation", {}).get("passed"))
    failed = sum(1 for item in status["runs"].values() if item.get("status") == "failed")
    print(json.dumps({"total": len(runs), "passed": passed, "failed": failed, "status": str(status_path.resolve())}, sort_keys=True))
    return 1 if failed or pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
