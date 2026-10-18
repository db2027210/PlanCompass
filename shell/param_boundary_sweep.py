#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from logger_utils import OptimizationLogger
from system_resource_monitor import (
    _cgroup_snapshot,
    _find_cgroup_paths,
    _read_vm_runtime_snapshot,
    _stats,
    parse_hybench_benchmark_output,
)


@dataclass
class PgConfig:
    """PostgreSQL connection config."""

    host: str
    port: str
    user: str
    dbname: str
    connect_timeout_s: int


@dataclass
class ParamSpec:
    """Parameter sweep values."""

    low: float
    mid: float
    high: float


@dataclass
class DbSnapshot:
    """DbSnapshot."""

    timestamp: float
    xact_commit: int
    xact_rollback: int
    tup_returned: int
    tup_fetched: int
    tup_inserted: int
    tup_updated: int
    tup_deleted: int
    blks_read: int
    blks_hit: int
    statements_calls: Optional[int]
    active_sessions: int

    @property
    def xact_total(self) -> int:
        return self.xact_commit + self.xact_rollback


PARAM_SPECS: Dict[str, ParamSpec] = {
    "seq_page_cost": ParamSpec(low=0.0001, mid=0.5, high=100.0),
    "random_page_cost": ParamSpec(low=0.0001, mid=0.5, high=100.0),
    "cpu_tuple_cost": ParamSpec(low=0.000001, mid=0.005, high=1.0),
    "cpu_index_tuple_cost": ParamSpec(low=0.000001, mid=0.004, high=1.0),
    "cpu_operator_cost": ParamSpec(low=0.000001, mid=0.002, high=1.0),
    "parallel_setup_cost": ParamSpec(low=0.0, mid=100.0, high=100000.0),
    "parallel_tuple_cost": ParamSpec(low=0.000001, mid=0.05, high=10.0),
}


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _psql_query(pg: PgConfig, sql: str) -> str:
    env = os.environ.copy()
    env["PGCONNECT_TIMEOUT"] = str(pg.connect_timeout_s)
    cmd = [
        "psql",
        "-h",
        pg.host,
        "-p",
        pg.port,
        "-U",
        pg.user,
        "-d",
        pg.dbname,
        "-v",
        "ON_ERROR_STOP=1",
        "-At",
        "-F",
        "\t",
        "-c",
        sql,
    ]
    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"psql query failed: {detail}")
    return (proc.stdout or "").strip()


def _has_pg_stat_statements(pg: PgConfig) -> bool:
    sql = """
        SELECT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'pg_stat_statements'
        );
    """
    return _psql_query(pg, sql).strip().lower() in {"t", "true", "1"}


def _fetch_snapshot(pg: PgConfig, include_statements: bool) -> DbSnapshot:
    statements_expr = """
        (
            SELECT COALESCE(SUM(calls), 0)::bigint
            FROM pg_stat_statements
            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
        ) AS statements_calls
    """ if include_statements else "NULL::bigint AS statements_calls"

    sql = f"""
        SELECT
            EXTRACT(EPOCH FROM clock_timestamp())::float8 AS ts,
            d.xact_commit,
            d.xact_rollback,
            d.tup_returned,
            d.tup_fetched,
            d.tup_inserted,
            d.tup_updated,
            d.tup_deleted,
            d.blks_read,
            d.blks_hit,
            {statements_expr},
            (
                SELECT COUNT(*)::bigint
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND state = 'active'
                  AND pid <> pg_backend_pid()
            ) AS active_sessions
        FROM pg_stat_database d
        WHERE d.datname = current_database();
    """
    raw = _psql_query(pg, sql)
    parts = raw.split("\t")
    if len(parts) != 12:
        raise RuntimeError(f"unexpected snapshot raw={raw!r}")
    return DbSnapshot(
        timestamp=float(parts[0]),
        xact_commit=int(parts[1]),
        xact_rollback=int(parts[2]),
        tup_returned=int(parts[3]),
        tup_fetched=int(parts[4]),
        tup_inserted=int(parts[5]),
        tup_updated=int(parts[6]),
        tup_deleted=int(parts[7]),
        blks_read=int(parts[8]),
        blks_hit=int(parts[9]),
        statements_calls=int(parts[10]) if parts[10] else None,
        active_sessions=int(parts[11]),
    )


