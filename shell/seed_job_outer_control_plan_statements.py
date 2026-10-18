#!/usr/bin/env python3
"""Seed control pg_stat_statements with the frozen JOB query corpus."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Dict

import psycopg2


LANE = Path("/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02")
ROOT = LANE / "history/job/outer/attempt_4_formal_dbqps_v1_retry3"
SQL_ROOT = LANE / "assets/job_sql"
EVIDENCE = Path(
    os.environ.get(
        "RA_PG_CONTROL_SEED_EVIDENCE",
        str(ROOT / "control_plan_seed_evidence.json"),
    )
).resolve()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def statement_calls() -> int:
    with psycopg2.connect(
        host="127.0.0.1",
        port=64309,
        user="postgres",
        password="CHANGE_ME",
        dbname="imdb_job",
        application_name="job-control-plan-seed-audit",
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT coalesce(sum(calls), 0)::bigint "
                "FROM pg_stat_statements "
                "WHERE dbid=(SELECT oid FROM pg_database "
                "WHERE datname=current_database()) "
                "AND query NOT ILIKE '%pg_stat_statements%'"
            )
            return int(cursor.fetchone()[0])


def main() -> int:
    if EVIDENCE.exists() or EVIDENCE.is_symlink():
        raise RuntimeError(f"refusing to overwrite control seed evidence: {EVIDENCE}")
    sql_paths = [
        path
        for path in sorted(SQL_ROOT.glob("*.sql"))
        if re.fullmatch(r"[0-9]+[a-z]\.sql", path.name)
    ]
    if not sql_paths:
        raise RuntimeError("frozen JOB SQL corpus is empty")
    env = os.environ.copy()
    env["PGPASSWORD"] = "CHANGE_ME"
    calls_before = statement_calls()
    began = time.perf_counter()
    records = []
    for index, sql_path in enumerate(sql_paths, start=1):
        query_began = time.perf_counter()
        process = subprocess.run(
            [
                "psql",
                "-h", "127.0.0.1",
                "-p", "64309",
                "-U", "postgres",
                "-d", "imdb_job",
                "-v", "ON_ERROR_STOP=1",
                "-f", str(sql_path),
            ],
            check=False,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            timeout=600,
        )
        elapsed = time.perf_counter() - query_began
        if process.returncode != 0:
            raise RuntimeError(
                f"JOB control seed failed for {sql_path.name}: "
                f"{(process.stderr or '')[-4000:]}"
            )
        record = {
            "index": index,
            "sql_path": str(sql_path),
            "sql_sha256": sha256(sql_path),
            "wall_seconds": elapsed,
        }
        records.append(record)
        print(
            f"seeded {index}/{len(sql_paths)} {sql_path.name} "
            f"wall_seconds={elapsed:.3f}",
            flush=True,
        )
    calls_after = statement_calls()
    if calls_after <= calls_before:
        raise RuntimeError(
            f"control statement calls did not increase: {calls_before}->{calls_after}"
        )
    evidence: Dict[str, Any] = {
        "schema_version": 1,
        "audit_id": "pg5_job_attempt4_retry3_control_plan_seed_v1",
        "database_endpoint": "127.0.0.1:64309/imdb_job",
        "sql_count": len(records),
        "pg_stat_statements_calls_before": calls_before,
        "pg_stat_statements_calls_after": calls_after,
        "monitor_sql_excluded_from_counts": True,
        "wall_seconds": time.perf_counter() - began,
        "records": records,
        "seed_script_path": str(Path(__file__).resolve()),
        "seed_script_sha256": sha256(Path(__file__).resolve()),
        "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    temporary = EVIDENCE.with_suffix(EVIDENCE.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(evidence, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(EVIDENCE)
    print(
        json.dumps(
            {
                "status": "passed",
                "evidence_path": str(EVIDENCE),
                "evidence_sha256": sha256(EVIDENCE),
                "sql_count": len(records),
                "calls_before": calls_before,
                "calls_after": calls_after,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
