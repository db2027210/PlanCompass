#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class PgConfig:
    """PostgreSQL connection config."""

    host: str
    port: str
    user: str
    dbname: str
    connect_timeout_s: int
    password: Optional[str] = None
    no_password: bool = False


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


def load_pg_config(config_path: str) -> PgConfig:
    data: Dict[str, object] = {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}

    postgres_cfg = data.get("postgres", {}) if isinstance(data, dict) else {}
    if not isinstance(postgres_cfg, dict):
        postgres_cfg = {}

    return PgConfig(
        host=os.environ.get("PGHOST", str(postgres_cfg.get("host", "127.0.0.1"))),
        port=os.environ.get("PGPORT", str(postgres_cfg.get("port", "5432"))),
        user=os.environ.get("PGUSER", str(postgres_cfg.get("user", "postgres"))),
        dbname=os.environ.get(
            "PGDATABASE",
            os.environ.get("PGDBNAME", str(postgres_cfg.get("dbname", "postgres"))),
        ),
        connect_timeout_s=max(1, int(float(os.environ.get("PGCONNECT_TIMEOUT", "5")))),
    )


def psql_args(pg: PgConfig, sql: str) -> List[str]:
    return [
        "psql",
        "-h",
        pg.host,
        "-p",
        str(pg.port),
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


def psql_query(pg: PgConfig, sql: str) -> str:
    env = os.environ.copy()
    env["PGCONNECT_TIMEOUT"] = str(pg.connect_timeout_s)
    if pg.password is not None:
        env["PGPASSWORD"] = pg.password
    if pg.no_password:
        env.pop("PGPASSWORD", None)

    args = psql_args(pg, sql)
    if pg.no_password:
        args.insert(9, "-w")

    proc = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"psql exited with code {proc.returncode}").strip()
        if not detail or detail == "psql: error:":
            detail = (
                "psql connection failed. Check host/port/user/dbname and pg_hba.conf. "
                "If the database requires a password, set PGPASSWORD or run with --ask-password. "
                "If it should be passwordless, run with --no-password."
            )
        raise RuntimeError(f"psql query failed: {detail}")
    return (proc.stdout or "").strip()


def has_pg_stat_statements(pg: PgConfig) -> bool:
    sql = """
        SELECT EXISTS (
            SELECT 1
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'pg_stat_statements'
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        );
    """
    return psql_query(pg, sql).strip().lower() in {"t", "true", "1"}


def fetch_snapshot(pg: PgConfig, *, include_statements: bool) -> DbSnapshot:
    if include_statements:
        statements_expr = """
            (
                SELECT COALESCE(SUM(calls), 0)::bigint
                FROM pg_stat_statements
                WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
                  AND query NOT ILIKE '%pg_stat_statements%'
                  AND query NOT ILIKE '%pg_stat_database%'
            ) AS statements_calls
        """
    else:
        statements_expr = "NULL::bigint AS statements_calls"

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
    raw = psql_query(pg, sql)
    if not raw:
        raise RuntimeError(f"no pg_stat_database row returned for db={pg.dbname!r}")

    parts = raw.split("\t")
    if len(parts) != 12:
        raise RuntimeError(f"unexpected snapshot column count: {len(parts)} raw={raw!r}")

    statements_calls = int(parts[10]) if parts[10] else None
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
        statements_calls=statements_calls,
        active_sessions=int(parts[11]),
    )


def rate(new_value: Optional[int], old_value: Optional[int], elapsed: float) -> Optional[float]:
    if new_value is None or old_value is None or elapsed <= 0:
        return None
    return max(0.0, float(new_value - old_value) / elapsed)


def format_optional(value: Optional[float]) -> str:
    if value is None:
        return "NA"
    return f"{value:.2f}"


def print_header() -> None:
    print(
        "time                elapsed_s qps      xact_tps commit_tps rollback_tps "
        "tuple_read_ps tuple_write_ps block_read_ps block_hit_ps active",
        flush=True,
    )
    print("-" * 121, flush=True)


def build_row(prev: DbSnapshot, cur: DbSnapshot) -> Dict[str, object]:
    elapsed = max(0.000001, cur.timestamp - prev.timestamp)
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cur.timestamp)),
        "elapsed_s": round(elapsed, 3),
        "qps_statements": rate(cur.statements_calls, prev.statements_calls, elapsed),
        "xact_tps": rate(cur.xact_total, prev.xact_total, elapsed),
        "commit_tps": rate(cur.xact_commit, prev.xact_commit, elapsed),
        "rollback_tps": rate(cur.xact_rollback, prev.xact_rollback, elapsed),
        "tuple_read_ps": rate(cur.tup_returned + cur.tup_fetched, prev.tup_returned + prev.tup_fetched, elapsed),
        "tuple_write_ps": rate(
            cur.tup_inserted + cur.tup_updated + cur.tup_deleted,
            prev.tup_inserted + prev.tup_updated + prev.tup_deleted,
            elapsed,
        ),
        "block_read_ps": rate(cur.blks_read, prev.blks_read, elapsed),
        "block_hit_ps": rate(cur.blks_hit, prev.blks_hit, elapsed),
        "active_sessions": cur.active_sessions,
        "xact_total": cur.xact_total,
        "statements_calls": cur.statements_calls,
    }