def _rate(new_value: Optional[int], old_value: Optional[int], elapsed: float) -> Optional[float]:
    if new_value is None or old_value is None or elapsed <= 0:
        return None
    return max(0.0, float(new_value - old_value) / elapsed)


def _db_row(prev: DbSnapshot, cur: DbSnapshot) -> Dict[str, Any]:
    elapsed = max(0.000001, cur.timestamp - prev.timestamp)
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cur.timestamp)),
        "elapsed_s": round(elapsed, 3),
        "qps_statements": _rate(cur.statements_calls, prev.statements_calls, elapsed),
        "xact_tps": _rate(cur.xact_total, prev.xact_total, elapsed),
        "commit_tps": _rate(cur.xact_commit, prev.xact_commit, elapsed),
        "rollback_tps": _rate(cur.xact_rollback, prev.xact_rollback, elapsed),
        "tuple_read_ps": _rate(
            cur.tup_returned + cur.tup_fetched,
            prev.tup_returned + prev.tup_fetched,
            elapsed,
        ),
        "tuple_write_ps": _rate(
            cur.tup_inserted + cur.tup_updated + cur.tup_deleted,
            prev.tup_inserted + prev.tup_updated + prev.tup_deleted,
            elapsed,
        ),
        "block_read_ps": _rate(cur.blks_read, prev.blks_read, elapsed),
        "block_hit_ps": _rate(cur.blks_hit, prev.blks_hit, elapsed),
        "active_sessions": cur.active_sessions,
    }


def _summarize_db_rows(rows: List[Dict[str, Any]], csv_path: str, include_statements: bool) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "csv_path": csv_path,
        "sample_count": len(rows),
        "pg_stat_statements": include_statements,
    }
    key_map = {
        "db_qps": "qps_statements",
        "db_xact_tps": "xact_tps",
        "db_commit_tps": "commit_tps",
        "db_rollback_tps": "rollback_tps",
        "db_active_sessions": "active_sessions",
        "db_tuple_read_ps": "tuple_read_ps",
        "db_tuple_write_ps": "tuple_write_ps",
        "db_block_read_ps": "block_read_ps",
        "db_block_hit_ps": "block_hit_ps",
    }
    for out_key, row_key in key_map.items():
        values = [
            float(item[row_key])
            for item in rows
            if item.get(row_key) is not None
        ]
        summary[out_key] = (sum(values) / len(values)) if values else None
    return summary


def _read_postmaster_start_time(pg: PgConfig) -> Optional[str]:
    try:
        value = _psql_query(pg, "SELECT pg_postmaster_start_time();")
    except Exception:
        return None
    return value or None


