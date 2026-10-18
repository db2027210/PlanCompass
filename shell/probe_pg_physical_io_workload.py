#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from binary_plan_resource_tuner import (
    cpuset_cpu_count,
    docker_sampler,
    is_container_pg_proc_io_mode,
    load_config,
    pg_conn_from_worker,
    psql_query,
    validation_workers,
)


def avg(values: List[float]) -> float:
    return float(statistics.fmean(values)) if values else 0.0


def pct(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return float(ordered[idx])


def setup_sql_workload(pg: Any) -> None:
    psql_query(
        pg,
        """
        CREATE TABLE IF NOT EXISTS bonect_physical_io_probe (
            id bigserial PRIMARY KEY,
            bucket int NOT NULL DEFAULT 0,
            payload text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        ALTER TABLE bonect_physical_io_probe ALTER COLUMN payload SET STORAGE EXTERNAL;
        TRUNCATE bonect_physical_io_probe;
        """,
        timeout_s=120.0,
    )
    psql_query(pg, "CHECKPOINT;", dbname="postgres", timeout_s=120.0)


def run_sql_workload(
    pg: Any,
    duration_s: float,
    label: str,
    args: argparse.Namespace,
    errors: List[str],
    session_idx: int = 0,
    session_count: int = 1,
) -> Dict[str, Any]:
    start = time.monotonic()
    iterations = 0
    try:
        while time.monotonic() - start < duration_s:
            psql_query(
                pg,
                """
                INSERT INTO bonect_physical_io_probe(bucket, payload)
                SELECT (random() * 1000)::int, repeat(md5(random()::text), {payload_repeat})
                FROM generate_series(1, {insert_rows});
                UPDATE bonect_physical_io_probe
                SET payload = repeat(md5(random()::text), {payload_repeat}),
                    updated_at = clock_timestamp()
                WHERE id IN (
                    SELECT id FROM bonect_physical_io_probe
                    WHERE (id % {session_count}) = {session_idx}
                    ORDER BY random()
                    LIMIT {update_rows}
                );
                SELECT count(*) FROM bonect_physical_io_probe WHERE bucket BETWEEN 100 AND 900;
                """.format(
                    insert_rows=max(1, int(args.insert_rows)),
                    update_rows=max(0, int(args.update_rows)),
                    payload_repeat=max(1, int(args.payload_repeat)),
                    session_count=max(1, int(session_count)),
                    session_idx=max(0, int(session_idx)),
                ),
                timeout_s=120.0,
            )
            iterations += 1
    except Exception as exc:
        errors.append(f"{label}: {type(exc).__name__}: {exc}")
    return {"iterations": iterations, "elapsed_s": max(0.0, time.monotonic() - start)}


def probe_worker(cfg: Dict[str, Any], worker: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    pg = pg_conn_from_worker(cfg, worker)
    samples = {
        "cpu_cores": [],
        "memory_bytes": [],
        "io_read_bps": [],
        "io_write_bps": [],
        "io_bps": [],
    }
    meta: Dict[str, Any] = {}
    errors: List[str] = []
    try:
        setup_sql_workload(pg)
    except Exception as exc:
        errors.append(f"{worker.get('name')}: setup {type(exc).__name__}: {exc}")
    stop_event = threading.Event()
    sampler = threading.Thread(
        target=docker_sampler,
        args=(
            str(worker.get("container")),
            float(args.interval_s),
            float(args.warmup_s),
            "working_set",
            str(args.io_mode),
            float(cpuset_cpu_count(worker.get("cpuset_cpus"))),
            str(worker.get("disk_root") or ""),
            worker.get("disk_devices"),
            stop_event,
            samples,
            meta,
            errors,
        ),
        daemon=True,
    )
    sampler.start()
    session_results: List[Dict[str, Any]] = []
    session_lock = threading.Lock()

    def run_session(session_idx: int) -> None:
        session_pg = pg_conn_from_worker(cfg, worker)
        label = f"{worker.get('name')}:s{session_idx}"
        result = run_sql_workload(session_pg, float(args.duration_s), label, args, errors, session_idx, session_count)
        with session_lock:
            session_results.append(result)

    session_count = max(1, int(args.sessions_per_worker))
    workload_threads = [
        threading.Thread(target=run_session, args=(session_idx,), daemon=False)
        for session_idx in range(session_count)
    ]
    workload_start = time.monotonic()
    for thread in workload_threads:
        thread.start()
    for thread in workload_threads:
        thread.join()
    try:
        psql_query(pg, "CHECKPOINT;", dbname="postgres", timeout_s=120.0)
    except Exception as exc:
        errors.append(f"{worker.get('name')}: post_checkpoint {type(exc).__name__}: {exc}")
    workload = {
        "sessions_per_worker": session_count,
        "iterations": sum(int(item.get("iterations") or 0) for item in session_results),
        "elapsed_s": max(0.0, time.monotonic() - workload_start),
        "sessions": sorted(session_results, key=lambda item: float(item.get("elapsed_s") or 0.0)),
    }
    stop_event.set()
    sampler.join(timeout=90.0 if is_container_pg_proc_io_mode(str(args.io_mode)) else 5.0)
    result = {
        "worker": worker.get("name"),
        "container": worker.get("container"),
        "host": pg.host,
        "port": pg.port,
        "dbname": pg.dbname,
        "workload": workload,
        "resources": {
            "cpu_cores": avg(samples["cpu_cores"]),
            "memory_gib": avg(samples["memory_bytes"]) / (1024.0 ** 3),
            "io_mib_s": avg(samples["io_bps"]) / (1024.0 ** 2),
            "io_read_mib_s": avg(samples["io_read_bps"]) / (1024.0 ** 2),
            "io_write_mib_s": avg(samples["io_write_bps"]) / (1024.0 ** 2),
        },
        "p99": {
            "cpu_cores": pct(samples["cpu_cores"], 0.99),
            "memory_gib": pct(samples["memory_bytes"], 0.99) / (1024.0 ** 3),
            "io_mib_s": pct(samples["io_bps"], 0.99) / (1024.0 ** 2),
        },
        "sample_count": {key: len(value) for key, value in samples.items()},
        "sampler_meta": meta,
        "errors": errors,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/binary_plan_tuning_hybenchxp.json")
    parser.add_argument("--target", default="none")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--duration-s", type=float, default=120.0)
    parser.add_argument("--warmup-s", type=float, default=10.0)
    parser.add_argument("--interval-s", type=float, default=1.0)
    parser.add_argument("--io-mode", default="cgroup")
    parser.add_argument("--insert-rows", type=int, default=800)
    parser.add_argument("--update-rows", type=int, default=400)
    parser.add_argument("--payload-repeat", type=int, default=128)
    parser.add_argument("--sessions-per-worker", type=int, default=1)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    cfg = load_config(args.config)
    workers = [
        worker for worker in validation_workers(cfg, args.target)
        if str(worker.get("name") or "").startswith("cpu_mem_w")
    ][: max(1, args.workers)]
    results: List[Dict[str, Any]] = []
    lock = threading.Lock()

    def run(worker: Dict[str, Any]) -> None:
        result = probe_worker(cfg, worker, args)
        with lock:
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)

    threads = [threading.Thread(target=run, args=(worker,), daemon=False) for worker in workers]
    start = time.time()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    payload = {
        "started_at": start,
        "elapsed_s": time.time() - start,
        "target": args.target,
        "parallelism": len(workers),
        "io_mode": str(args.io_mode),
        "results": sorted(results, key=lambda item: str(item.get("worker"))),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
