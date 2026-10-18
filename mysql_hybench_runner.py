"""Run AP, TP, or XP HyBench on MySQL and emit optimizer measurement JSON."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, Tuple

from mysql_resource_monitor import run_and_monitor


_RAW_LOG_MAX_CHARS = 4 * 1024 * 1024
_RAW_LOG_IMPORTANT = re.compile(
    r"HYBENCH_METRICS_JSON=|\b(?:QPS|TPS) is\b|XP-(?:QPS|TPS)|95% rt|99% rt|"
    r"(?:ERROR|Exception|FATAL|WARN)",
    flags=re.I,
)


def _bounded_raw_log(output: str, max_chars: int = _RAW_LOG_MAX_CHARS) -> str:
    """Retain diagnostic summaries without letting per-transaction stdout flood disk."""
    if max_chars <= 0:
        raise ValueError("raw log limit must be positive")
    if len(output) <= max_chars:
        return output
    marker = f"\n[RAW_LOG_TRUNCATED original_chars={len(output)} limit_chars={max_chars}]\n"
    head = output[: max_chars // 8]
    important = "\n".join(line for line in output.splitlines() if _RAW_LOG_IMPORTANT.search(line))
    important = important[-max_chars // 4 :]
    remaining = max_chars - len(marker) - len(head) - len(important) - 2
    tail = output[-max(0, remaining) :]
    compact = head + marker + important + "\n" + tail
    return compact[:max_chars]


def _read_properties(path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _write_properties(path: Path, values: Dict[str, str]) -> None:
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")


def _last_number(patterns: Iterable[str], output: str) -> float | None:
    values = []
    for pattern in patterns:
        values.extend(float(item) for item in re.findall(pattern, output, flags=re.I))
    return values[-1] if values else None


def _parse_output(workload: str, output: str) -> Dict[str, float]:
    summaries = re.findall(r"HYBENCH_METRICS_JSON=(\{[^\n]+\})", output)
    native = json.loads(summaries[-1]) if summaries else {}
    native_qps = float(native["qps"]) if "qps" in native else None
    if workload == "ap":
        qps = native_qps if native_qps is not None else _last_number((r"\bQPS is\s+([0-9.]+)", r"Current \d+/\d+ time AP QPS is\s+([0-9.]+)"), output)
    elif workload == "tp":
        qps = native_qps if native_qps is not None else _last_number((r"\bTPS is\s+([0-9.]+)", r"Current \d+/\d+ time TP TPS is\s+([0-9.]+)"), output)
    else:
        iq = _last_number((r"XP-QPS is\s+([0-9.]+)", r"Current \d+/\d+ time AP QPS is\s+([0-9.]+)"), output) or 0.0
        tx = _last_number((r"XP-TPS is\s+([0-9.]+)", r"Current \d+/\d+ time TP TPS is\s+([0-9.]+)"), output) or 0.0
        qps = native_qps if native_qps is not None else iq + tx
    # A hard-bounded measurement can terminate HyBench before its JVM-level
    # final summary is printed.  Emit a zero sentinel here so the parent can
    # derive throughput from MySQL's Queries counter over the exact same
    # monitored interval.  Zero is never accepted as a valid workload result
    # by the preflight gate itself.
    if qps is None:
        qps = 0.0
    latency_pairs = [
        (float(p95), float(p99))
        for p95, p99 in re.findall(r"95% rt\s*:\s*([0-9.]+)\s*\|\s*99% rt\s*:\s*([0-9.]+)", output)
        if math.isfinite(float(p95)) and math.isfinite(float(p99))
    ]
    result = {
        "qps": qps,
        "latency_p95_ms": max((item[0] for item in latency_pairs), default=0.0),
        "latency_p99_ms": max((item[1] for item in latency_pairs), default=0.0),
    }
    if "query_count" in native:
        result["query_count"] = float(native["query_count"])
    return result


def _mysql_status_counter(args: argparse.Namespace, name: str) -> int:
    """Read a server counter without exposing the password in argv."""
    env = dict(os.environ)
    env["MYSQL_PWD"] = args.password
    proc = subprocess.run(
        [
            "mysql", "--protocol=tcp", f"--host={args.host}",
            f"--port={args.port}", f"--user={args.user}",
            "--batch", "--skip-column-names", "-e",
            f"SHOW GLOBAL STATUS LIKE '{name}'",
        ],
        capture_output=True, text=True, timeout=15, check=False, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"cannot read MySQL {name}: {(proc.stderr or proc.stdout)[-1000:]}")
    fields = proc.stdout.strip().split()
    if len(fields) < 2:
        raise RuntimeError(f"unexpected MySQL {name} response: {proc.stdout!r}")
    return int(fields[-1])


def worker(args: argparse.Namespace) -> Dict[str, Any]:
    command = [
        "java", "-cp", os.pathsep.join((str(args.connector_jar.resolve()), str(args.jar.resolve()))), "com.hybench.HyBench",
        "-t", f"run{args.workload}", "-c", str(args.runtime_config.resolve()),
        "-f", str(args.statements.resolve()),
    ]
    timed_out = False
    try:
        proc = subprocess.run(command, cwd=args.runtime_root, capture_output=True, text=True, timeout=args.timeout_sec, check=False)
        output = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
        returncode = proc.returncode
    except subprocess.TimeoutExpired as exc:
        # HyBench may finish its configured time window while one AP query is
        # still draining.  The timeout kills that tail query; retain periodic
        # throughput emitted during the complete measured window.
        timed_out = True
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        output = stdout + ("\n" + stderr if stderr else "")
        returncode = 0
    args.raw_log.parent.mkdir(parents=True, exist_ok=True)
    args.raw_log.write_text(_bounded_raw_log(output), encoding="utf-8")
    if returncode != 0:
        raise RuntimeError(f"HyBench failed (exit={returncode}): {output[-4000:]}")
    result = _parse_output(args.workload, output)
    result.setdefault("query_count", result["qps"] * args.duration_sec)
    result["query_time"] = float(args.duration_sec)
    result["tail_query_hard_stopped"] = timed_out
    print(json.dumps(result, sort_keys=True), flush=True)
    return result


def _explain(args: argparse.Namespace) -> str:
    if args.workload == "ap" and args.ap_query_sequence == "9":
        statement = (
            "SELECT c.name,SUM(t.amount) AS total_revenue "
            "FROM company c,transfer t "
            "WHERE c.companyid=t.targetid "
            "AND EXISTS (SELECT * FROM loanapps la WHERE la.applicantid=c.companyid "
            "AND loanapps_ts BETWEEN STR_TO_DATE('2023-11-01','%Y-%m-%d') "
            "AND (STR_TO_DATE('2023-11-01','%Y-%m-%d') + INTERVAL 60 DAY)) "
            "AND NOT EXISTS (SELECT * FROM loantrans lt WHERE lt.applicantid=c.companyid "
            "AND loantrans_ts BETWEEN STR_TO_DATE('2023-11-01','%Y-%m-%d') "
            "AND (STR_TO_DATE('2023-11-01','%Y-%m-%d') + INTERVAL 60 DAY)) "
            "GROUP BY c.name ORDER BY total_revenue DESC"
        )
    elif args.workload == "tp" and args.tp_transaction_id == 17:
        statement = "SELECT balance,isblocked FROM savingAccount WHERE accountid=1"
    else:
        statement = "SELECT COUNT(*) FROM customer"
    proc = subprocess.run(
        ["mysql", "--protocol=tcp", f"--host={args.host}", f"--port={args.port}",
         f"--user={args.user}", f"--password={args.password}", "--batch", "--raw",
         "--skip-column-names", args.database, "-e", "EXPLAIN FORMAT=JSON " + statement],
        capture_output=True, text=True, timeout=30, check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else f"EXPLAIN unavailable: {(proc.stderr or proc.stdout)[-1000:].strip()}"


def _runtime_config(
    args: argparse.Namespace, target: Path, duration_sec: int, *, random_seed: int | None = None,
) -> None:
    values = _read_properties(args.base_config)
    jdbc = f"jdbc:mysql://{args.host}:{args.port}/{args.database}?useUnicode=true&characterEncoding=utf-8&useSSL=false&allowPublicKeyRetrieval=true"
    values.update({
        "db": "mysql", "classname": "com.mysql.cj.jdbc.Driver", "username": args.user, "password": args.password,
        "url": jdbc, "url_ap": jdbc, "classname_ap": "com.mysql.cj.jdbc.Driver",
        "username_ap": args.user, "password_ap": args.password,
        "apclient": str(args.ap_clients), "tpclient": str(args.tp_clients),
        "xapclient": str(args.xap_clients), "xtpclient": str(args.xtp_clients),
        "apRunSecs": str(duration_sec), "tpRunSecs": str(duration_sec), "xpRunSecs": str(duration_sec),
        "progress_windows": str(max(1, min(10, duration_sec))),
        "random_seed": str(args.random_seed if random_seed is None else random_seed),
    })
    if args.workload == "xp":
        # Native HyBench XP selects IQ1-IQ7 uniformly at random.  Strip custom
        # AP routing inherited from a base config so XP cannot silently become
        # the AP13 workload used by older experiments.
        for key in (
            "ap_query_ids", "ap_query_weights", "ap_query_sequence",
            "ap_query_sequence_limit",
        ):
            values.pop(key, None)
    elif args.ap_query_sequence:
        values["ap_query_sequence"] = args.ap_query_sequence
        # Zero means repeat the sequence throughout the measurement window.
        values["ap_query_sequence_limit"] = str(args.ap_query_sequence_limit)
    if args.tp_transaction_mix:
        for transaction_id in range(1, 19):
            values[f"tp{transaction_id}_percent"] = str(
                args.tp_transaction_mix.get(transaction_id, 0)
            )
    elif args.tp_transaction_id is not None:
        for transaction_id in range(1, 19):
            values[f"tp{transaction_id}_percent"] = (
                "100" if transaction_id == args.tp_transaction_id else "0"
            )
    _write_properties(target, values)


def measure(args: argparse.Namespace) -> Dict[str, Any]:
    args.output_root.mkdir(parents=True, exist_ok=True)
    run_nonce = time.time_ns()
    run_dir = args.output_root / f"hybench_{args.workload}_{time.strftime('%Y%m%d_%H%M%S')}_{run_nonce}"
    run_dir.mkdir(parents=True)
    if args.vary_random_seed:
        warmup_seed = (int(args.random_seed) + run_nonce % 2_000_000_000) % 2_147_483_647
        measured_seed = (warmup_seed + 104729) % 2_147_483_647
    else:
        warmup_seed = measured_seed = int(args.random_seed)

    if args.prewarm_sql:
        prewarm = subprocess.run(
            [
                "mysql", "--protocol=tcp", f"--host={args.host}",
                f"--port={args.port}", f"--user={args.user}",
                f"--password={args.password}", args.database,
                "-e", "SET SESSION MAX_EXECUTION_TIME=0; " + args.prewarm_sql,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=float(args.prewarm_timeout_sec),
        )
        if prewarm.returncode != 0:
            detail = (prewarm.stderr or prewarm.stdout or "HyBench prewarm failed").strip()
            raise RuntimeError(f"HyBench prewarm failed: {detail[-2000:]}")

    def command(config: Path, raw_log: Path, duration: int) -> list[str]:
        worker_timeout = (
            int(args.hard_timeout_sec)
            if int(args.hard_timeout_sec) > 0
            else max(args.timeout_sec, duration + 90)
        )
        return [
            sys.executable, str(Path(__file__).resolve()), "--worker", "--workload", args.workload,
            "--runtime-root", str(args.runtime_root), "--runtime-config", str(config),
            "--jar", str(args.jar), "--statements", str(args.statements), "--raw-log", str(raw_log),
            "--connector-jar", str(args.connector_jar),
            "--duration-sec", str(duration), "--timeout-sec", str(worker_timeout),
            "--port", str(args.port),
        ]

    if args.warmup_duration_sec > 0:
        warmup_config = run_dir / "warmup.props"
        _runtime_config(args, warmup_config, args.warmup_duration_sec, random_seed=warmup_seed)
        subprocess.run(command(warmup_config, run_dir / "warmup.log", args.warmup_duration_sec), check=True, stdout=subprocess.DEVNULL)
    measured_config = run_dir / "measured.props"
    raw_log = run_dir / "measured.log"
    _runtime_config(args, measured_config, args.duration_sec, random_seed=measured_seed)
    queries_before = _mysql_status_counter(args, "Queries")
    measured_started = time.monotonic()
    resource = run_and_monitor(
        command(measured_config, raw_log, args.duration_sec), pids=[args.mysqld_pid],
        interval_sec=args.interval_sec, warmup_samples=args.warmup_samples,
    )
    measured_elapsed = max(time.monotonic() - measured_started, 1e-9)
    queries_after = _mysql_status_counter(args, "Queries")
    db_qps = max(0.0, float(queries_after - queries_before - 1) / measured_elapsed)
    native_qps = float(resource.pop("qps", 0.0))
    native_query_count = float(resource.get("query_count", 0.0))
    if native_query_count > 0.0:
        # HyBench rounds its printed QPS to two decimals.  The count and the
        # configured interval preserve enough resolution to compare nearby
        # cost points and avoid manufacturing a five-percent step by rounding.
        qps = native_query_count / float(args.duration_sec)
        qps_source = "hybench_query_count"
    elif native_qps > 0.0:
        qps = native_qps
        qps_source = "hybench"
    else:
        qps = db_qps
        qps_source = "mysql_queries"
    p95 = float(resource.pop("latency_p95_ms", 0.0))
    p99 = float(resource.pop("latency_p99_ms", 0.0))
    explain = _explain(args)
    return {
        "resource": resource, "qps": qps, "query_count": qps * args.duration_sec, "query_time": float(args.duration_sec),
        "_stats": {"qps": {"avg": qps}, "query_count": {"avg": qps * args.duration_sec},
                   "query_time": {"avg": float(args.duration_sec)},
                   "benchmark": {"latency_p95": p95, "latency_p99": p99, "throughput": qps,
                                 "benchmark": f"hybench_{args.workload}"}},
        "_metadata": {"db_qps": db_qps, "qps_source": qps_source,
                      "measurement_wall_sec": measured_elapsed,
                      "latency_p95_ms": p95, "latency_p99_ms": p99,
                      "summary_path": str(raw_log.resolve()), "explain_samples": [explain],
                      "warmup_random_seed": warmup_seed, "measured_random_seed": measured_seed,
                      "vary_random_seed": bool(args.vary_random_seed),
                      "xp_ap_query_mode": (
                          "native_iq1_iq7_uniform_random" if args.workload == "xp" else None
                      ),
                      "ap_query_sequence": (
                          args.ap_query_sequence or None if args.workload == "ap" else None
                      ),
                      "ap_query_sequence_limit": (
                          args.ap_query_sequence_limit if args.workload == "ap" else None
                      ),
                      "tp_transaction_id": args.tp_transaction_id,
                      "tp_transaction_mix": args.tp_transaction_mix or None},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--workload", choices=("ap", "tp", "xp"), required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default='CHANGE_ME')
    parser.add_argument("--database", default="hybench")
    parser.add_argument("--base-config", type=Path, default=Path("config/hybench_mysql_prepare.props"))
    parser.add_argument("--runtime-root", type=Path, default=Path("/disk2/resource_adaptive_mysql/gd_20260718/hybench_runtime"))
    parser.add_argument("--runtime-config", type=Path)
    parser.add_argument("--jar", type=Path, default=Path("/disk2/resource_adaptive_mysql/gd_20260718/hybench_client/target/HyBench-1.0-SNAPSHOT-jar-with-dependencies.jar"))
    parser.add_argument("--connector-jar", type=Path, default=Path("/home/anonymous/benchbase-mysql/lib/mysql-connector-java-8.0.30.jar"))
    parser.add_argument("--statements", type=Path, default=Path("/home/anonymous/HyBench-2024-master/conf/stmt_mysql.toml"))
    parser.add_argument("--raw-log", type=Path)
    parser.add_argument("--duration-sec", type=int, default=20)
    parser.add_argument("--warmup-duration-sec", type=int, default=5)
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--hard-timeout-sec", type=int, default=0,
                        help="force worker timeout for bounded preflight warmup only")
    parser.add_argument("--ap-clients", type=int, default=2)
    parser.add_argument("--tp-clients", type=int, default=16)
    parser.add_argument("--xap-clients", type=int, default=2)
    parser.add_argument("--xtp-clients", type=int, default=16)
    parser.add_argument("--random-seed", type=int, default=20260718)
    parser.add_argument("--vary-random-seed", action="store_true")
    parser.add_argument("--ap-query-sequence", default=None)
    parser.add_argument("--ap-query-sequence-limit", type=int, default=0)
    parser.add_argument("--tp-transaction-id", type=int, default=None)
    parser.add_argument("--tp-transaction-mix", default=None)
    parser.add_argument("--prewarm-sql", default="")
    parser.add_argument("--prewarm-timeout-sec", type=int, default=600)
    parser.add_argument("--mysqld-pid", type=int)
    parser.add_argument("--interval-sec", type=float, default=1.0)
    parser.add_argument("--warmup-samples", type=int, default=1)
    parser.add_argument("--output-root", type=Path, default=Path("history/mysql/hybench_measurements"))
    args = parser.parse_args()
    if args.workload == "xp" and args.ap_query_sequence:
        parser.error(
            "HyBench XP uses native uniform-random IQ1-IQ7 selection; "
            "--ap-query-sequence is only valid for standalone AP"
        )
    if args.ap_query_sequence:
        try:
            query_ids = [int(item.strip()) for item in args.ap_query_sequence.split(",") if item.strip()]
        except ValueError:
            parser.error("--ap-query-sequence must be a comma-separated integer list")
        if not query_ids or any(item < 1 or item > 13 for item in query_ids):
            parser.error("--ap-query-sequence IDs must be in [1, 13]")
    if args.ap_query_sequence_limit < 0:
        parser.error("--ap-query-sequence-limit must be non-negative")
    if args.tp_transaction_id is not None and not 1 <= args.tp_transaction_id <= 18:
        parser.error("--tp-transaction-id must be in [1, 18]")
    if args.tp_transaction_mix:
        try:
            pairs = [item.split(":", 1) for item in args.tp_transaction_mix.split(",")]
            args.tp_transaction_mix = {int(key): int(value) for key, value in pairs}
        except (TypeError, ValueError):
            parser.error("--tp-transaction-mix must use ID:PERCENT comma pairs")
        if (
            any(key < 1 or key > 18 or value < 0 for key, value in args.tp_transaction_mix.items())
            or sum(args.tp_transaction_mix.values()) != 100
        ):
            parser.error("--tp-transaction-mix IDs must be in [1,18] and percentages sum to 100")
        if args.tp_transaction_id is not None:
            parser.error("--tp-transaction-id and --tp-transaction-mix are mutually exclusive")
    else:
        args.tp_transaction_mix = None
    if args.worker:
        if args.runtime_config is None or args.raw_log is None:
            parser.error("--runtime-config and --raw-log are required in worker mode")
        worker(args)
        return 0
    if not args.mysqld_pid:
        parser.error("--mysqld-pid is required")
    print("RESOURCE_METRICS=" + json.dumps(measure(args), ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
