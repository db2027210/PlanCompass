"""Run a real MySQL benchmark while sampling the owned mysqld process.

The benchmark process is always fresh, which is required for MySQL optimizer
cost changes to become visible to new sessions.  Resource values use the same
physical units as the existing double-loop framework: CPU cores, GiB memory
and MiB/s physical IO.  Memory can be process RSS, SQL/TempTable dynamic
allocation, or MySQL's configured buffer pool plus that dynamic allocation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping, Sequence

from mysql_cost_adapter import MySQLCLIClient
from mysql_resource_monitor import _aggregate
from mysql_workload_adapter import (
    MySQLEndpoint, generate_benchbase_xml, generate_hybench_properties,
    parse_benchbase_summary, parse_hybench_mode_metrics, run_job,
)


@dataclass(frozen=True)
class LiveMeasurement:
    db_qps: float
    cpu: float
    memory: float
    io: float
    p95_ms: float
    p99_ms: float
    explain_samples: tuple[str, ...]
    elapsed_s: float
    resource_sample_count: int
    benchmark_summary: str

    def as_payload(self) -> dict[str, Any]:
        value = asdict(self)
        value["explain_samples"] = list(self.explain_samples)
        value["resource"] = {
            "cpu": self.cpu,
            "memory": self.memory,
            "io": self.io,
            "db_qps": self.db_qps,
        }
        return value


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def summarize_samples(samples: Sequence[Mapping[str, float]], warmup: int = 1) -> dict[str, float]:
    usable = list(samples[max(0, int(warmup)):]) or list(samples)
    if not usable:
        raise ValueError("no resource samples were collected")
    result = {
        name: sum(float(row[name]) for row in usable) / len(usable)
        for name in ("cpu", "memory", "io")
    }
    if not all(math.isfinite(value) and value >= 0.0 for value in result.values()):
        raise ValueError("resource samples contain invalid values")
    return result


def _pfs_sql_memory_gib(client: MySQLCLIClient) -> float:
    sql = (
        "SELECT COALESCE(SUM(CURRENT_NUMBER_OF_BYTES_USED),0)/1073741824 "
        "FROM performance_schema.memory_summary_global_by_event_name "
        "WHERE EVENT_NAME LIKE 'memory/sql/%' OR EVENT_NAME LIKE 'memory/temptable/%'"
    )
    return float(client.execute(sql).strip())


def _mysql_total_memory_gib(client: MySQLCLIClient) -> float:
    """MySQL memory budget: pool, live-session buffers, and dynamic SQL memory."""
    sql = (
        "SELECT (@@GLOBAL.innodb_buffer_pool_size + "
        "CAST((SELECT VARIABLE_VALUE FROM performance_schema.global_status "
        "WHERE VARIABLE_NAME='Threads_connected') AS UNSIGNED) * "
        "(@@GLOBAL.join_buffer_size + @@GLOBAL.sort_buffer_size + "
        "@@GLOBAL.read_buffer_size + @@GLOBAL.read_rnd_buffer_size) + "
        "COALESCE(SUM(CURRENT_NUMBER_OF_BYTES_USED),0))/1073741824 "
        "FROM performance_schema.memory_summary_global_by_event_name "
        "WHERE EVENT_NAME LIKE 'memory/sql/%' OR EVENT_NAME LIKE 'memory/temptable/%'"
    )
    return float(client.execute(sql).strip())


def _memory_value(client: MySQLCLIClient, rss_gib: float, memory_mode: str) -> float:
    if memory_mode == "rss":
        return rss_gib
    if memory_mode == "sql_dynamic":
        return _pfs_sql_memory_gib(client)
    if memory_mode == "sql_total":
        return _mysql_total_memory_gib(client)
    raise ValueError(f"unsupported memory mode: {memory_mode}")


def _active_queries(client: MySQLCLIClient, database: str) -> list[str]:
    escaped = database.replace("'", "''")
    sql = (
        "SELECT INFO FROM information_schema.processlist "
        f"WHERE DB='{escaped}' AND COMMAND<>'Sleep' AND INFO IS NOT NULL "
        "AND INFO NOT LIKE 'SELECT INFO FROM information_schema.processlist%'"
    )
    try:
        return [line.strip() for line in client.execute(sql).splitlines() if line.strip()]
    except RuntimeError:
        return []


def _explain_queries(client: MySQLCLIClient, queries: Sequence[str], limit: int = 3) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in queries:
        query = raw.strip().rstrip(";")
        if not query or query in seen:
            continue
        seen.add(query)
        try:
            explained = client.execute("EXPLAIN FORMAT=JSON " + query).strip()
        except RuntimeError:
            continue
        if explained:
            output.append(explained[:20000])
        if len(output) >= limit:
            break
    return tuple(output)


def _representative_query(benchmark: str) -> str:
    if benchmark.lower() == "tpch":
        return (
            "SELECT l_returnflag,l_linestatus,SUM(l_quantity) AS sum_qty,"
            "COUNT(*) AS count_order FROM lineitem "
            "WHERE l_shipdate <= DATE '1998-12-01' - INTERVAL '90' DAY "
            "GROUP BY l_returnflag,l_linestatus"
        )
    return (
        "SELECT w_id,w_name FROM warehouse WHERE w_id=1"
        if benchmark.lower() == "tpcc" else "SELECT 1"
    )


def _sample_until(
    done: Any, *, mysqld_pid: int, interval_s: float, timeout_s: float,
    client: MySQLCLIClient, database: str, memory_mode: str = "rss",
) -> tuple[list[dict[str, float]], list[str]]:
    clock_ticks = float(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
    samples: list[dict[str, float]] = []
    captured_queries: list[str] = []
    started = time.monotonic()
    previous_time = started
    previous = _aggregate([mysqld_pid])
    first_sample = True
    while first_sample or not done():
        first_sample = False
        if time.monotonic() - started > timeout_s:
            raise TimeoutError(f"workload exceeded {timeout_s:.1f}s")
        time.sleep(interval_s)
        now = time.monotonic()
        current = _aggregate([mysqld_pid])
        elapsed = max(now - previous_time, 1e-9)
        rss_gib = max(0.0, current[1]) / 1024.0 / 1024.0
        samples.append({
            "cpu": max(0.0, current[0] - previous[0]) / clock_ticks / elapsed,
            "memory": _memory_value(client, rss_gib, memory_mode),
            "rss_memory": rss_gib,
            "io": max(0.0, current[2] + current[3] - previous[2] - previous[3])
                  / 1024.0 / 1024.0 / elapsed,
        })
        previous_time, previous = now, current
        if len(samples) % max(1, round(5.0 / interval_s)) == 0:
            captured_queries.extend(_active_queries(client, database))
    return samples, captured_queries


def measure_benchbase(
    benchmark: str,
    endpoint: MySQLEndpoint,
    output_dir: Path | str,
    *,
    mysqld_pid: int,
    jar_path: Path | str,
    duration_s: int,
    terminals: int,
    scale_factor: float,
    interval_s: float = 1.0,
    timeout_s: float = 600.0,
    warmup_samples: int = 1,
    transaction_weights: Sequence[float] | None = None,
    rate_limit: float | None = None,
    memory_mode: str = "rss",
) -> dict[str, Any]:
    """Execute BenchBase and return QPS, latency, physical resources and plans."""
    if mysqld_pid <= 0 or not (Path("/proc") / str(mysqld_pid)).is_dir():
        raise ValueError(f"invalid mysqld pid: {mysqld_pid}")
    if duration_s <= 0 or terminals <= 0 or interval_s <= 0.0:
        raise ValueError("duration, terminals and interval must be positive")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    jar = Path(jar_path).resolve()
    if not jar.is_file():
        raise FileNotFoundError(jar)
    config = generate_benchbase_xml(
        benchmark, endpoint, output, duration_s=duration_s,
        terminals=terminals, scale_factor=scale_factor,
        transaction_weights=transaction_weights,
        rate_limit=rate_limit,
    )
    argv = [
        "java", "-XX:ActiveProcessorCount=4", "-jar", str(jar),
        "-b", benchmark.lower(), "-c", str(config), "--execute=true",
        "-d", str(output),
    ]
    _write(output / "command.json", json.dumps({"argv": argv, "cwd": str(jar.parent)}, indent=2) + "\n")
    client = MySQLCLIClient({
        "host": endpoint.host, "port": endpoint.port, "user": endpoint.user,
        "password": endpoint.password, "database": endpoint.database,
        "query_timeout_sec": 15,
    })
    stdout_path = output / "stdout.log"
    stderr_path = output / "stderr.log"
    clock_ticks = float(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
    samples: list[dict[str, float]] = []
    captured_queries: list[str] = []
    started = time.monotonic()
    previous_time = started
    previous = _aggregate([mysqld_pid])
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(argv, cwd=str(jar.parent), stdout=stdout, stderr=stderr, text=True)
        while process.poll() is None:
            if time.monotonic() - started > timeout_s:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise TimeoutError(f"BenchBase exceeded {timeout_s:.1f}s")
            time.sleep(interval_s)
            now = time.monotonic()
            current = _aggregate([mysqld_pid])
            elapsed = max(now - previous_time, 1e-9)
            rss_gib = max(0.0, current[1]) / 1024.0 / 1024.0
            samples.append({
                "cpu": max(0.0, current[0] - previous[0]) / clock_ticks / elapsed,
                "memory": _memory_value(client, rss_gib, memory_mode),
                "rss_memory": rss_gib,
                "io": max(0.0, current[2] + current[3] - previous[2] - previous[3])
                      / 1024.0 / 1024.0 / elapsed,
            })
            previous_time, previous = now, current
            if len(samples) % max(1, round(5.0 / interval_s)) == 0:
                captured_queries.extend(_active_queries(client, endpoint.database))
        returncode = process.wait()
    if returncode:
        detail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"BenchBase exited {returncode}: {detail}")
    summaries = sorted(output.glob("*.summary.json"), key=lambda path: path.stat().st_mtime)
    if not summaries:
        raise RuntimeError("BenchBase produced no summary JSON")
    metrics = parse_benchbase_summary(summaries[-1])
    resources = summarize_samples(samples, warmup_samples)
    explains = _explain_queries(client, captured_queries)
    if not explains:
        explains = _explain_queries(client, [_representative_query(benchmark)], limit=1)
    measurement = LiveMeasurement(
        db_qps=metrics.qps,
        cpu=resources["cpu"], memory=resources["memory"], io=resources["io"],
        p95_ms=metrics.latency_p95_ms, p99_ms=metrics.latency_p99_ms,
        explain_samples=explains,
        elapsed_s=time.monotonic() - started,
        resource_sample_count=len(samples),
        benchmark_summary=str(summaries[-1]),
    )
    payload = measurement.as_payload()
    payload["memory_measurement_mode"] = memory_mode
    payload["rss_memory_gib"] = sum(row["rss_memory"] for row in samples) / len(samples)
    _write(output / "live_measurement.json", json.dumps(payload, indent=2) + "\n")
    return payload


def measure_benchbase_multiprocess(
    benchmark: str,
    endpoint: MySQLEndpoint,
    output_dir: Path | str,
    *,
    mysqld_pid: int,
    jar_path: Path | str,
    duration_s: int,
    processes: int,
    terminals_per_process: int = 1,
    scale_factor: float,
    interval_s: float = 1.0,
    timeout_s: float = 600.0,
    transaction_weights: Sequence[float] | None = None,
    rate_limit: float | None = None,
    memory_mode: str = "rss",
) -> dict[str, Any]:
    """Run independent one-terminal BenchBase clients and aggregate one DB endpoint.

    This avoids BenchBase's multi-terminal shutdown barrier while preserving
    concurrent database pressure.  Every child samples the same owned mysqld;
    resource values are therefore averaged, while throughputs are summed.
    """
    if processes < 2 or terminals_per_process < 1:
        raise ValueError("multiprocess BenchBase needs at least two processes")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)

    def run_one(index: int) -> dict[str, Any]:
        child_rate = None if rate_limit is None else float(rate_limit) / processes
        return measure_benchbase(
            benchmark, endpoint, output / f"client_{index:02d}",
            mysqld_pid=mysqld_pid, jar_path=jar_path,
            duration_s=duration_s, terminals=terminals_per_process, scale_factor=scale_factor,
            interval_s=interval_s, timeout_s=timeout_s,
            transaction_weights=transaction_weights, rate_limit=child_rate,
            memory_mode=memory_mode,
        )

    with ThreadPoolExecutor(max_workers=processes) as executor:
        children = list(executor.map(run_one, range(1, processes + 1)))
    resources = {
        key: sum(float(row["resource"][key]) for row in children) / len(children)
        for key in ("cpu", "memory", "io")
    }
    qps = sum(float(row["db_qps"]) for row in children)
    explains: list[str] = []
    for row in children:
        for sample in row.get("explain_samples") or []:
            if sample not in explains:
                explains.append(sample)
    payload = {
        "db_qps": qps,
        "cpu": resources["cpu"],
        "memory": resources["memory"],
        "io": resources["io"],
        "p95_ms": max(float(row["p95_ms"]) for row in children),
        "p99_ms": max(float(row["p99_ms"]) for row in children),
        "explain_samples": explains[:3],
        "elapsed_s": max(float(row["elapsed_s"]) for row in children),
        "resource_sample_count": min(int(row["resource_sample_count"]) for row in children),
        "benchmark_summary": [row["benchmark_summary"] for row in children],
        "resource": resources | {"db_qps": qps},
        "memory_measurement_mode": memory_mode,
        "rss_memory_gib": sum(float(row["rss_memory_gib"]) for row in children) / len(children),
        "benchbase_client_processes": processes,
        "benchbase_terminals_per_process": terminals_per_process,
    }
    _write(output / "live_measurement.json", json.dumps(payload, indent=2) + "\n")
    return payload


def measure_hybench(
    mode: str,
    endpoint: MySQLEndpoint,
    output_dir: Path | str,
    *,
    mysqld_pid: int,
    hybench_home: Path | str,
    duration_s: int,
    clients: int,
    scale_factor: str = "10x",
    interval_s: float = 1.0,
    timeout_s: float = 900.0,
    warmup_samples: int = 1,
    extra_properties: Mapping[str, str | int | float] | None = None,
    memory_mode: str = "rss",
) -> dict[str, Any]:
    """Execute native HyBench AP/TP/XP and sample the owned mysqld."""
    mode = mode.lower()
    mode_arg = {"ap": "runap", "tp": "runtp", "xp": "runxp"}.get(mode)
    if mode_arg is None:
        raise ValueError("HyBench mode must be ap, tp or xp")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    home = Path(hybench_home).resolve()
    launcher = home / "hybench"
    statement_file = home / "conf" / "stmt_mysql.toml"
    if not launcher.is_file() or not statement_file.is_file():
        raise FileNotFoundError(f"incomplete HyBench home: {home}")
    extras = {"sf": scale_factor} | dict(extra_properties or {})
    props = generate_hybench_properties(
        endpoint, output, duration_s=duration_s, clients=clients, extra=extras,
    )
    argv = ["bash", str(launcher), "-t", mode_arg, "-c", str(props), "-f", str(statement_file)]
    _write(output / "command.json", json.dumps({"argv": argv, "cwd": str(home)}, indent=2) + "\n")
    client = MySQLCLIClient({
        "host": endpoint.host, "port": endpoint.port, "user": endpoint.user,
        "password": endpoint.password, "database": endpoint.database,
        "query_timeout_sec": 15,
    })
    stdout_path, stderr_path = output / "stdout.log", output / "stderr.log"
    started = time.monotonic()
    env = dict(os.environ)
    env["HYBENCH_CONFIG"] = str(props)
    env["HYBENCH_OUTPUT_DIR"] = str(output)
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(argv, cwd=str(home), env=env, stdout=stdout, stderr=stderr, text=True)
        try:
            samples, captured = _sample_until(
                lambda: process.poll() is not None,
                mysqld_pid=mysqld_pid, interval_s=interval_s, timeout_s=timeout_s,
                client=client, database=endpoint.database,
                memory_mode=memory_mode,
            )
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        returncode = process.wait()
    if returncode:
        detail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"HyBench exited {returncode}: {detail}")
    stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
    metrics = parse_hybench_mode_metrics(stdout_text, mode)
    resources = summarize_samples(samples, warmup_samples)
    explains = _explain_queries(client, captured)
    if not explains:
        explains = _explain_queries(client, ["SELECT COUNT(*) FROM transfer"], limit=1)
    measurement = LiveMeasurement(
        db_qps=metrics.qps, cpu=resources["cpu"], memory=resources["memory"], io=resources["io"],
        p95_ms=metrics.latency_p95_ms, p99_ms=metrics.latency_p99_ms,
        explain_samples=explains, elapsed_s=time.monotonic() - started,
        resource_sample_count=len(samples), benchmark_summary=str(stdout_path),
    )
    payload = measurement.as_payload()
    payload["hybench_mode"] = mode
    payload["throughput_source"] = (
        "official_ap_histogram_apTotalCount_over_apTotalTime"
        if mode == "ap" and not re.search(r"(?im)(?<![-\w])QPS\s*(?:is|:|=)", stdout_text)
        else "native_hybench_summary_xp_qps_plus_tps"
        if mode == "xp"
        else "native_hybench_summary"
    )
    payload["latency_source"] = f"native_hybench_{mode}_summary_percentile"
    payload["memory_measurement_mode"] = memory_mode
    payload["rss_memory_gib"] = sum(row["rss_memory"] for row in samples) / len(samples)
    _write(output / "live_measurement.json", json.dumps(payload, indent=2) + "\n")
    return payload


def measure_job(
    endpoint: MySQLEndpoint,
    output_dir: Path | str,
    *,
    mysqld_pid: int,
    manifest_path: Path | str,
    concurrency: int,
    repetitions: int,
    interval_s: float = 1.0,
    timeout_s: float = 900.0,
    warmup_samples: int = 1,
    memory_mode: str = "rss",
) -> dict[str, Any]:
    """Replay the immutable JOB manifest while sampling the owned mysqld."""
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    client = MySQLCLIClient({
        "host": endpoint.host, "port": endpoint.port, "user": endpoint.user,
        "password": endpoint.password, "database": endpoint.database,
        "query_timeout_sec": 15,
    })
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            run_job, manifest_path, endpoint, output,
            concurrency=concurrency, repetitions=repetitions,
            dry_run=False, timeout_s=timeout_s,
        )
        samples, captured = _sample_until(
            future.done, mysqld_pid=mysqld_pid, interval_s=interval_s,
            timeout_s=timeout_s, client=client, database=endpoint.database,
            memory_mode=memory_mode,
        )
        result = future.result()
    metrics = result["metrics"]
    resources = summarize_samples(samples, warmup_samples)
    explains = tuple(metrics.get("explain_samples") or ()) or _explain_queries(client, captured)
    measurement = LiveMeasurement(
        db_qps=float(metrics["qps"]), cpu=resources["cpu"], memory=resources["memory"], io=resources["io"],
        p95_ms=float(metrics["latency_p95_ms"]), p99_ms=float(metrics["latency_p99_ms"]),
        explain_samples=tuple(explains), elapsed_s=time.monotonic() - started,
        resource_sample_count=len(samples), benchmark_summary=str(output / "adapter_result.json"),
    )
    payload = measurement.as_payload()
    payload["memory_measurement_mode"] = memory_mode
    payload["rss_memory_gib"] = sum(row["rss_memory"] for row in samples) / len(samples)
    _write(output / "live_measurement.json", json.dumps(payload, indent=2) + "\n")
    return payload