def _restart_db(
    pg: PgConfig,
    cfg: Dict[str, Any],
    logger: OptimizationLogger,
    *,
    tag: str,
) -> None:
    cleanup_cfg = cfg.get("post_benchmark_cleanup") or {}
    metrics_cfg = cfg.get("metrics") or {}
    metrics_source = str(metrics_cfg.get("source", "vm-runtime")).strip().lower()
    timeout_s = float(cleanup_cfg.get("restart_wait_timeout_sec", 600))
    poll_s = float(cleanup_cfg.get("restart_poll_interval_sec", 2))
    ready_sql = str(cleanup_cfg.get("ready_sql", "SELECT 1;"))
    pre_restart = _read_postmaster_start_time(pg)

    if metrics_source == "vm-runtime":
        vm_cfg = metrics_cfg.get("vm_runtime") or {}
        ssh_target = str(cleanup_cfg.get("vm_restart_ssh_target") or vm_cfg.get("ssh_target") or vm_cfg.get("host") or "").strip()
        ssh_port = int(cleanup_cfg.get("vm_restart_ssh_port", vm_cfg.get("ssh_port", 22)))
        identity_file = str(cleanup_cfg.get("vm_restart_ssh_identity_file", vm_cfg.get("ssh_identity_file", ""))).strip()
        restart_cmd = str(cleanup_cfg.get("vm_restart_cmd") or "").strip()
        if not ssh_target or not restart_cmd:
            raise RuntimeError("vm-runtime restart config is incomplete")
        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={max(1, int((vm_cfg.get('connect_timeout_sec', 5))))}",
            "-p",
            str(ssh_port),
        ]
        if identity_file:
            cmd.extend(["-i", identity_file])
        cmd.extend([ssh_target, f"bash -lc {shlex.quote(restart_cmd)}"])
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"vm restart failed: {(proc.stderr or proc.stdout).strip()}")
    else:
        container = str(cleanup_cfg.get("db_container") or "").strip()
        pg_ctl_path = str(cleanup_cfg.get("pg_ctl_path", "/usr/lib/postgresql/17/bin/pg_ctl"))
        pg_data_dir = str(cleanup_cfg.get("pg_data_dir", "/data/pg_data"))
        if not container:
            raise RuntimeError("docker restart config is incomplete: db_container is empty")
        stop_cmd = f'su - postgres -c "{pg_ctl_path} -D {pg_data_dir} -m fast stop"'
        start_cmd = f'su - postgres -c "{pg_ctl_path} -D {pg_data_dir} start"'
        stop_proc = subprocess.run(
            ["docker", "exec", container, "bash", "-lc", stop_cmd],
            check=False,
            capture_output=True,
            text=True,
        )
        if stop_proc.returncode != 0:
            detail = f"{stop_proc.stdout}\n{stop_proc.stderr}".lower()
            if "no server running" not in detail and "is not running" not in detail:
                raise RuntimeError(f"docker stop failed: {(stop_proc.stderr or stop_proc.stdout).strip()}")
        start_proc = subprocess.run(
            ["docker", "exec", container, "bash", "-lc", start_cmd],
            check=False,
            capture_output=True,
            text=True,
        )
        if start_proc.returncode != 0:
            detail = f"{start_proc.stdout}\n{start_proc.stderr}".lower()
            if "already running" not in detail and "server did not start in time" not in detail:
                raise RuntimeError(f"docker start failed: {(start_proc.stderr or start_proc.stdout).strip()}")

    deadline = time.time() + timeout_s
    last_error = ""
    while time.time() < deadline:
        try:
            _psql_query(pg, ready_sql)
            post_restart = _read_postmaster_start_time(pg)
            logger.log_struct(
                {
                    "event": "restart_db",
                    "tag": tag,
                    "success": True,
                    "pre_restart_postmaster_start_time": pre_restart,
                    "post_restart_postmaster_start_time": post_restart,
                    "restart_confirmed": bool(pre_restart and post_restart and pre_restart != post_restart),
                }
            )
            time.sleep(5)
            return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(poll_s)
    raise RuntimeError(f"database readiness check timeout after restart: {last_error}")


def _apply_params(pg: PgConfig, params: Dict[str, float]) -> None:
    _psql_query(pg, "SELECT 1;")
    for name, value in params.items():
        _psql_query(pg, f"ALTER SYSTEM SET {name} = '{value}';")
    _psql_query(pg, "SELECT pg_reload_conf();")


def _read_effective_params(pg: PgConfig, names: List[str]) -> Dict[str, str]:
    quoted = ", ".join(f"'{name}'" for name in names)
    sql = f"""
        SELECT name, setting, COALESCE(unit, '')
        FROM pg_settings
        WHERE name IN ({quoted})
        ORDER BY name;
    """
    output = _psql_query(pg, sql)
    result: Dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        result[parts[0]] = f"{parts[1]}{parts[2]}".strip()
    return result


