"""Run a real MySQL benchmark and emit the optimizer measurement protocol.

The runner currently supports the local BenchBase TPC-C/TPC-H distribution.
It creates an ephemeral configuration for the selected isolated MySQL worker,
samples the host mysqld process, parses BenchBase's summary, and prints one
``RESOURCE_METRICS=...`` JSON line.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Dict
import xml.etree.ElementTree as ET

from mysql_resource_monitor import run_and_monitor


def _configure(source: Path, target: Path, *, host: str, port: int, database: str, terminals: int | None, duration_sec: int | None, loader_threads: int | None = None, scale_factor: float | None = None, query_ids: list[int] | None = None, tpcc_weights: list[int] | None = None, user: str = "admin", password: str = "password") -> None:
    tree = ET.parse(source)
    root = tree.getroot()
    url = root.find("url")
    if url is None or not url.text:
        raise ValueError(f"missing JDBC URL in {source}")
    suffix = url.text.split("?", 1)[1] if "?" in url.text else ""
    url.text = f"jdbc:mysql://{host}:{port}/{database}" + (f"?{suffix}" if suffix else "")
    for tag, value in (("username", user), ("password", password)):
        node = root.find(tag)
        if node is not None:
            node.text = value
    if terminals is not None:
        node = root.find("terminals")
        if node is None:
            raise ValueError(f"missing terminals in {source}")
        node.text = str(terminals)
    if duration_sec is not None:
        times = root.findall("./works/work/time")
        for node in times:
            node.text = str(duration_sec)
    if loader_threads is not None:
        node = root.find("loaderThreads")
        if node is None:
            node = ET.Element("loaderThreads")
            batch = root.find("batchsize")
            root.insert(list(root).index(batch) + 1 if batch is not None else 0, node)
        node.text = str(loader_threads)
    if scale_factor is not None:
        node = root.find("scalefactor")
        if node is None:
            raise ValueError(f"scale factor override requested but no scalefactor exists in {source}")
        node.text = str(scale_factor)
    if query_ids is not None:
        invalid = sorted(set(query_ids) - set(range(1, 23)))
        if invalid or not query_ids:
            raise ValueError(f"TPC-H query IDs must be non-empty and within 1..22: {invalid}")
        works = root.find("works")
        if works is None:
            raise ValueError(f"missing works in {source}")
        work_nodes = works.findall("work")
        if not work_nodes:
            raise ValueError(f"missing work in {source}")
        for extra in work_nodes[1:]:
            works.remove(extra)
        weights = work_nodes[0].find("weights")
        if weights is None:
            raise ValueError(f"missing work weights in {source}")
        serial = work_nodes[0].find("serial")
        if serial is None:
            raise ValueError(f"missing work serial flag in {source}")
        serial.text = "false"
        time_node = work_nodes[0].find("time")
        if time_node is None:
            time_node = ET.Element("time")
            rate = work_nodes[0].find("rate")
            children = list(work_nodes[0])
            work_nodes[0].insert(children.index(rate) + 1 if rate is not None else 0, time_node)
        time_node.text = str(duration_sec if duration_sec is not None else 30)
        selected_ids = list(dict.fromkeys(query_ids))
        selected = set(selected_ids)
        base_weight, remainder = divmod(100, len(selected_ids))
        weight_by_query = {
            query_id: base_weight + (1 if index < remainder else 0)
            for index, query_id in enumerate(selected_ids)
        }
        weights.text = ",".join(str(weight_by_query.get(query_id, 0)) for query_id in range(1, 23))
    if tpcc_weights is not None:
        if len(tpcc_weights) != 5 or any(weight < 0 for weight in tpcc_weights) or sum(tpcc_weights) != 100:
            raise ValueError("TPC-C weights must be five non-negative integers summing to 100")
        weights_nodes = root.findall("./works/work/weights")
        if not weights_nodes:
            raise ValueError(f"missing TPC-C work weights in {source}")
        weights_nodes[0].text = ",".join(str(weight) for weight in tpcc_weights)
    tree.write(target, encoding="utf-8", xml_declaration=True)


def _latest_summary(directory: Path, started_at: float) -> Path:
    matches = [
        path for path in directory.rglob("*.summary.json")
        if path.stat().st_mtime >= started_at - 2.0
    ]
    if not matches:
        raise RuntimeError(f"BenchBase produced no summary below {directory}")
    return max(matches, key=lambda path: path.stat().st_mtime)


def _explain(host: str, port: int, user: str, password: str, database: str, benchmark: str,
             query_ids: list[int] | None = None) -> str:
    if benchmark == "tpcc":
        # Representative StockLevel statement used by the read-only TPC-C
        # preflight mix.  This captures its join/access plan instead of a
        # plan-insensitive COUNT(*) over warehouse.
        statement = (
            "SELECT COUNT(DISTINCTROW(s_i_id)) FROM order_line, stock "
            "WHERE ol_w_id=1 AND ol_d_id=1 AND ol_o_id < 3001 "
            "AND ol_o_id >= 2981 AND s_w_id=1 AND s_i_id=ol_i_id "
            "AND s_quantity < 15"
        )
    elif query_ids == [14]:
        statement = (
            "SELECT 100.00*SUM(CASE WHEN p_type LIKE 'PROMO%' THEN "
            "l_extendedprice*(1-l_discount) ELSE 0 END)/"
            "SUM(l_extendedprice*(1-l_discount)) AS promo_revenue "
            "FROM lineitem,part WHERE l_partkey=p_partkey "
            "AND l_shipdate>=DATE '1995-09-01' "
            "AND l_shipdate<CAST('1995-10-01' AS DATE)"
        )
    else:
        statement = "SELECT COUNT(*) FROM lineitem"
    proc = subprocess.run(
        [
            "mysql", f"--host={host}", f"--port={port}", f"--user={user}",
            f"--password={password}", "--batch", "--raw", "--skip-column-names",
            database, "-e", "EXPLAIN FORMAT=JSON " + statement,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0:
        return f"EXPLAIN unavailable: {(proc.stderr or proc.stdout)[-1000:].strip()}"
    return proc.stdout.strip()


def run(args: argparse.Namespace) -> Dict[str, Any]:
    root = args.benchbase_root.resolve()
    config_source = root / "config" / "mysql" / f"sample_{args.benchmark}_config.xml"
    if not config_source.is_file():
        raise FileNotFoundError(config_source)
    args.output_root.mkdir(parents=True, exist_ok=True)
    run_dir = args.output_root / f"{args.benchmark}_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns()}"
    run_dir.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix=f"ra_mysql_{args.benchmark}_") as temp_dir:
        config_path = Path(temp_dir) / f"{args.benchmark}.xml"
        _configure(
            config_source,
            config_path,
            host=args.host,
            port=args.port,
            database=args.database,
            user=args.user, password=args.password,
            terminals=args.terminals,
            duration_sec=args.duration_sec,
            loader_threads=args.loader_threads,
            scale_factor=args.scale_factor,
            query_ids=args.query_ids,
            tpcc_weights=args.tpcc_weights if args.benchmark == "tpcc" else None,
        )
        command = [
            "java", "-jar", str(root / "benchbase.jar"),
            "-b", args.benchmark,
            "-c", str(config_path),
            "--create=false", "--load=false", "--execute=true",
            "-d", str(run_dir),
        ]
        if args.prewarm_sql:
            prewarm = subprocess.run(
                [
                    "mysql", f"--host={args.host}", f"--port={args.port}",
                    f"--user={args.user}", f"--password={args.password}",
                    args.database, "-e", "SET SESSION MAX_EXECUTION_TIME=0; " + args.prewarm_sql,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=float(args.prewarm_timeout_sec),
            )
            if prewarm.returncode != 0:
                detail = (prewarm.stderr or prewarm.stdout or "benchmark prewarm failed").strip()
                raise RuntimeError(f"benchmark prewarm failed: {detail[-2000:]}")
        if args.warmup_duration_sec > 0:
            warmup_config = Path(temp_dir) / f"{args.benchmark}_warmup.xml"
            _configure(
                config_source,
                warmup_config,
                host=args.host,
                port=args.port,
                database=args.database,
                user=args.user, password=args.password,
                terminals=args.terminals,
                duration_sec=args.warmup_duration_sec,
                loader_threads=args.loader_threads,
                scale_factor=args.scale_factor,
                query_ids=args.query_ids,
                tpcc_weights=args.tpcc_weights if args.benchmark == "tpcc" else None,
            )
            warmup_dir = run_dir / "warmup"
            warmup_dir.mkdir()
            warmup_command = [
                "java", "-jar", str(root / "benchbase.jar"),
                "-b", args.benchmark, "-c", str(warmup_config),
                "--create=false", "--load=false", "--execute=true",
                "-d", str(warmup_dir),
            ]
            warmup = subprocess.run(
                warmup_command, cwd=root, check=False, capture_output=True, text=True,
            )
            if warmup.returncode:
                detail = (warmup.stderr or warmup.stdout)[-4000:].strip()
                raise RuntimeError(f"BenchBase warmup failed (exit={warmup.returncode}): {detail}")
        started_at = time.time()
        resource = run_and_monitor(
            command,
            pids=[args.mysqld_pid],
            interval_sec=args.interval_sec,
            warmup_samples=args.warmup_samples,
            cwd=str(root),
        )
    summary_path = _latest_summary(run_dir, started_at)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    latency = summary.get("Latency Distribution") or {}
    qps = float(summary["Throughput (requests/second)"])
    p95_ms = float(latency["95th Percentile Latency (microseconds)"]) / 1000.0
    p99_ms = float(latency["99th Percentile Latency (microseconds)"]) / 1000.0
    explain = _explain(
        args.host, args.port, args.user, args.password, args.database,
        args.benchmark, args.query_ids,
    )
    return {
        "resource": resource,
        "qps": qps,
        "query_count": float(summary.get("Measured Requests", 0)),
        "query_time": float(summary.get("Elapsed Time (nanoseconds)", 0)) / 1e9,
        "_stats": {
            "qps": {"avg": qps},
            "query_count": {"avg": float(summary.get("Measured Requests", 0))},
            "query_time": {"avg": float(summary.get("Elapsed Time (nanoseconds)", 0)) / 1e9},
            "benchmark": {
                "latency_p95": p95_ms,
                "latency_p99": p99_ms,
                "throughput": qps,
                "benchmark": args.benchmark,
            },
        },
        "_metadata": {
            "db_qps": qps,
            "latency_p95_ms": p95_ms,
            "latency_p99_ms": p99_ms,
            "summary_path": str(summary_path.resolve()),
            "explain_samples": [explain],
        },
    }


def prepare(args: argparse.Namespace) -> Dict[str, Any]:
    """Create and load one benchmark into its dedicated schema."""
    root = args.benchbase_root.resolve()
    config_source = root / "config" / "mysql" / f"sample_{args.benchmark}_config.xml"
    args.output_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "mysql", f"--host={args.host}", f"--port={args.port}", f"--user={args.user}",
            f"--password={args.password}", "-e",
            f"CREATE DATABASE IF NOT EXISTS `{args.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
        ],
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix=f"ra_mysql_prepare_{args.benchmark}_") as temp_dir:
        config_path = Path(temp_dir) / f"{args.benchmark}.xml"
        _configure(
            config_source,
            config_path,
            host=args.host,
            port=args.port,
            database=args.database,
            user=args.user, password=args.password,
            terminals=args.terminals,
            duration_sec=args.duration_sec,
            loader_threads=args.loader_threads,
            scale_factor=args.scale_factor,
            query_ids=args.query_ids,
            tpcc_weights=args.tpcc_weights if args.benchmark == "tpcc" else None,
        )
        command = [
            "java", "-jar", str(root / "benchbase.jar"),
            "-b", args.benchmark, "-c", str(config_path),
            "--create=true", "--load=true", "--execute=false",
            "-d", str(args.output_root),
        ]
        subprocess.run(command, cwd=root, check=True)
    return {"prepared": True, "benchmark": args.benchmark, "database": args.database}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=("tpcc", "tpch"), required=True)
    parser.add_argument("--benchbase-root", type=Path, default=Path("/home/anonymous/benchbase-mysql"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", default="benchbase")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="password")
    parser.add_argument("--mysqld-pid", type=int, required=True)
    parser.add_argument("--terminals", type=int)
    parser.add_argument("--duration-sec", type=int)
    parser.add_argument("--warmup-duration-sec", type=int, default=0)
    parser.add_argument("--interval-sec", type=float, default=1.0)
    parser.add_argument("--warmup-samples", type=int, default=1)
    parser.add_argument("--loader-threads", type=int, default=8)
    parser.add_argument("--scale-factor", type=float)
    parser.add_argument("--query-ids", type=lambda value: [int(item) for item in value.split(",") if item])
    parser.add_argument("--prewarm-sql", default="")
    parser.add_argument("--prewarm-timeout-sec", type=int, default=600)
    parser.add_argument("--tpcc-weights", type=lambda value: [int(item) for item in value.split(",") if item])
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.prepare_only:
        print(json.dumps(prepare(args), sort_keys=True), flush=True)
        return 0
    payload = run(args)
    print("RESOURCE_METRICS=" + json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