def print_row(row: Dict[str, object]) -> None:
    print(
        "{time} {elapsed:>9} {qps:>8} {xact_tps:>9} {commit_tps:>10} {rollback_tps:>12} "
        "{tuple_read_ps:>13} {tuple_write_ps:>14} {block_read_ps:>13} {block_hit_ps:>12} {active:>6}".format(
            time=row["time"],
            elapsed=f"{float(row['elapsed_s']):.3f}",
            qps=format_optional(row["qps_statements"] if isinstance(row["qps_statements"], float) else None),
            xact_tps=format_optional(row["xact_tps"] if isinstance(row["xact_tps"], float) else None),
            commit_tps=format_optional(row["commit_tps"] if isinstance(row["commit_tps"], float) else None),
            rollback_tps=format_optional(row["rollback_tps"] if isinstance(row["rollback_tps"], float) else None),
            tuple_read_ps=format_optional(row["tuple_read_ps"] if isinstance(row["tuple_read_ps"], float) else None),
            tuple_write_ps=format_optional(row["tuple_write_ps"] if isinstance(row["tuple_write_ps"], float) else None),
            block_read_ps=format_optional(row["block_read_ps"] if isinstance(row["block_read_ps"], float) else None),
            block_hit_ps=format_optional(row["block_hit_ps"] if isinstance(row["block_hit_ps"], float) else None),
            active=row["active_sessions"],
        ),
        flush=True,
    )


def open_csv_writer(path: Optional[str]) -> Tuple[Optional[object], Optional[csv.DictWriter]]:
    if not path:
        return None, None
    handle = open(path, "w", newline="", encoding="utf-8")
    fields = [
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
        "xact_total",
        "statements_calls",
    ]
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    handle.flush()
    return handle, writer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor PostgreSQL QPS/TPS from pg_stat_database and pg_stat_statements.",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("OPTIMIZER_CONFIG", "config/optimizer_config.json"),
        help="optimizer config path, default: config/optimizer_config.json",
    )
    parser.add_argument("--interval", type=float, default=1.0, help="sample interval seconds")
    parser.add_argument("--count", type=int, default=0, help="number of output samples, 0 means unlimited")
    parser.add_argument("--csv", default="", help="optional CSV output path")
    parser.add_argument("--host", default="", help="override PostgreSQL host")
    parser.add_argument("--port", default="", help="override PostgreSQL port")
    parser.add_argument("--user", default="", help="override PostgreSQL user")
    parser.add_argument("--dbname", default="", help="override PostgreSQL database")
    parser.add_argument("--no-pg-stat-statements", action="store_true", help="disable QPS from pg_stat_statements")
    parser.add_argument("--ask-password", action="store_true", help="prompt once for PostgreSQL password")
    parser.add_argument("--no-password", action="store_true", help="force passwordless psql connection")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pg = load_pg_config(args.config)
    if args.host:
        pg.host = args.host
    if args.port:
        pg.port = args.port
    if args.user:
        pg.user = args.user
    if args.dbname:
        pg.dbname = args.dbname
    pg.no_password = bool(args.no_password)
    if args.ask_password and "PGPASSWORD" not in os.environ:
        pg.password = getpass.getpass(f"Password for {pg.user}@{pg.host}:{pg.port}/{pg.dbname}: ")

    print(
        f"Connecting db={pg.dbname} host={pg.host} port={pg.port} user={pg.user} interval={args.interval}s",
        flush=True,
    )

    include_statements = False if args.no_pg_stat_statements else has_pg_stat_statements(pg)
    print(f"pg_stat_statements={include_statements}", flush=True)
    if not include_statements:
        print("QPS is unavailable because pg_stat_statements is disabled or not visible.", flush=True)

    csv_handle, csv_writer = open_csv_writer(args.csv or None)
    try:
        prev = fetch_snapshot(pg, include_statements=include_statements)
        print("Initial snapshot captured. Realtime rates will print every interval.", flush=True)
        print_header()
        emitted = 0
        while args.count <= 0 or emitted < args.count:
            time.sleep(max(0.1, args.interval))
            cur = fetch_snapshot(pg, include_statements=include_statements)
            row = build_row(prev, cur)
            print_row(row)
            if csv_writer is not None and csv_handle is not None:
                csv_writer.writerow(row)
                csv_handle.flush()
            prev = cur
            emitted += 1
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 0
    finally:
        if csv_handle is not None:
            csv_handle.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
