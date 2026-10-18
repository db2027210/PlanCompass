#!/usr/bin/env python3
"""Run canonical JOB with a predeclared deterministic worker-lane schedule."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set, Tuple

import run_benchmark_capture as base


SCHEDULE_CONTRACT = "canonical_job_113_fixed_worker_lanes_v1"


def _schedule_weight(path: Path) -> float:
    """Return a stable positive LPT weight for deterministic lane balancing."""
    historical = base.JOB_HISTORICAL_RUNTIME_HINT_SECONDS.get(path.name)
    if historical is not None:
        return float(historical)
    return max(0.001, base.job_query_complexity(path) / 10.0)


def build_fixed_schedule(
    sql_dir: Path,
    *,
    workers: int,
    long_query_fraction: float,
    max_concurrent_long_queries: int,
) -> Dict[str, Any]:
    """Build the exact per-worker sequence shared by every JOB Docker."""
    sql_files = sorted(
        path
        for path in sql_dir.glob("*.sql")
        if base.JOB_QUERY_FILE_RE.fullmatch(path.name)
    )
    if len(sql_files) != 113:
        raise RuntimeError(
            f"JOB fixed schedule requires 113 queries, found {len(sql_files)}"
        )
    if workers <= 0:
        raise ValueError("workers must be positive")
    if max_concurrent_long_queries != 1:
        raise RuntimeError(
            "fixed JOB schedule currently requires max_concurrent_long_queries=1"
        )
    scheduled, long_query_names = base.stagger_job_queries(
        sql_files,
        workers=workers,
        long_query_fraction=long_query_fraction,
        max_concurrent_long_queries=max_concurrent_long_queries,
    )
    lanes: List[List[Path]] = [[] for _ in range(workers)]
    lane_weights = [0.0 for _ in range(workers)]
    for path in scheduled:
        lane_index = min(range(workers), key=lambda index: (lane_weights[index], index))
        lanes[lane_index].append(path)
        lane_weights[lane_index] += _schedule_weight(path)
    manifest: Dict[str, Any] = {
        "contract": SCHEDULE_CONTRACT,
        "worker_count": workers,
        "worker_schedule_variant": "base",
        "worker_assignment_fixed": True,
        "lane_order_fixed": True,
        "global_completion_order_fixed": False,
        "max_concurrent_long_queries": max_concurrent_long_queries,
        "long_query_fraction": long_query_fraction,
        "lane_assignments": [
            [path.name for path in lane]
            for lane in lanes
        ],
        "global_lpt_order": [path.name for path in scheduled],
        "lane_estimated_seconds": lane_weights,
        "long_queries": sorted(long_query_names),
        "query_count": sum(len(lane) for lane in lanes),
        "query_set": sorted(path.name for path in sql_files),
    }
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest["schedule_sha256"] = hashlib.sha256(payload).hexdigest()
    return manifest


def load_authoritative_schedule(
    path: Path,
    *,
    sql_dir: Path,
    workers: int,
    long_query_fraction: float,
    max_concurrent_long_queries: int,
) -> Dict[str, Any]:
    """Load and cryptographically verify a campaign-frozen JOB schedule."""
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict):
        raise RuntimeError("JOB fixed schedule manifest must be a JSON object")
    expected_sha256 = str(manifest.get("schedule_sha256") or "")
    unsigned = dict(manifest)
    unsigned.pop("schedule_sha256", None)
    payload = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    observed_sha256 = hashlib.sha256(payload).hexdigest()
    if not expected_sha256 or observed_sha256 != expected_sha256:
        raise RuntimeError(
            "JOB frozen schedule manifest SHA mismatch: "
            f"expected={expected_sha256} observed={observed_sha256}"
        )
    if str(manifest.get("contract") or "") != SCHEDULE_CONTRACT:
        raise RuntimeError("JOB frozen schedule contract mismatch")
    if int(manifest.get("worker_count") or 0) != workers:
        raise RuntimeError("JOB frozen schedule worker count mismatch")
    if int(manifest.get("max_concurrent_long_queries") or 0) != max_concurrent_long_queries:
        raise RuntimeError("JOB frozen schedule long-query concurrency mismatch")
    if abs(float(manifest.get("long_query_fraction") or 0.0) - long_query_fraction) > 1e-12:
        raise RuntimeError("JOB frozen schedule long-query fraction mismatch")
    current_query_set = sorted(
        path.name
        for path in sql_dir.glob("*.sql")
        if base.JOB_QUERY_FILE_RE.fullmatch(path.name)
    )
    if current_query_set != list(manifest.get("query_set") or []):
        raise RuntimeError("JOB frozen schedule query set mismatch")
    lane_assignments = manifest.get("lane_assignments") or []
    if len(lane_assignments) != workers:
        raise RuntimeError("JOB frozen schedule lane count mismatch")
    flattened = [name for lane in lane_assignments for name in lane]
    if len(flattened) != 113 or sorted(flattened) != current_query_set:
        raise RuntimeError("JOB frozen schedule must cover all 113 queries exactly once")
    return manifest


def run_job_fixed(args: Any) -> int:
    """Execute one complete JOB replay using fixed per-worker query lanes."""
    if int(args.rounds) != 1:
        raise RuntimeError("fixed JOB physical replay requires exactly one round")
    sql_dir = Path(args.sql_dir)
    frozen_manifest_path = str(
        os.environ.get("JOB_FIXED_SCHEDULE_MANIFEST") or ""
    ).strip()
    if frozen_manifest_path:
        manifest = load_authoritative_schedule(
            Path(frozen_manifest_path),
            sql_dir=sql_dir,
            workers=int(args.workers),
            long_query_fraction=float(args.long_query_fraction),
            max_concurrent_long_queries=int(args.max_concurrent_long_queries),
        )
    else:
        manifest = build_fixed_schedule(
            sql_dir,
            workers=int(args.workers),
            long_query_fraction=float(args.long_query_fraction),
            max_concurrent_long_queries=int(args.max_concurrent_long_queries),
        )
    expected_sha256 = str(os.environ.get("JOB_FIXED_SCHEDULE_SHA256") or "").strip()
    observed_sha256 = str(manifest["schedule_sha256"])
    if not expected_sha256:
        raise RuntimeError("JOB_FIXED_SCHEDULE_SHA256 is required")
    if observed_sha256 != expected_sha256:
        raise RuntimeError(
            "JOB fixed schedule SHA mismatch: "
            f"expected={expected_sha256} observed={observed_sha256}"
        )

    path_by_name = {
        path.name: path
        for path in sql_dir.glob("*.sql")
        if base.JOB_QUERY_FILE_RE.fullmatch(path.name)
    }
    lanes = [
        [path_by_name[name] for name in names]
        for names in manifest["lane_assignments"]
    ]
    long_query_names: Set[str] = set(manifest["long_queries"])
    canonical_index = {
        name: index
        for index, name in enumerate(
            manifest["global_lpt_order"],
            start=1,
        )
    }
    long_ticket = {
        path.name: ticket
        for ticket, path in enumerate(
            sorted(
                (path for lane in lanes for path in lane if path.name in long_query_names),
                key=lambda path: canonical_index[path.name],
            )
        )
    }

    active_lock = threading.Lock()
    long_condition = threading.Condition()
    active_queries = 0
    peak_concurrency = 0
    active_long_queries = 0
    peak_long_query_concurrency = 0
    next_long_ticket = 0

    def execute_one(
        round_index: int,
        lane_index: int,
        lane_position: int,
        path: Path,
    ) -> Tuple[int, int, int, Path, subprocess.CompletedProcess[str], float]:
        nonlocal active_queries
        nonlocal peak_concurrency
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
        with active_lock:
            active_queries += 1
            peak_concurrency = max(peak_concurrency, active_queries)
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
            with active_lock:
                active_queries -= 1
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return round_index, lane_index, lane_position, path, completed, elapsed_ms

    def run_lane(lane_index: int, lane: Sequence[Path]) -> List[Dict[str, Any]]:
        nonlocal active_long_queries
        nonlocal peak_long_query_concurrency
        nonlocal next_long_ticket
        results: List[Dict[str, Any]] = []
        for round_index in range(args.rounds):
            for lane_position, path in enumerate(lane, start=1):
                is_long_query = path.name in long_query_names
                ticket = long_ticket.get(path.name)
                if is_long_query:
                    assert ticket is not None
                    with long_condition:
                        while ticket != next_long_ticket:
                            long_condition.wait()
                        active_long_queries += 1
                        peak_long_query_concurrency = max(
                            peak_long_query_concurrency,
                            active_long_queries,
                        )
                print(
                    "RESOURCE_ADAPTIVE_JOB_QUERY_EVENT="
                    + json.dumps(
                        {
                            "event": "JOB_QUERY_DISPATCH",
                            "round": round_index + 1,
                            "lane_index": lane_index + 1,
                            "lane_position": lane_position,
                            "query_index": canonical_index[path.name],
                            "file": path.name,
                            "family": base.job_query_family(path),
                            "is_long_query": is_long_query,
                            "long_ticket": ticket,
                            "schedule_contract": SCHEDULE_CONTRACT,
                            "schedule_sha256": observed_sha256,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                result = execute_one(
                    round_index,
                    lane_index,
                    lane_position,
                    path,
                )
                if is_long_query:
                    with long_condition:
                        active_long_queries -= 1
                        next_long_ticket += 1
                        long_condition.notify_all()
                _, _, _, _, completed, elapsed_ms = result
                print(
                    f"JOB round={round_index + 1} "
                    f"query={canonical_index[path.name]}/113 file={path.name} "
                    f"elapsed_ms={elapsed_ms:.3f} returncode={completed.returncode}",
                    flush=True,
                )
                print(
                    "RESOURCE_ADAPTIVE_JOB_QUERY_EVENT="
                    + json.dumps(
                        {
                            "event": "JOB_QUERY_COMPLETE",
                            "round": round_index + 1,
                            "lane_index": lane_index + 1,
                            "lane_position": lane_position,
                            "query_index": canonical_index[path.name],
                            "file": path.name,
                            "family": base.job_query_family(path),
                            "is_long_query": is_long_query,
                            "elapsed_ms": elapsed_ms,
                            "returncode": completed.returncode,
                            "schedule_contract": SCHEDULE_CONTRACT,
                            "schedule_sha256": observed_sha256,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                if completed.returncode != 0:
                    print(completed.stdout[-4000:], flush=True)
                results.append(
                    {
                        "path": path,
                        "elapsed_ms": elapsed_ms,
                        "returncode": completed.returncode,
                    }
                )
        return results

    workload_started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(run_lane, lane_index, lane)
            for lane_index, lane in enumerate(lanes)
        ]
        lane_results = [future.result() for future in futures]
    wall_time_s = time.perf_counter() - workload_started
    results = [item for lane in lane_results for item in lane]
    first_failure = next(
        (int(item["returncode"]) for item in results if int(item["returncode"]) != 0),
        0,
    )
    if first_failure:
        return first_failure
    latencies_ms = [float(item["elapsed_ms"]) for item in results]
    query_counts = {
        path.stem: int(args.rounds)
        for path in path_by_name.values()
    }
    base.emit(
        {
            "query_count": len(results),
            "query_counts": query_counts,
            "all_ap_queries_completed": len(results) == 113 * int(args.rounds),
            "expected_query_count": 113,
            "canonical_query_names": sorted(path.stem for path in path_by_name.values()),
            "exactly_once": bool(args.rounds == 1 and len(results) == 113),
            "execution_mode": SCHEDULE_CONTRACT,
            "shared_database": True,
            "task_dispatch": "fixed_worker_lanes",
            "worker_count": int(args.workers),
            "lane_assignments": manifest["lane_assignments"],
            "worker_assignment_fixed": True,
            "lane_order_fixed": True,
            "schedule_contract": SCHEDULE_CONTRACT,
            "fixed_schedule_sha256": observed_sha256,
            "wall_time_s": wall_time_s,
            "target_wall_time_s": args.target_wall_time_s,
            "completed_within_target_wall_time": bool(
                args.target_wall_time_s <= 0.0
                or wall_time_s <= args.target_wall_time_s
            ),
            "scheduling": manifest,
        },
        {
            "all_p90_avg": base.percentile(latencies_ms, 0.90),
            "all_p90_max": base.percentile(latencies_ms, 0.90),
            "all_p95_avg": base.percentile(latencies_ms, 0.95),
            "all_p95_max": base.percentile(latencies_ms, 0.95),
            "all_p99_avg": base.percentile(latencies_ms, 0.99),
            "all_p99_max": base.percentile(latencies_ms, 0.99),
        },
    )
    return 0


def main() -> int:
    base.run_job = run_job_fixed
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