def _reset_pg_stats(pg: PgConfig, include_statements: bool) -> None:
    try:
        _psql_query(pg, "SELECT pg_stat_reset();")
    except Exception:
        pass
    if include_statements:
        try:
            _psql_query(pg, "SELECT pg_stat_statements_reset();")
        except Exception:
            pass


def _db_sampler(
    pg: PgConfig,
    include_statements: bool,
    interval_s: float,
    stop_event: threading.Event,
    rows: List[Dict[str, Any]],
    errors: List[str],
) -> None:
    try:
        prev = _fetch_snapshot(pg, include_statements)
    except Exception as exc:
        errors.append(str(exc))
        return
    while not stop_event.wait(interval_s):
        try:
            cur = _fetch_snapshot(pg, include_statements)
            rows.append(_db_row(prev, cur))
            prev = cur
        except Exception as exc:
            if len(errors) < 5:
                errors.append(str(exc))


def _runtime_sampler_vm(
    vm_cfg: Dict[str, Any],
    interval_s: float,
    warmup_s: float,
    stop_event: threading.Event,
    samples_cpu: List[float],
    samples_mem: List[float],
    samples_io_read: List[float],
    samples_io_write: List[float],
    runtime_meta: Dict[str, Any],
    errors: List[str],
) -> None:
    start_ts = time.time()
    prev_snap: Optional[Dict[str, float]] = None
    prev_ts: Optional[float] = None
    while not stop_event.is_set():
        try:
            now = time.time()
            snap = _read_vm_runtime_snapshot(
                host=str(vm_cfg.get("host", "")),
                ssh_target=str(vm_cfg.get("ssh_target", "")),
                ssh_user=str(vm_cfg.get("ssh_user", "")),
                ssh_port=int(vm_cfg.get("ssh_port", 22)),
                identity_file=str(vm_cfg.get("ssh_identity_file", "")),
                connect_timeout_s=float(vm_cfg.get("connect_timeout_sec", 5)),
                disk_devices=vm_cfg.get("disk_devices") or [],
            )
            stable = (now - start_ts) >= max(0.0, warmup_s)
            pg_pid_count = float(snap.get("pg_pid_count", 0.0) or 0.0)
            pg_pss_pid_count = float(snap.get("pg_pss_pid_count", 0.0) or 0.0)
            memory_mode = str(vm_cfg.get("memory_mode", "pg_proc_pss")).strip().lower()
            if memory_mode == "pg_proc_pss" and pg_pss_pid_count > 0:
                mem_used_bytes = max(0.0, float(snap.get("pg_pss_kb", 0.0) or 0.0) * 1024.0)
                runtime_meta["memory_source"] = "vm_pg_proc_pss"
            elif memory_mode in ("pg_proc_pss", "pg_proc_rss") and pg_pid_count > 0:
                mem_used_bytes = max(0.0, float(snap.get("pg_rss_kb", 0.0) or 0.0) * 1024.0)
                runtime_meta["memory_source"] = "vm_pg_proc_rss"
            else:
                mem_used_bytes = max(
                    0.0,
                    (float(snap.get("mem_total_kb", 0.0)) - float(snap.get("mem_available_kb", 0.0))) * 1024.0,
                )
                runtime_meta["memory_source"] = "vm_meminfo_used"
            runtime_meta["memory_pg_pid_count"] = pg_pid_count
            if stable:
                samples_mem.append(mem_used_bytes)
            if prev_snap is not None and prev_ts is not None:
                dt = max(1e-6, now - prev_ts)
                cpu_total_delta = max(0.0, snap.get("cpu_total", 0.0) - prev_snap.get("cpu_total", 0.0))
                cpu_idle_delta = max(0.0, snap.get("cpu_idle", 0.0) - prev_snap.get("cpu_idle", 0.0))
                cpu_ratio = 0.0
                if cpu_total_delta > 0:
                    cpu_ratio = max(0.0, min(1.0, 1.0 - (cpu_idle_delta / cpu_total_delta)))
                cpu_count = max(1.0, snap.get("cpu_count", prev_snap.get("cpu_count", 1.0)))
                io_read_rate = max(
                    0.0,
                    (snap.get("io_read_sectors", 0.0) - prev_snap.get("io_read_sectors", 0.0)) * 512.0 / dt,
                )
                io_write_rate = max(
                    0.0,
                    (snap.get("io_write_sectors", 0.0) - prev_snap.get("io_write_sectors", 0.0)) * 512.0 / dt,
                )
                if stable:
                    samples_cpu.append(cpu_ratio * cpu_count)
                    samples_io_read.append(io_read_rate)
                    samples_io_write.append(io_write_rate)
            prev_snap = snap
            prev_ts = now
        except Exception as exc:
            if len(errors) < 5:
                errors.append(str(exc))
        stop_event.wait(max(0.1, interval_s))


