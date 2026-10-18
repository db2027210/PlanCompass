#!/usr/bin/env python3
"""Run BenchBase or JOB and emit a normalized latency marker for monitoring."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set, Tuple
from urllib.parse import quote
from xml.etree import ElementTree as ET


MARKER = "RESOURCE_ADAPTIVE_BENCHMARK_JSON="
# Mean runtimes measured at the accepted warm-r0 planner parameters in the
# two-worker 2026-08-12 baseline.  Worker w2 completed all 113 queries in
# 979.5s and w1 completed 112 before the 1020s process timeout.  The failed
# wall-time contract is never training/accepted evidence, but its per-query
# timings are valid scheduler telemetry: the previous default-plan hints put
# 6d/6f/17d (about 526/519/497s here) near the tail while 10c, formerly ranked
# slow, now takes about 27s.  These hints affect scheduling only: every
# canonical SQL still executes exactly once, and all candidates use the same
# frozen deterministic order.  Longest-processing-time first avoids leaving
# multi-minute SQL at the tail of the shared worker queue.
JOB_HISTORICAL_RUNTIME_HINT_SECONDS = {
    "6d.sql": 526.189,
    "6f.sql": 518.887,
    "17d.sql": 496.962,
    "25c.sql": 385.759,
    "17b.sql": 380.114,
    "17c.sql": 380.112,
    "30c.sql": 300.028,
    "18a.sql": 285.163,
    "18c.sql": 284.820,
    "30a.sql": 257.119,
    "20a.sql": 251.516,
    "20c.sql": 239.328,
    "17f.sql": 241.192,
    "16b.sql": 241.176,
    "26c.sql": 198.544,
    "26a.sql": 176.695,
    "17e.sql": 151.290,
    "7c.sql": 136.949,
    "17a.sql": 133.323,
    "9b.sql": 106.947,
    "9d.sql": 93.435,
    "25a.sql": 81.097,
    "28a.sql": 71.256,
    "8a.sql": 68.041,
    "22c.sql": 56.266,
    "22d.sql": 56.265,
    "16c.sql": 53.301,
    "19c.sql": 52.152,
    "16d.sql": 42.147,
    "8c.sql": 39.815,
    "20b.sql": 38.432,
    "7a.sql": 29.002,
}
# Empirical conflict groups are narrower than the numeric JOB family.  r17
# and r19 showed that 17b/17c/17d mutually stretch to roughly 941--991s when
# dispatched together, while r18 showed that serializing every ``17*`` query
# creates a >1020s critical path.  Only the demonstrated conflicting variants
# receive a hard cap of one; all other long-query families keep the configured
# default cap.
JOB_LONG_QUERY_CONFLICT_GROUPS = {
    "job17_bcd": frozenset({"17b.sql", "17c.sql", "17d.sql"}),
}
JOB_QUERY_FILE_RE = re.compile(r"^[0-9]+[a-z]\.sql$", re.IGNORECASE)
TPCH_QUERY_NAMES = tuple(f"Q{index}" for index in range(1, 23))
TPCH_HISTORICAL_RUNTIME_HINT_SECONDS = {
    "Q18": 128.719,
    "Q4": 127.209,
    "Q9": 81.683,
    "Q5": 57.620,
    "Q20": 40.462,
    "Q8": 40.382,
    "Q6": 34.336,
    "Q7": 29.376,
    "Q14": 26.490,
    "Q12": 21.631,
    "Q1": 7.530,
    "Q19": 7.268,
    "Q17": 6.290,
    "Q21": 4.852,
    "Q10": 4.134,
    "Q11": 4.081,
    "Q3": 3.642,
    "Q13": 2.110,
    "Q15": 2.051,
    "Q2": 1.587,
    "Q16": 0.710,
    "Q22": 0.200,
}


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(float(value) for value in values)
    position = fraction * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def emit(benchmark: Dict[str, Any], latency: Dict[str, Any]) -> None:
    print(
        MARKER
        + json.dumps(
            {"benchmark": benchmark, "benchmark_latency": latency},
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


def job_query_complexity(path: Path) -> float:
    """Estimate relative JOB cost from joins, FROM width, nesting, and SQL size."""
    sql = path.read_text(encoding="utf-8")
    lowered = sql.lower()
    from_match = re.search(r"\bfrom\b(?P<body>.*?)\bwhere\b", lowered, re.DOTALL)
    relation_count = 1
    if from_match:
        relation_count += from_match.group("body").count(",")
    join_count = len(re.findall(r"\bjoin\b", lowered))
    nested_select_count = max(0, len(re.findall(r"\bselect\b", lowered)) - 1)
    return (
        relation_count * 10.0
        + join_count * 10.0
        + nested_select_count * 3.0
        + len(sql) / 1000.0
    )


def stagger_job_queries(
    sql_files: Sequence[Path],
    *,
    workers: int,
    long_query_fraction: float,
    max_concurrent_long_queries: int,
) -> Tuple[List[Path], Set[str]]:
    """Order JOB SQL by observed cost and identify every known long query.

    The long-query gate must cover both structurally complex SQL and queries
    that were empirically slow in prior complete canonical baselines.  The old
    capped path ignored the runtime hints, so several known multi-minute
    queries could run together even when ``max_concurrent_long_queries`` was
    two.  Scheduling is deterministic and never changes the query set.
    """
    ranked = sorted(sql_files, key=lambda path: (-job_query_complexity(path), path.name))
    long_count = max(1, min(len(ranked), math.ceil(len(ranked) * long_query_fraction)))
    structural_long_names = {path.name for path in ranked[:long_count]}
    hinted_names = {
        path.name
        for path in sql_files
        if path.name in JOB_HISTORICAL_RUNTIME_HINT_SECONDS
    }
    long_query_names = structural_long_names.union(hinted_names)
    scheduled = sorted(
        sql_files,
        key=lambda path: (
            path.name not in long_query_names,
            path.name not in JOB_HISTORICAL_RUNTIME_HINT_SECONDS,
            -JOB_HISTORICAL_RUNTIME_HINT_SECONDS.get(path.name, 0.0),
            -job_query_complexity(path),
            path.name,
        ),
    )
    return scheduled, long_query_names


def job_query_family(path: Path) -> str:
    """Return the stable numeric JOB query family."""
    family_match = re.match(r"(?P<family>[0-9]+)", path.stem)
    return family_match.group("family") if family_match else path.stem


def job_query_concurrency_group(path: Path) -> str:
    """Return an evidence-backed conflict group, falling back to family."""
    for group, members in JOB_LONG_QUERY_CONFLICT_GROUPS.items():
        if path.name in members:
            return group
    return f"family_{job_query_family(path)}"


def job_query_concurrency_cap(path: Path, default_family_cap: int) -> int:
    """Use cap=1 only for demonstrated conflicts; fail open elsewhere."""
    group = job_query_concurrency_group(path)
    return 1 if group in JOB_LONG_QUERY_CONFLICT_GROUPS else default_family_cap


def complementary_pairwise_order(paths: Sequence[Path]) -> List[Path]:
    """Swap adjacent LPT ranks without moving a query far from its rank.

    The two physical databases otherwise launch the exact same heavy query at
    the same time on the shared SSD.  One worker keeps the base LPT order and
    the other uses this deterministic adjacent swap.  Every query remains
    exactly once and a worker's order is frozen across all candidates.
    """
    ordered = list(paths)
    for index in range(0, len(ordered) - 1, 2):
        ordered[index], ordered[index + 1] = ordered[index + 1], ordered[index]
    return ordered


def next_eligible_job_task_index(
    tasks: Sequence[Tuple[int, int, Path]],
    *,
    long_query_names: Set[str],
    active_long_queries: int,
    max_concurrent_long_queries: int,
    active_long_query_families: Dict[str, int] | None = None,
    max_concurrent_long_queries_per_family: int = 1,
) -> Optional[int]:
    """Return the next task that satisfies global and conflict-group caps.

    Only empirically demonstrated conflicting variants receive a cap of one;
    other groups use the configured family cap.  Scheduling never removes,
    duplicates, or rewrites a query.
    """
    group_counts = active_long_query_families or {}
    for index, (_round_index, _query_index, path) in enumerate(tasks):
        if path.name not in long_query_names:
            return index
        group = job_query_concurrency_group(path)
        group_cap = job_query_concurrency_cap(
            path,
            max_concurrent_long_queries_per_family,
        )
        if (
            active_long_queries < max_concurrent_long_queries
            and group_counts.get(group, 0) < group_cap
        ):
            return index
    return None


def benchbase_summary(root: Path, output_lines: Sequence[str], start_time: float, benchmark: str) -> Path:
    pattern = re.compile(r"Output summary data into file:\s*(?P<path>\S+\.summary\.json)")
    for line in reversed(output_lines):
        match = pattern.search(line)
        if match:
            path = Path(match.group("path"))
            if not path.is_absolute():
                path = root / "results" / path.name
            if path.is_file():
                return path
    candidates = [
        path
        for path in (root / "results").glob(f"{benchmark}_*.summary.json")
        if path.stat().st_mtime >= start_time - 2.0
    ]
    if not candidates:
        raise RuntimeError("BenchBase completed without a new summary JSON")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def benchbase_query_counts(summary_path: Path) -> Dict[str, int]:
    """Return successful transaction counts from the raw BenchBase result."""
    suffix = ".summary.json"
    if not summary_path.name.endswith(suffix):
        raise RuntimeError(f"unexpected BenchBase summary name: {summary_path}")
    raw_path = summary_path.with_name(summary_path.name[:-len(suffix)] + ".raw.csv")
    if not raw_path.is_file():
        raise RuntimeError(f"BenchBase completed without raw query evidence: {raw_path}")

    counts: Dict[str, int] = {}
    with raw_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            query_name = str(row.get("Transaction Name") or "").strip()
            if query_name:
                counts[query_name] = counts.get(query_name, 0) + 1
    return counts


def benchbase_query_sequence(summary_path: Path) -> List[str]:
    """Return the native completion order from BenchBase raw evidence."""
    suffix = ".summary.json"
    if not summary_path.name.endswith(suffix):
        raise RuntimeError(f"unexpected BenchBase summary name: {summary_path}")
    raw_path = summary_path.with_name(summary_path.name[:-len(suffix)] + ".raw.csv")
    if not raw_path.is_file():
        raise RuntimeError(f"BenchBase completed without raw query evidence: {raw_path}")
    sequence: List[str] = []
    with raw_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            query_name = str(row.get("Transaction Name") or "").strip()
            if query_name:
                sequence.append(query_name)
    return sequence


def benchbase_raw_latencies_ms(summary_path: Path) -> List[float]:
    suffix = ".summary.json"
    raw_path = summary_path.with_name(summary_path.name[:-len(suffix)] + ".raw.csv")
    values: List[float] = []
    with raw_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            raw = row.get("Latency (microseconds)")
            if raw is not None:
                values.append(float(raw) / 1000.0)
    return values


def missing_tpch_queries(query_counts: Dict[str, int]) -> List[str]:
    """List canonical TPC-H AP queries without a successful completion."""
    return [name for name in TPCH_QUERY_NAMES if query_counts.get(name, 0) <= 0]


def partition_tpch_queries(worker_count: int) -> List[List[str]]:
    """Assign Q1--Q22 once using deterministic LPT bin packing."""
    if worker_count <= 0 or worker_count > len(TPCH_QUERY_NAMES):
        raise ValueError("TPC-H worker count must be in [1, 22]")
    lanes: List[List[str]] = [[] for _ in range(worker_count)]
    lane_loads = [0.0 for _ in range(worker_count)]
    ordered = sorted(
        TPCH_QUERY_NAMES,
        key=lambda name: (-TPCH_HISTORICAL_RUNTIME_HINT_SECONDS[name], name),
    )
    for query_name in ordered:
        lane_index = min(range(worker_count), key=lambda index: (lane_loads[index], index))
        lanes[lane_index].append(query_name)
        lane_loads[lane_index] += TPCH_HISTORICAL_RUNTIME_HINT_SECONDS[query_name]
    return lanes


def write_tpch_serial_lane_config(
    source: Path,
    destination: Path,
    query_names: Sequence[str],
) -> None:
    """Create one untimed BenchBase lane over a subset of native TPC-H procedures."""
    tree = ET.parse(source)
    root = tree.getroot()
    terminals = root.find("terminals")
    if terminals is None:
        raise RuntimeError("TPC-H config has no terminals element")
    terminals.text = "1"
    work = root.find("./works/work")
    if work is None:
        raise RuntimeError("TPC-H config has no work phase")
    serial = work.find("serial")
    if serial is None:
        serial = ET.SubElement(work, "serial")
    serial.text = "true"
    for tag in ("time", "warmup"):
        element = work.find(tag)
        if element is not None:
            work.remove(element)
    weights = work.find("weights")
    if weights is None:
        weights = ET.SubElement(work, "weights")
    weights.text = ",".join("1" for _ in query_names)

    registry = root.find("transactiontypes")
    if registry is None:
        raise RuntimeError("TPC-H config has no transaction registry")
    transactions = {
        str(transaction.findtext("name") or "").strip(): transaction
        for transaction in registry.findall("transactiontype")
    }
    missing = [name for name in query_names if name not in transactions]
    if missing:
        raise RuntimeError(f"TPC-H lane registry is missing queries: {missing}")
    for transaction in list(registry.findall("transactiontype")):
        registry.remove(transaction)
    for lane_transaction_id, name in enumerate(query_names, start=1):
        transaction = transactions[name]
        transaction_id = transaction.find("id")
        if transaction_id is None:
            raise RuntimeError(f"TPC-H transaction {name} has no id")
        # BenchBase's raw ResultWriter indexes a lane-local result array with
        # ``transaction_id - 1``.  After selecting a Q1--Q22 subset, retaining
        # the original global IDs (for example Q18 -> 18 in a one-item lane)
        # causes an IndexOutOfBoundsException after an otherwise successful
        # query.  IDs are lane-local metadata; renumbering does not alter the
        # native procedure selected by its transaction name.
        transaction_id.text = str(lane_transaction_id)
        registry.append(transaction)
    observed = [
        str(item.findtext("name") or "").strip()
        for item in registry.findall("transactiontype")
    ]
    if observed != list(query_names):
        raise RuntimeError(f"TPC-H lane registry mismatch: {observed}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tree.write(destination, encoding="UTF-8", xml_declaration=True)


def run_tpch_parallel_full_pass(args: argparse.Namespace) -> int:
    """Run native Q1--Q22 once across multiple concurrent BenchBase lanes."""
    lanes = partition_tpch_queries(args.workers)
    replay_root = args.runtime_root / f"parallel_{os.getpid()}_{time.time_ns()}"
    replay_root.mkdir(parents=True, exist_ok=False)
    workload_started = time.perf_counter()

    def run_lane(lane_index: int, query_names: Sequence[str]) -> Dict[str, Any]:
        lane_root = replay_root / f"lane_{lane_index + 1:02d}"
        lane_root.mkdir(parents=True, exist_ok=False)
        for name in ("benchbase.jar", "lib", "config"):
            source = args.source_root / name
            target = lane_root / name
            if not source.exists():
                raise RuntimeError(f"BenchBase source asset is missing: {source}")
            target.symlink_to(source, target_is_directory=source.is_dir())
        config = lane_root / "tpch_lane.xml"
        write_tpch_serial_lane_config(args.source_config, config, query_names)
        host, port, dbname, user, password = args.endpoint
        tree = ET.parse(config)
        root = tree.getroot()
        url = root.find("url")
        username = root.find("username")
        password_node = root.find("password")
        scale = root.find("scalefactor")
        if None in (url, username, password_node, scale):
            raise RuntimeError("TPC-H lane config is missing endpoint/scalefactor nodes")
        assert url is not None and username is not None and password_node is not None and scale is not None
        url.text = (
            f"jdbc:postgresql://{host}:{port}/{dbname}?sslmode=disable"
            f"&ApplicationName=resource-adaptive-tpch-lane-{lane_index + 1}"
        )
        username.text = user
        password_node.text = password
        scale.text = os.environ.get("BENCHBASE_SCALEFACTOR", "5")
        tree.write(config, encoding="UTF-8", xml_declaration=True)
        command = [
            "java",
            f"-Xmx{args.java_xmx}",
            "-jar",
            "benchbase.jar",
            "-b",
            "tpch",
            "-c",
            str(config),
            "--execute=true",
        ]
        started_at = time.time()
        process = subprocess.Popen(
            command,
            cwd=str(lane_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        lines: List[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line.rstrip("\n"))
            print(f"[tpch_lane_{lane_index + 1:02d}] {line}", end="", flush=True)
        returncode = process.wait()
        if returncode != 0:
            raise RuntimeError(f"TPC-H lane {lane_index + 1} exited {returncode}")
        summary_path = benchbase_summary(lane_root, lines, started_at, "tpch")
        counts = benchbase_query_counts(summary_path)
        unexpected = sorted(set(counts).difference(query_names))
        non_unit = sorted(name for name in query_names if counts.get(name, 0) != 1)
        if unexpected or non_unit:
            raise RuntimeError(
                f"TPC-H lane {lane_index + 1} coverage failed: "
                f"unexpected={unexpected}, non_unit={non_unit}"
            )
        return {
            "lane": lane_index + 1,
            "queries": list(query_names),
            "summary_path": str(summary_path),
            "latencies_ms": benchbase_raw_latencies_ms(summary_path),
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(run_lane, lane_index, query_names)
            for lane_index, query_names in enumerate(lanes)
        ]
        results = [future.result() for future in futures]

    wall_time_s = time.perf_counter() - workload_started
    completed_queries = [
        query_name
        for result in results
        for query_name in result["queries"]
    ]
    counts = {name: completed_queries.count(name) for name in TPCH_QUERY_NAMES}
    if len(completed_queries) != 22 or any(count != 1 for count in counts.values()):
        raise RuntimeError(f"TPC-H parallel full-pass coverage failed: {counts}")
    latencies_ms = [
        latency
        for result in results
        for latency in result["latencies_ms"]
    ]
    throughput_qps = len(TPCH_QUERY_NAMES) / max(wall_time_s, 1e-9)
    emit(
        {
            "throughput_qps": throughput_qps,
            "goodput_qps": throughput_qps,
            "query_count": len(completed_queries),
            "query_counts": counts,
            "all_ap_queries_completed": True,
            "exactly_once": True,
            "execution_mode": "native_tpch_parallel_lpt_q1_q22_exactly_once",
            "worker_count": args.workers,
            "lane_assignments": [result["queries"] for result in results],
            "summary_paths": [result["summary_path"] for result in results],
            "wall_time_s": wall_time_s,
            "target_wall_time_s": args.target_wall_time_s,
            "completed_within_target_wall_time": bool(
                args.target_wall_time_s <= 0.0 or wall_time_s <= args.target_wall_time_s
            ),
        },
        {
            "all_p90_avg": percentile(latencies_ms, 0.90),
            "all_p90_max": percentile(latencies_ms, 0.90),
            "all_p95_avg": percentile(latencies_ms, 0.95),
            "all_p95_max": percentile(latencies_ms, 0.95),
            "all_p99_avg": percentile(latencies_ms, 0.99),
            "all_p99_max": percentile(latencies_ms, 0.99),
        },
    )
    return 0


def run_benchbase(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    command = [
        "java",
        f"-Xmx{args.java_xmx}",
        "-jar",
        "benchbase.jar",
        "-b",
        args.benchmark,
        "-c",
        str(args.config.resolve()),
    ]
    if args.create_load:
        command.extend(["--create=true", "--load=true"])
    command.append("--execute=true")
    start_time = time.time()
    process = subprocess.Popen(
        command,
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: List[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        lines.append(line.rstrip("\n"))
        print(line, end="", flush=True)
    returncode = process.wait()
    if returncode != 0:
        return returncode
    summary_path = benchbase_summary(root, lines, start_time, args.benchmark)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    query_counts = benchbase_query_counts(summary_path)
    query_sequence = benchbase_query_sequence(summary_path)
    missing_queries: List[str] = []
    if args.benchmark == "tpch":
        missing_queries = missing_tpch_queries(query_counts)
        non_unit_counts = [
            query_name
            for query_name in TPCH_QUERY_NAMES
            if query_counts.get(query_name, 0) != 1
        ]
        if missing_queries or non_unit_counts or query_sequence != list(TPCH_QUERY_NAMES):
            raise RuntimeError(
                "TPC-H full serial contract failed: "
                f"missing={missing_queries}, non_unit_counts={non_unit_counts}, "
                f"observed_sequence={query_sequence}"
            )
    distribution = summary.get("Latency Distribution") or {}
    p90 = float(distribution["90th Percentile Latency (microseconds)"]) / 1000.0
    p95 = float(distribution["95th Percentile Latency (microseconds)"]) / 1000.0
    p99 = float(distribution["99th Percentile Latency (microseconds)"]) / 1000.0
    emit(
        {
            "throughput_qps": summary.get("Throughput (requests/second)"),
            "goodput_qps": summary.get("Goodput (requests/second)"),
            "summary_path": str(summary_path),
            "query_counts": query_counts,
            "all_ap_queries_completed": not missing_queries,
            "query_count": len(query_sequence),
            "query_sequence": query_sequence,
            "expected_query_sequence": list(TPCH_QUERY_NAMES) if args.benchmark == "tpch" else [],
            "exactly_once": bool(
                args.benchmark != "tpch"
                or query_sequence == list(TPCH_QUERY_NAMES)
            ),
            "execution_mode": (
                "native_tpch_single_process_single_terminal_serial_q1_q22_once"
                if args.benchmark == "tpch"
                else "native_benchbase"
            ),
        },
        {
            "all_p90_avg": p90,
            "all_p90_max": p90,
            "all_p95_avg": p95,
            "all_p95_max": p95,
            "all_p99_avg": p99,
            "all_p99_max": p99,
        },
    )
    return 0


def run_job(args: argparse.Namespace) -> int:
    # The JOB repository also contains schema/index helper SQL.  Replaying
    # every ``*.sql`` would mutate the database during a benchmark and make
    # latency/QPS incomparable.  JOB query files use names such as 1a.sql and
    # 33c.sql, so keep only that explicit query family.
    sql_files = sorted(
        path
        for path in args.sql_dir.glob("*.sql")
        if JOB_QUERY_FILE_RE.fullmatch(path.name)
    )
    if not sql_files:
        raise RuntimeError(f"no JOB SQL files found under {args.sql_dir}")
    if len(sql_files) != args.expected_query_count:
        raise RuntimeError(
            "JOB canonical query-count contract failed: "
            f"expected={args.expected_query_count}, actual={len(sql_files)}"
        )
    scheduled_files, long_query_names = stagger_job_queries(
        sql_files,
        workers=args.workers,
        long_query_fraction=args.long_query_fraction,
        max_concurrent_long_queries=args.max_concurrent_long_queries,
    )
    schedule_variant = str(getattr(args, "worker_schedule_variant", "base"))
    if schedule_variant == "pairwise_swap":
        scheduled_files = complementary_pairwise_order(scheduled_files)
    elif schedule_variant != "base":
        raise RuntimeError(f"unsupported JOB worker schedule variant: {schedule_variant}")
    hinted_names = {
        path.name
        for path in sql_files
        if path.name in JOB_HISTORICAL_RUNTIME_HINT_SECONDS
    }
    tasks = [
        (round_index, query_index, path)
        for round_index in range(args.rounds)
        for query_index, path in enumerate(scheduled_files, start=1)
    ]
    concurrency_lock = threading.Lock()
    active_queries = 0
    active_long_queries = 0
    peak_concurrency = 0
    peak_long_query_concurrency = 0
    max_long_per_family = max(
        1,
        int(getattr(args, "max_concurrent_long_queries_per_family", 1)),
    )

    def execute_one(task: Any) -> Any:
        nonlocal active_queries
        nonlocal active_long_queries
        nonlocal peak_concurrency
        nonlocal peak_long_query_concurrency
        round_index, query_index, path = task
        started = time.perf_counter()
        session_settings = f"SET statement_timeout='{args.statement_timeout_ms}ms'"
        if args.work_mem_mb > 0:
            session_settings += f"; SET work_mem='{args.work_mem_mb}MB'"
        command = [
            "psql",
            args.dsn,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            session_settings,
            "-f",
            str(path),
        ]
        is_long_query = path.name in long_query_names
        with concurrency_lock:
            active_queries += 1
            peak_concurrency = max(peak_concurrency, active_queries)
            if is_long_query:
                active_long_queries += 1
                peak_long_query_concurrency = max(
                    peak_long_query_concurrency,
                    active_long_queries,
                )
        try:
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=max(1.0, args.statement_timeout_ms / 1000.0 + 30.0),
                check=False,
            )
        finally:
            with concurrency_lock:
                active_queries -= 1
                if is_long_query:
                    active_long_queries -= 1
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return round_index, query_index, path, completed, elapsed_ms

    latencies_ms: List[float] = []
    first_failure = 0
    workload_started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        pending = list(tasks)
        running: Dict[
            concurrent.futures.Future[Any],
            Tuple[bool, str | None, int, str],
        ] = {}
        submitted_long_queries = 0
        submitted_long_query_families: Dict[str, int] = {}
        peak_long_query_family_concurrency = 0
        while pending or running:
            while pending and len(running) < args.workers:
                task_index = next_eligible_job_task_index(
                    pending,
                    long_query_names=long_query_names,
                    active_long_queries=submitted_long_queries,
                    max_concurrent_long_queries=args.max_concurrent_long_queries,
                    active_long_query_families=submitted_long_query_families,
                    max_concurrent_long_queries_per_family=max_long_per_family,
                )
                if task_index is None:
                    break
                task = pending.pop(task_index)
                is_long_query = task[2].name in long_query_names
                concurrency_group = job_query_concurrency_group(task[2])
                future = executor.submit(execute_one, task)
                running[future] = (
                    is_long_query,
                    concurrency_group if is_long_query else None,
                    int(task[1]),
                    task[2].name,
                )
                if is_long_query:
                    submitted_long_queries += 1
                    submitted_long_query_families[concurrency_group] = (
                        submitted_long_query_families.get(concurrency_group, 0) + 1
                    )
                    peak_long_query_family_concurrency = max(
                        peak_long_query_family_concurrency,
                        submitted_long_query_families[concurrency_group],
                    )
                print(
                    "RESOURCE_ADAPTIVE_JOB_QUERY_EVENT="
                    + json.dumps(
                        {
                            "event": "JOB_QUERY_DISPATCH",
                            "query_index": int(task[1]),
                            "file": task[2].name,
                            "family": job_query_family(task[2]),
                            "concurrency_group": concurrency_group,
                            "concurrency_group_cap": job_query_concurrency_cap(
                                task[2], max_long_per_family
                            ) if is_long_query else None,
                            "is_long_query": is_long_query,
                            "active_task_count": len(running),
                            "active_long_query_count": submitted_long_queries,
                            "schedule_variant": schedule_variant,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            if not running:
                raise RuntimeError("JOB scheduler could not dispatch an eligible task")
            completed_futures, _ = concurrent.futures.wait(
                running,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in completed_futures:
                was_long_query, concurrency_group, dispatched_index, dispatched_file = (
                    running.pop(future)
                )
                if was_long_query:
                    submitted_long_queries -= 1
                    assert concurrency_group is not None
                    remaining_family = (
                        submitted_long_query_families.get(concurrency_group, 0) - 1
                    )
                    if remaining_family > 0:
                        submitted_long_query_families[concurrency_group] = remaining_family
                    else:
                        submitted_long_query_families.pop(concurrency_group, None)
                round_index, query_index, path, completed, elapsed_ms = future.result()
                latencies_ms.append(elapsed_ms)
                print(
                    f"JOB round={round_index + 1} query={query_index}/{len(sql_files)} "
                    f"file={path.name} elapsed_ms={elapsed_ms:.3f} returncode={completed.returncode}",
                    flush=True,
                )
                print(
                    "RESOURCE_ADAPTIVE_JOB_QUERY_EVENT="
                    + json.dumps(
                        {
                            "event": "JOB_QUERY_COMPLETE",
                            "query_index": dispatched_index,
                            "file": dispatched_file,
                            "family": job_query_family(path),
                            "concurrency_group": job_query_concurrency_group(path),
                            "is_long_query": was_long_query,
                            "elapsed_ms": elapsed_ms,
                            "returncode": completed.returncode,
                            "schedule_variant": schedule_variant,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                if completed.returncode != 0:
                    print(completed.stdout[-4000:], flush=True)
                    first_failure = first_failure or completed.returncode
    wall_time_s = time.perf_counter() - workload_started
    if first_failure:
        return first_failure
    p90 = percentile(latencies_ms, 0.90)
    p95 = percentile(latencies_ms, 0.95)
    p99 = percentile(latencies_ms, 0.99)
    emit(
        {
            "query_count": len(latencies_ms),
            "query_counts": {
                path.stem: args.rounds
                for path in sql_files
            },
            "all_ap_queries_completed": len(latencies_ms) == len(tasks),
            "expected_query_count": args.expected_query_count,
            "canonical_query_names": [path.stem for path in sql_files],
            "exactly_once": bool(
                args.rounds == 1
                and len(latencies_ms) == args.expected_query_count
            ),
            "execution_mode": "canonical_job_113_concurrent_staggered",
            "shared_database": True,
            "task_dispatch": "single_shared_queue",
            "wall_time_s": wall_time_s,
            "target_wall_time_s": args.target_wall_time_s,
            "completed_within_target_wall_time": bool(
                args.target_wall_time_s <= 0.0
                or wall_time_s <= args.target_wall_time_s
            ),
            "scheduling": {
                "mode": (
                    "historical_runtime_lpt_shared_queue"
                    if args.max_concurrent_long_queries >= args.workers
                    else "historical_runtime_lpt_nonblocking_long_cap"
                ),
                "workers": args.workers,
                "long_queries": sorted(long_query_names),
                "max_concurrent_long_queries": args.max_concurrent_long_queries,
                "observed_peak_concurrency": peak_concurrency,
                "observed_peak_long_query_concurrency": peak_long_query_concurrency,
                "max_concurrent_long_queries_per_family": max_long_per_family,
                "observed_peak_long_query_family_concurrency": (
                    peak_long_query_family_concurrency
                ),
                "conflict_group_caps": {
                    group: 1 for group in sorted(JOB_LONG_QUERY_CONFLICT_GROUPS)
                },
                "worker_schedule_variant": schedule_variant,
                "runtime_hint_source": (
                    "20260812_warm_r0_scheduler_telemetry_mean_elapsed_v1"
                ),
                "runtime_hint_query_count": (
                    len(hinted_names)
                ),
            },
        },
        {
            "all_p90_avg": p90,
            "all_p90_max": p90,
            "all_p95_avg": p95,
            "all_p95_max": p95,
            "all_p99_avg": p99,
            "all_p99_max": p99,
        },
    )
    return 0


def read_props(path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def postgres_endpoint_from_props(props: Dict[str, str]) -> Tuple[str, str, str, str, str]:
    url = props.get("url") or props.get("url_tp") or props.get("url_ap") or ""
    match = re.search(r"jdbc:postgresql://([^/:?]+):(\d+)/([^?]+)", url)
    if not match:
        raise RuntimeError(f"cannot parse PostgreSQL JDBC URL: {url!r}")
    return (
        match.group(1),
        match.group(2),
        match.group(3),
        props.get("username") or props.get("username_ap") or "postgres",
        props.get("password") or props.get("password_ap") or "",
    )


def isolated_benchbase_runtime(port: str) -> Tuple[Path, Path]:
    source_root = Path(
        os.environ.get("BENCHBASE_SOURCE_ROOT") or "/home/anonymous/benchbase-postgres"
    ).resolve()
    runtime_parent = Path(
        os.environ.get("BENCHBASE_RUNTIME_ROOT") or "/tmp/resource_adaptive_benchbase_runtime"
    ).resolve()
    runtime_root = runtime_parent / f"pg_{port}"
    runtime_root.mkdir(parents=True, exist_ok=True)
    for name in ("benchbase.jar", "lib", "config"):
        source = source_root / name
        target = runtime_root / name
        if not source.exists():
            raise RuntimeError(f"BenchBase source asset is missing: {source}")
        if target.exists() or target.is_symlink():
            continue
        target.symlink_to(source, target_is_directory=source.is_dir())
    return runtime_root, source_root


def write_tpch_full_serial_config(
    source: Path,
    destination: Path,
    *,
    endpoint: Tuple[str, str, str, str, str],
) -> None:
    write_tpch_serial_lane_config(source, destination, TPCH_QUERY_NAMES)
    host, port, dbname, user, password = endpoint
    tree = ET.parse(destination)
    root = tree.getroot()
    url = root.find("url")
    username = root.find("username")
    password_node = root.find("password")
    scale = root.find("scalefactor")
    if None in (url, username, password_node, scale):
        raise RuntimeError("TPC-H config is missing connection/scalefactor nodes")
    assert url is not None and username is not None and password_node is not None and scale is not None
    url.text = (
        f"jdbc:postgresql://{host}:{port}/{dbname}?sslmode=disable"
        "&ApplicationName=resource-adaptive-tpch-full"
    )
    username.text = user
    password_node.text = password
    scale.text = os.environ.get("BENCHBASE_SCALEFACTOR", "5")
    tree.write(destination, encoding="UTF-8", xml_declaration=True)


def run_full_workload_shim(args: argparse.Namespace) -> int:
    props = read_props(args.props.resolve())
    endpoint = postgres_endpoint_from_props(props)
    workload = str(os.environ.get("GENERIC_WORKLOAD") or "").strip().lower()
    if workload == "job":
        host, port, dbname, user, password = endpoint
        dsn = (
            f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@"
            f"{host}:{port}/{quote(dbname, safe='')}"
        )
        return run_job(
            argparse.Namespace(
                dsn=dsn,
                sql_dir=Path(
                    os.environ.get("JOB_SQL_DIR")
                    or "/home/anonymous/dataset/pg_data/join-order-benchmark"
                ),
                rounds=1,
                statement_timeout_ms=int(
                    os.environ.get("JOB_STATEMENT_TIMEOUT_MS", "1800000")
                ),
                work_mem_mb=0,
                workers=int(os.environ.get("JOB_WORKERS", "4")),
                max_concurrent_long_queries=int(
                    os.environ.get("JOB_MAX_CONCURRENT_LONG_QUERIES", "1")
                ),
                max_concurrent_long_queries_per_family=int(
                    os.environ.get(
                        "JOB_MAX_CONCURRENT_LONG_QUERIES_PER_FAMILY",
                        "1",
                    )
                ),
                long_query_fraction=float(
                    os.environ.get("JOB_LONG_QUERY_FRACTION", "0.2")
                ),
                expected_query_count=113,
                target_wall_time_s=float(
                    os.environ.get("JOB_TARGET_WALL_TIME_S", "0")
                ),
                worker_schedule_variant=(
                    "pairwise_swap" if int(port) % 2 == 0 else "base"
                ),
            )
        )
    if workload == "tpch":
        _host, port, _dbname, _user, _password = endpoint
        runtime_root, source_root = isolated_benchbase_runtime(port)
        source_config = Path(
            os.environ.get("BENCHBASE_XML_TEMPLATE")
            or source_root / "config/postgres/sample_tpch_config.xml"
        )
        java_xmx = str(os.environ.get("BENCHBASE_JAVA_XMX", "2g"))
        if java_xmx.startswith("-Xmx"):
            java_xmx = java_xmx[4:]
        return run_tpch_parallel_full_pass(
            argparse.Namespace(
                runtime_root=runtime_root,
                source_root=source_root,
                source_config=source_config,
                endpoint=endpoint,
                java_xmx=java_xmx,
                workers=int(os.environ.get("BENCHBASE_TPCH_WORKERS", "5")),
                target_wall_time_s=float(
                    os.environ.get("TPCH_TARGET_WALL_TIME_S", "900")
                ),
            )
        )
    raise RuntimeError(f"unsupported GENERIC_WORKLOAD={workload!r}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    benchbase = subparsers.add_parser("benchbase")
    benchbase.add_argument("--root", type=Path, required=True)
    benchbase.add_argument("--benchmark", choices=("tpcc", "tpch", "chbenchmark"), required=True)
    benchbase.add_argument("--config", type=Path, required=True)
    benchbase.add_argument("--java-xmx", default="8g")
    benchbase.add_argument("--create-load", action="store_true")
    job = subparsers.add_parser("job")
    job.add_argument("--dsn", required=True)
    job.add_argument("--sql-dir", type=Path, required=True)
    job.add_argument("--rounds", type=int, default=1)
    job.add_argument("--statement-timeout-ms", type=int, default=300000)
    job.add_argument("--work-mem-mb", type=int, default=0)
    job.add_argument("--workers", type=int, default=1)
    job.add_argument("--max-concurrent-long-queries", type=int, default=1)
    job.add_argument(
        "--max-concurrent-long-queries-per-family",
        type=int,
        default=1,
    )
    job.add_argument("--long-query-fraction", type=float, default=0.2)
    job.add_argument("--expected-query-count", type=int, default=113)
    job.add_argument("--target-wall-time-s", type=float, default=0.0)
    job.add_argument(
        "--worker-schedule-variant",
        choices=("base", "pairwise_swap"),
        default="base",
    )
    shim = subparsers.add_parser("shim")
    shim.add_argument("-t", "--mode", default="runxp")
    shim.add_argument("-c", "--props", type=Path, required=True)
    shim.add_argument("-f", "--statement-file", default="")
    args = parser.parse_args()
    if args.mode == "job" and (
        args.rounds <= 0
        or args.statement_timeout_ms <= 0
        or args.work_mem_mb < 0
        or args.workers <= 0
        or args.max_concurrent_long_queries <= 0
        or args.max_concurrent_long_queries > args.workers
        or args.max_concurrent_long_queries_per_family <= 0
        or args.max_concurrent_long_queries_per_family
        > args.max_concurrent_long_queries
        or not 0.0 < args.long_query_fraction <= 1.0
        or args.expected_query_count <= 0
        or args.target_wall_time_s < 0.0
    ):
        parser.error(
            "JOB rounds/timeout/workers must be positive, work_mem non-negative, "
            "long-query concurrency within workers, and long-query fraction in (0, 1]"
        )
    return args


def main() -> int:
    args = parse_args()
    if args.mode == "benchbase":
        return run_benchbase(args)
    if args.mode == "job":
        return run_job(args)
    return run_full_workload_shim(args)


if __name__ == "__main__":
    raise SystemExit(main())
