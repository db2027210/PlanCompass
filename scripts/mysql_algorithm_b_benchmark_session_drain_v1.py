#!/usr/bin/env python3
"""Drain only formal benchmark sessions between MySQL Algorithm B waves."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Iterable, Mapping


BENCHMARK_USER = "ra_benchmark"
READBACK_SQL = (
    "SELECT ID,COMMAND,TIME FROM information_schema.PROCESSLIST "
    f"WHERE USER='{BENCHMARK_USER}' ORDER BY ID"
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _mysql(worker: Mapping[str, Any], sql: str) -> str:
    defaults = Path(str(worker["credential_defaults"]))
    if not defaults.is_file() or defaults.is_symlink():
        raise RuntimeError(
            f"{worker['worker_id']} regular root credential authority required"
        )
    completed = subprocess.run(
        [
            "mysql", f"--defaults-file={defaults}", "--host=127.0.0.1",
            f"--port={int(worker['host_port'])}", "--connect-timeout=5",
            "-NBe", sql,
        ],
        text=True, capture_output=True, check=True, timeout=15,
    )
    return completed.stdout.strip()


def _sessions(worker: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in _mysql(worker, READBACK_SQL).splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            raise RuntimeError(f"{worker['worker_id']} processlist row differs")
        rows.append({
            "connection_id": int(fields[0]),
            "command": fields[1],
            "time_seconds": int(fields[2]),
        })
    return rows


def _view_count(worker: Mapping[str, Any]) -> int:
    if str(worker.get("scene")) != "tpch_sf4_memory":
        return 0
    raw = _mysql(
        worker,
        "SELECT COUNT(*) FROM information_schema.VIEWS "
        "WHERE TABLE_SCHEMA='tpch' AND TABLE_NAME='revenue0'",
    )
    return int(raw or 0)


def drain_worker(
    worker: Mapping[str, Any], *, timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    started = time.monotonic()
    before = _sessions(worker)
    killed: list[int] = []
    for row in before:
        connection_id = int(row["connection_id"])
        try:
            _mysql(worker, f"KILL CONNECTION {connection_id}")
            killed.append(connection_id)
        except subprocess.CalledProcessError as error:
            # A connection may finish between readback and KILL.  Only accept
            # that race when the final zero-session readback proves cleanup.
            if "Unknown thread id" not in (error.stderr or ""):
                raise
    deadline = time.monotonic() + timeout_seconds
    after: list[dict[str, Any]] = []
    while True:
        after = _sessions(worker)
        if not after:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"{worker['worker_id']} benchmark session drain timed out: {after}"
            )
        time.sleep(0.25)
    view_before = _view_count(worker)
    if str(worker.get("scene")) == "tpch_sf4_memory":
        _mysql(worker, "DROP VIEW IF EXISTS tpch.revenue0")
    view_after = _view_count(worker)
    if view_after != 0:
        raise RuntimeError(f"{worker['worker_id']} revenue0 cleanup failed")
    return {
        "worker_id": str(worker["worker_id"]),
        "scene": str(worker["scene"]),
        "container": str(worker["container"]),
        "container_id": str(worker["container_id"]),
        "benchmark_user": BENCHMARK_USER,
        "sessions_before": before,
        "killed_connection_ids": killed,
        "sessions_after": after,
        "revenue0_view_count_before": view_before,
        "revenue0_view_count_after": view_after,
        "elapsed_seconds": time.monotonic() - started,
        "status": "PASS",
    }


def drain_workers(
    workers: Mapping[str, Mapping[str, Any]], worker_ids: Iterable[str],
) -> dict[str, Any]:
    selected = sorted(set(str(worker_id) for worker_id in worker_ids))
    if not selected:
        raise ValueError("at least one worker is required for session drain")
    rows = []
    for worker_id in selected:
        if worker_id not in workers:
            raise RuntimeError(f"unknown drain worker: {worker_id}")
        rows.append(drain_worker(workers[worker_id]))
    return {
        "schema_version": "mysql_algorithm_b_benchmark_session_drain_v1",
        "status": "PASS",
        "created_at": _now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "benchmark_user": BENCHMARK_USER,
        "external_container_mutation": False,
        "workers": rows,
        "all_workers_zero_sessions_after": all(
            not row["sessions_after"] for row in rows
        ),
        "all_tpch_revenue0_views_absent_after": all(
            row["revenue0_view_count_after"] == 0 for row in rows
        ),
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{_sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-binding", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker-id", action="append", required=True)
    args = parser.parse_args()
    prepared_path = args.prepared_binding.resolve()
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh drain output required: {output}")
    prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
    if prepared.get("status") != "PREPARED":
        raise RuntimeError("prepared binding status differs")
    workers = {
        str(row["worker_id"]): dict(row) for row in prepared.get("workers") or []
    }
    document = drain_workers(workers, args.worker_id)
    document["prepared_binding_path"] = str(prepared_path)
    document["prepared_binding_sha256"] = _sha(prepared_path)
    _atomic(output, document)
    print(json.dumps({
        "status": "PASS", "output": str(output),
        "workers": args.worker_id,
        "killed_sessions": sum(
            len(row["killed_connection_ids"]) for row in document["workers"]
        ),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