def _runtime_sampler_docker(
    docker_cfg: Dict[str, Any],
    interval_s: float,
    warmup_s: float,
    stop_event: threading.Event,
    samples_cpu: List[float],
    samples_mem: List[float],
    samples_io_read: List[float],
    samples_io_write: List[float],
    runtime_meta: Dict[str, Any],
    errors: List[str],
) -> None:
    container_name = str(docker_cfg.get("container", "")).strip()
    cgroup_paths = _find_cgroup_paths(container_name)
    start_ts = time.time()
    prev_snap: Optional[Dict[str, float]] = None
    prev_ts: Optional[float] = None
    memory_mode = str(docker_cfg.get("memory_mode", "pss")).strip().lower()
    while not stop_event.is_set():
        try:
            now = time.time()
            snap = _cgroup_snapshot(
                cgroup_paths,
                container_name=container_name,
                memory_mode="pss" if memory_mode == "pss" else "usage",
            )
            stable = (now - start_ts) >= max(0.0, warmup_s)
            runtime_meta["memory_source"] = str(snap.get("memory_source", "cgroup_usage"))
            runtime_meta["memory_pg_pid_count"] = float(snap.get("memory_pid_count", 0.0) or 0.0)
            runtime_meta["io_source"] = str(snap.get("io_source", "unknown"))
            if stable:
                samples_mem.append(float(snap.get("memory_bytes", 0.0) or 0.0))
            if prev_snap is not None and prev_ts is not None:
                dt = max(1e-6, now - prev_ts)
                cpu_delta_ns = max(0.0, snap["cpu_ns"] - prev_snap["cpu_ns"])
                io_read_rate = max(0.0, snap["io_read_bytes"] - prev_snap["io_read_bytes"]) / dt
                io_write_rate = max(0.0, snap["io_write_bytes"] - prev_snap["io_write_bytes"]) / dt
                if stable:
                    samples_cpu.append(cpu_delta_ns / (dt * 1e9))
                    samples_io_read.append(io_read_rate)
                    samples_io_write.append(io_write_rate)
            prev_snap = snap
            prev_ts = now
        except Exception as exc:
            if len(errors) < 5:
                errors.append(str(exc))
        stop_event.wait(max(0.1, interval_s))


