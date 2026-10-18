#!/usr/bin/env python3
"""Safely plan, load, and validate HyBench CSV data in MySQL.

Dry-run is the default.  The tool never manages containers and never edits the
HyBench checkout.  Execution requires both --execute and an exact database
confirmation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TABLE_ORDER = (
    "customer", "company", "savingAccount", "checkingAccount",
    "transfer", "checking", "loanapps", "loantrans",
)


@dataclass(frozen=True)
class TableSpec:
    file_name: str
    load_columns: str
    set_clause: str = ""


TABLE_SPECS = {
    "customer": TableSpec("customer.csv", "custID,companyID,gender,name,age,phone,province,city,loan_balance,saving_credit,checking_credit,loan_credit,Isblocked,created_date,last_update_timestamp"),
    "company": TableSpec("company.csv", "companyID,name,category,staff_size,loan_balance,phone,province,city,saving_credit,checking_credit,loan_credit,Isblocked,created_date,last_update_timestamp"),
    "savingAccount": TableSpec("savingAccount.csv", "accountID,userID,balance,Isblocked,savingAccount_ts,@ignored_trailing"),
    "checkingAccount": TableSpec("checkingAccount.csv", "accountID,userID,balance,Isblocked,checkingAccount_ts"),
    "transfer": TableSpec("transfer.csv", "id,sourceID,targetID,amount,type,transfer_ts,@fresh_ts", "fresh_ts=NULLIF(@fresh_ts,'')"),
    "checking": TableSpec("checking.csv", "id,sourceID,targetID,amount,type,checking_ts"),
    "loanapps": TableSpec("loanApps.csv", "id,applicantID,amount,duration,status,loanapps_ts"),
    "loantrans": TableSpec("loanTrans.csv", "id,applicantID,appID,amount,status,loantrans_ts,duration,@contract_timestamp,delinquency", "contract_timestamp=NULLIF(@contract_timestamp,'')"),
}
CANONICAL_TABLE = {name.lower(): name for name in TABLE_ORDER}


def sql_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def discover_data(root: Path, scale: str) -> tuple[Path, dict[str, dict[str, int]]]:
    data_dir = (root / f"Data_{scale}").resolve()
    if not data_dir.is_dir():
        raise ValueError(f"data directory not found: {data_dir}")
    files: dict[str, dict[str, int]] = {}
    for table in TABLE_ORDER:
        path = data_dir / TABLE_SPECS[table].file_name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"missing or empty input for {table}: {path}")
        files[table] = {"bytes": path.stat().st_size, "estimated_rows": estimate_rows(path)}
    return data_dir, files


def estimate_rows(path: Path, sample_bytes: int = 1 << 20) -> int:
    size = path.stat().st_size
    with path.open("rb") as stream:
        sample = stream.read(min(sample_bytes, size))
    lines = sample.count(b"\n")
    if not sample or not lines:
        return 1
    return max(1, round(size * lines / len(sample)))


def load_sql(data_dir: Path, tables: Iterable[str] = TABLE_ORDER) -> str:
    statements = []
    selected = tuple(tables)
    unknown = set(selected) - set(TABLE_ORDER)
    if unknown:
        raise ValueError(f"unknown HyBench tables: {sorted(unknown)}")
    for table in selected:
        spec = TABLE_SPECS[table]
        path = str((data_dir / spec.file_name).resolve())
        statement = (
            f"LOAD DATA LOCAL INFILE {sql_quote(path)} INTO TABLE `{table}` "
            "CHARACTER SET utf8mb4 FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"' "
            "LINES TERMINATED BY '\\n' "
            f"({spec.load_columns})"
        )
        if spec.set_clause:
            statement += " SET " + spec.set_clause
        statements.append(statement + ";")
    return "\n".join(statements)


def count_sql(database: str) -> str:
    parts = [
        f"SELECT {sql_quote(table)}, COUNT(*) FROM `{table}`"
        for table in TABLE_ORDER
    ]
    return " UNION ALL ".join(parts) + ";"


def mysql_command(args: argparse.Namespace) -> list[str]:
    return [
        args.mysql_bin, "--local-infile=1", "--protocol=TCP",
        f"--host={args.host}", f"--port={args.port}", f"--user={args.user}",
        "--batch", "--skip-column-names", "--show-warnings", args.database,
    ]


def run_mysql(args: argparse.Namespace, sql: str) -> str:
    env = os.environ.copy()
    password = env.get(args.password_env)
    if password is None:
        raise RuntimeError(f"password environment variable is not set: {args.password_env}")
    env["MYSQL_PWD"] = password
    completed = subprocess.run(
        mysql_command(args), input=sql, text=True, capture_output=True, env=env,
        timeout=args.timeout, check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"mysql exited {completed.returncode}: {detail}")
    return completed.stdout


def parse_counts(output: str) -> dict[str, int]:
    counts = {}
    for line in output.splitlines():
        fields = line.split("\t")
        canonical = CANONICAL_TABLE.get(fields[0].lower()) if fields else None
        if len(fields) == 2 and canonical is not None:
            counts[canonical] = int(fields[1])
    return counts


def existing_counts(args: argparse.Namespace) -> dict[str, int]:
    table_list = ",".join(sql_quote(t.lower()) for t in TABLE_ORDER)
    sql = (
        "SELECT table_name, table_rows FROM information_schema.tables "
        f"WHERE table_schema={sql_quote(args.database)} AND LOWER(table_name) IN ({table_list});"
    )
    existing = []
    for line in run_mysql(args, sql).splitlines():
        fields = line.split("\t")
        if len(fields) == 2:
            existing.append(fields[0])
    if not existing:
        return {}
    # INFORMATION_SCHEMA.TABLES.table_rows is only an estimate for InnoDB and
    # can report zero for a populated table.  Use exact counts for this safety
    # gate even though it makes the explicit execution preflight slower.
    exact_sql = " UNION ALL ".join(
        f"SELECT {sql_quote(name)}, COUNT(*) FROM `{name}`" for name in existing
    ) + ";"
    return parse_counts(run_mysql(args, exact_sql))


def validate_identifiers(args: argparse.Namespace) -> None:
    if not IDENTIFIER.fullmatch(args.database):
        raise ValueError("--database must be a simple MySQL identifier")
    if not IDENTIFIER.fullmatch(args.user):
        raise ValueError("--user must be a simple MySQL identifier")
    if not 1 <= args.port <= 65535:
        raise ValueError("--port must be between 1 and 65535")


def plan(args: argparse.Namespace) -> dict:
    root = args.hybench_root.resolve()
    ddl = (root / "conf" / "ddl_mysql.sql").resolve()
    if not ddl.is_file():
        raise ValueError(f"MySQL DDL not found: {ddl}")
    data_dir, files = discover_data(root, args.scale)
    total = sum(item["bytes"] for item in files.values())
    return {
        "mode": "resume-missing" if args.execute and args.resume_missing else ("execute" if args.execute else ("validate" if args.validate_only else "dry-run")),
        "endpoint": {"host": args.host, "port": args.port, "database": args.database, "user": args.user},
        "hybench_root": str(root), "data_dir": str(data_dir), "ddl": str(ddl),
        "tables": files, "input_bytes": total,
        "recommended_free_bytes": total * 2,
        "mysql_command": mysql_command(args),
    }


def execute(args: argparse.Namespace, details: dict) -> dict:
    if not args.validate_only and args.confirm_database != args.database:
        raise RuntimeError("execution requires --confirm-database exactly matching --database")
    if args.validate_only:
        counts = parse_counts(run_mysql(args, count_sql(args.database)))
        return {**details, "counts": counts, "validated": len(counts) == len(TABLE_ORDER) and all(v > 0 for v in counts.values())}

    present = existing_counts(args)
    nonempty = {name: count for name, count in present.items() if count > 0}
    if nonempty and not args.resume_missing:
        raise RuntimeError(f"refusing to load non-empty HyBench tables: {nonempty}")
    ddl_text = Path(details["ddl"]).read_text(encoding="utf-8")
    run_mysql(args, ddl_text)
    missing = [name for name in TABLE_ORDER if present.get(name, 0) == 0]
    if missing:
        run_mysql(args, "SET SESSION sql_mode='STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION';\n" + load_sql(Path(details["data_dir"]), missing))
    counts = parse_counts(run_mysql(args, count_sql(args.database)))
    if len(counts) != len(TABLE_ORDER) or any(value <= 0 for value in counts.values()):
        raise RuntimeError(f"post-load validation failed: {counts}")
    return {**details, "counts": counts, "validated": True}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", required=True, help="explicit MySQL host; no endpoint default")
    p.add_argument("--port", required=True, type=int, help="explicit MySQL TCP port")
    p.add_argument("--database", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--hybench-root", type=Path, default=Path("/home/anonymous/HyBench-2024-master"))
    p.add_argument("--scale", choices=("10x", "100x"), default="10x")
    p.add_argument("--mysql-bin", default="mysql")
    p.add_argument("--password-env", default="HYBENCH_MYSQL_PASSWORD")
    p.add_argument("--timeout", type=int, default=86400)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="create missing tables and load CSV files")
    mode.add_argument("--validate-only", action="store_true", help="connect read-only and count existing rows")
    p.add_argument("--resume-missing", action="store_true",
                   help="with --execute, preserve non-empty tables and load only absent/empty tables")
    p.add_argument("--confirm-database", help="required with --execute; must exactly match --database")
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.resume_missing and not args.execute:
        print("error: --resume-missing requires --execute", file=sys.stderr)
        return 1
    try:
        validate_identifiers(args)
        details = plan(args)
        result = execute(args, details) if (args.execute or args.validate_only) else details
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if not args.validate_only or result.get("validated") else 2
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