def _run_case_workload(
    workload_cmd: str,
    pg: PgConfig,
    cfg: Dict[str, Any],
    db_monitor_csv: str,
    *,
    case_label: str,
    include_statements: bool,
) -> Dict[str, Any]:
    metrics_cfg = cfg.get("metrics") or {}
    metrics_source = str(metrics_cfg.get("source", "vm-runtime")).strip().lower()
    db_rows: List[Dict[str, Any]] = []
    db_errors: List[str] = []
    runtime_errors: List[str] = []
    runtime_meta: Dict[str, Any] = {"io_source": "unknown", "memory_source": "unknown", "memory_pg_pid_count": 0.0}
    stop_event = threading.Event()

    samples_cpu: List[float] = []
    samples_mem: List[float] = []
    samples_io_read: List[float] = []
    samples_io_write: List[float] = []

    benchmark_cfg = cfg.get("benchmark") or {}
    vm_cfg = metrics_cfg.get("vm_runtime") or {}
    docker_cfg = metrics_cfg.get("docker_runtime") or {}
    interval_s = float(vm_cfg.get("interval_sec", 2))
    warmup_s = float(vm_cfg.get("warmup_sec", 0))
    if metrics_source == "docker-runtime":
        interval_s = float(docker_cfg.get("interval_sec", 2))
        warmup_s = float(docker_cfg.get("warmup_sec", 0))
    if not _to_bool(benchmark_cfg.get("warmup_enabled", False), False):
        warmup_s = 0.0

    db_thread = threading.Thread(
        target=_db_sampler,
        kwargs={
            "pg": pg,
            "include_statements": include_statements,
            "interval_s": 1.0,
            "stop_event": stop_event,
            "rows": db_rows,
            "errors": db_errors,
        },
        daemon=True,
    )
    db_thread.start()

    if metrics_source == "vm-runtime":
        runtime_thread = threading.Thread(
            target=_runtime_sampler_vm,
            kwargs={
                "vm_cfg": metrics_cfg.get("vm_runtime") or {},
                "interval_s": interval_s,
                "warmup_s": warmup_s,
                "stop_event": stop_event,
                "samples_cpu": samples_cpu,
                "samples_mem": samples_mem,
                "samples_io_read": samples_io_read,
                "samples_io_write": samples_io_write,
                "runtime_meta": runtime_meta,
                "errors": runtime_errors,
            },
            daemon=True,
        )
    else:
        runtime_thread = threading.Thread(
            target=_runtime_sampler_docker,
            kwargs={
                "docker_cfg": metrics_cfg.get("docker_runtime") or {},
                "interval_s": interval_s,
                "warmup_s": warmup_s,
                "stop_event": stop_event,
                "samples_cpu": samples_cpu,
                "samples_mem": samples_mem,
                "samples_io_read": samples_io_read,
                "samples_io_write": samples_io_write,
                "runtime_meta": runtime_meta,
                "errors": runtime_errors,
            },
            daemon=True,
        )
    runtime_thread.start()

    proc = subprocess.Popen(
        workload_cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    workload_output_lines: List[str] = []
    start_ts = time.time()

    import fcntl

    fd = proc.stdout.fileno()
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    try:
        while True:
            try:
                while True:
                    raw = proc.stdout.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\n")
                    workload_output_lines.append(line)
                    print(line, flush=True)
            except (IOError, OSError):
                pass

            if proc.poll() is not None:
                remaining = proc.stdout.read()
                if remaining:
                    for line in remaining.decode("utf-8", errors="replace").splitlines():
                        workload_output_lines.append(line)
                        print(line, flush=True)
                break
            time.sleep(0.1)
    finally:
        ret = proc.wait()
        stop_event.set()
        db_thread.join(timeout=3.0)
        runtime_thread.join(timeout=3.0)
        if ret != 0:
            raise subprocess.CalledProcessError(ret, workload_cmd)

    end_ts = time.time()
    cpu_stats = _stats(samples_cpu)
    mem_stats = _stats(samples_mem)
    io_read_stats = _stats(samples_io_read)
    io_write_stats = _stats(samples_io_write)

    with open(db_monitor_csv, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_label",
                "time",
                "elapsed_s",
                "qps_statements",
                "xact_tps",
                "commit_tps",
                "rollback_tps",
                "tuple_read_ps",
                "tuple_write_ps",
                "block_read_ps",
                "block_hit_ps",
                "active_sessions",
            ],
        )
        if handle.tell() == 0:
            writer.writeheader()
        for row in db_rows:
            csv_row = dict(row)
            csv_row["case_label"] = case_label
            writer.writerow(csv_row)

    benchmark_parsed = parse_hybench_benchmark_output(workload_output_lines)
    return {
        "window": [start_ts, end_ts],
        "resource_avg": {
            "cpu": cpu_stats["avg"],
            "memory": mem_stats["avg"],
            "io_read": io_read_stats["avg"],
            "io_write": io_write_stats["avg"],
        },
        "resource_stats": {
            "cpu": cpu_stats,
            "memory": mem_stats,
            "io_read": io_read_stats,
            "io_write": io_write_stats,
            "memory_source": runtime_meta.get("memory_source"),
            "memory_pg_pid_count": runtime_meta.get("memory_pg_pid_count"),
            "io_source": runtime_meta.get("io_source", "unknown"),
        },
        "db_view_monitor": _summarize_db_rows(db_rows, db_monitor_csv, include_statements),
        "db_view_monitor_errors": db_errors,
        "runtime_errors": runtime_errors,
        "benchmark": benchmark_parsed.get("benchmark") or {},
        "benchmark_latency": benchmark_parsed.get("benchmark_latency") or {},
        "workload_output_tail": benchmark_parsed.get("workload_output") or "",
    }


def _summary_value(result: Dict[str, Any], key: str) -> Optional[float]:
    return _to_float((result.get("benchmark") or {}).get(key))


def _build_cases(selected_params: Optional[List[str]]) -> List[Tuple[str, str, Dict[str, float]]]:
    params = selected_params or list(PARAM_SPECS.keys())
    unknown = [name for name in params if name not in PARAM_SPECS]
    if unknown:
        raise ValueError(f"unknown params: {', '.join(unknown)}")
    defaults = {name: spec.mid for name, spec in PARAM_SPECS.items()}
    cases: List[Tuple[str, str, Dict[str, float]]] = []
    for param_name in params:
        spec = PARAM_SPECS[param_name]
        for level_name, value in [("low", spec.low), ("mid", spec.mid), ("high", spec.high)]:
            case_params = dict(defaults)
            case_params[param_name] = value
            cases.append((param_name, level_name, case_params))
    return cases


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sweep PostgreSQL planner params across low/mid/high boundaries with restart and monitoring.",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("OPTIMIZER_CONFIG", "config/optimizer_config.json"),
        help="config path, default: config/optimizer_config.json",
    )
    parser.add_argument(
        "--params",
        default="",
        help="comma separated subset of params to run",
    )
    parser.add_argument(
        "--output-dir",
        default="history/param_boundary_sweep",
        help="output directory root",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = _load_config(args.config)
    output_root = os.path.abspath(args.output_dir)
    os.makedirs(output_root, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(output_root, timestamp)
    os.makedirs(run_dir, exist_ok=True)

    log_path = os.path.join(run_dir, "sweep_history.jsonl")
    summary_csv = os.path.join(run_dir, "summary.csv")
    db_monitor_csv = os.path.join(run_dir, "db_monitor.csv")
    logger = OptimizationLogger(log_path, verbose=True)

    postgres_cfg = cfg.get("postgres") or {}
    benchmark_cfg = cfg.get("benchmark") or {}
    workload_cmd = str(benchmark_cfg.get("workload_cmd") or "").strip()
    if not workload_cmd:
        raise RuntimeError("benchmark.workload_cmd is empty")

    pg = PgConfig(
        host=os.environ.get("PGHOST", str(postgres_cfg.get("host", "127.0.0.1"))),
        port=os.environ.get("PGPORT", str(postgres_cfg.get("port", "5432"))),
        user=os.environ.get("PGUSER", str(postgres_cfg.get("user", "postgres"))),
        dbname=os.environ.get("PGDATABASE", str(postgres_cfg.get("dbname", "postgres"))),
        connect_timeout_s=max(1, int(float(os.environ.get("PGCONNECT_TIMEOUT", "5")))),
    )

    selected_params = [item.strip() for item in args.params.split(",") if item.strip()] or None
    include_statements = _has_pg_stat_statements(pg)
    cases = _build_cases(selected_params)

    logger.log(f"Output dir: {run_dir}", force=True)
    logger.log(f"Workload cmd: {workload_cmd}", force=True)
    logger.log(f"pg_stat_statements={include_statements}", force=True)
    logger.log(f"Case count: {len(cases)}", force=True)

    with open(summary_csv, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_label",
                "param_name",
                "level",
                "target_value",
                "cpu_avg",
                "memory_avg_bytes",
                "io_read_avg_bytes_s",
                "io_write_avg_bytes_s",
                "db_qps",
                "db_xact_tps",
                "db_commit_tps",
                "db_active_sessions",
                "xp_qps",
                "xp_tps",
                "ap_qps",
                "tp_tps",
                "latency_p95",
                "latency_p99",
                "memory_source",
                "io_source",
                "db_monitor_sample_count",
                "effective_params_json",
            ],
        )
        writer.writeheader()

        try:
            for index, (param_name, level_name, case_params) in enumerate(cases, start=1):
                case_label = f"{index:02d}_{param_name}_{level_name}"
                logger.log(f"=== [{index}/{len(cases)}] {case_label} ===", force=True)
                logger.log(f"Applying params: {case_params}", force=True)
                _apply_params(pg, case_params)
                _restart_db(pg, cfg, logger, tag=case_label)
                _reset_pg_stats(pg, include_statements)
                effective_params = _read_effective_params(pg, list(case_params.keys()))
                result = _run_case_workload(
                    workload_cmd,
                    pg,
                    cfg,
                    db_monitor_csv,
                    case_label=case_label,
                    include_statements=include_statements,
                )
                payload = {
                    "event": "boundary_case_result",
                    "case_label": case_label,
                    "param_name": param_name,
                    "level": level_name,
                    "target_value": case_params[param_name],
                    "applied_params": case_params,
                    "effective_params": effective_params,
                    "result": result,
                }
                logger.log_struct(payload)
                writer.writerow(
                    {
                        "case_label": case_label,
                        "param_name": param_name,
                        "level": level_name,
                        "target_value": case_params[param_name],
                        "cpu_avg": result["resource_avg"]["cpu"],
                        "memory_avg_bytes": result["resource_avg"]["memory"],
                        "io_read_avg_bytes_s": result["resource_avg"]["io_read"],
                        "io_write_avg_bytes_s": result["resource_avg"]["io_write"],
                        "db_qps": result["db_view_monitor"].get("db_qps"),
                        "db_xact_tps": result["db_view_monitor"].get("db_xact_tps"),
                        "db_commit_tps": result["db_view_monitor"].get("db_commit_tps"),
                        "db_active_sessions": result["db_view_monitor"].get("db_active_sessions"),
                        "xp_qps": _summary_value(result, "xp_qps"),
                        "xp_tps": _summary_value(result, "xp_tps"),
                        "ap_qps": _summary_value(result, "ap_qps"),
                        "tp_tps": _summary_value(result, "tp_tps"),
                        "latency_p95": _summary_value(result, "latency_p95"),
                        "latency_p99": _summary_value(result, "latency_p99"),
                        "memory_source": result["resource_stats"].get("memory_source"),
                        "io_source": result["resource_stats"].get("io_source"),
                        "db_monitor_sample_count": result["db_view_monitor"].get("sample_count"),
                        "effective_params_json": json.dumps(effective_params, ensure_ascii=False),
                    }
                )
                handle.flush()
        finally:
            restore_params = {name: spec.mid for name, spec in PARAM_SPECS.items()}
            logger.log("Restoring mid/default parameters...", force=True)
            try:
                _apply_params(pg, restore_params)
                _restart_db(pg, cfg, logger, tag="restore_defaults")
            except Exception as exc:
                logger.log(f"[WARN] restore_defaults failed: {exc}", force=True)
                logger.log_struct(
                    {
                        "event": "restore_defaults_failed",
                        "success": False,
                        "error": str(exc),
                    }
                )
            logger.close()

    print(f"Summary CSV: {summary_csv}", flush=True)
    print(f"DB monitor CSV: {db_monitor_csv}", flush=True)
    print(f"JSONL log: {logger.get_log_path() or log_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
