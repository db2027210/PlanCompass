"""Prepare one or more dedicated MySQL HyBench 1x workers in parallel.

The loader is deliberately conservative: a complete worker is reused, an empty
worker is initialized, and a partially populated worker is rejected so data is
never duplicated silently.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
from pathlib import Path
from typing import Dict, Iterable


WORKSPACE = Path(__file__).resolve().parent
HYBENCH = Path("/home/anonymous/HyBench-2024-master")
DEFAULT_DATA_ROOT = Path("/home/anonymous/HyBench-2024-master")
BASE_EXPECTED: Dict[str, int] = {
    "customer": 300_000,
    "company": 2_000,
    "transfer": 6_000_000,
    "checking": 600_000,
    "checkingaccount": 302_000,
    "savingaccount": 302_000,
    "loanapps": 600_000,
    "loantrans": 600_000,
}


def expected_counts(scale: int) -> Dict[str, int]:
    if scale <= 0:
        raise ValueError("scale must be positive")
    return {table: rows * scale for table, rows in BASE_EXPECTED.items()}


def mysql(port: int, sql: str, *, database: str | None = None, timeout: int = 3600) -> str:
    command = [
        "mysql", "--connect-timeout=10", "--local-infile=1", "-h127.0.0.1",
        f"-P{port}", "-uroot", "-pmysql", "--batch", "--skip-column-names",
    ]
    if database:
        command.append(database)
    command.extend(["--execute", sql])
    proc = subprocess.run(
        command, cwd=WORKSPACE, capture_output=True, text=True, timeout=timeout, check=False,
    )
    if proc.returncode:
        detail = (proc.stderr or proc.stdout)[-4000:].strip()
        raise RuntimeError(f"mysql port {port} failed ({proc.returncode}): {detail}")
    return proc.stdout.strip()


def counts(port: int) -> Dict[str, int]:
    rows = mysql(
        port,
        "SELECT table_name,table_rows FROM information_schema.tables "
        "WHERE table_schema='hybench' ORDER BY table_name;",
        timeout=60,
    )
    result: Dict[str, int] = {}
    for line in rows.splitlines():
        name, value = line.split("\t", 1)
        result[name.lower()] = int(value)
    return result


def exact_counts(port: int, expected: Dict[str, int]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for table in expected:
        result[table] = int(mysql(port, f"SELECT COUNT(*) FROM `{table}`;", database="hybench", timeout=600))
    return result


def load_sql(data: Path) -> str:
    files = {
        "customer": "customer.csv",
        "company": "company.csv",
        "transfer": "transfer.csv",
        "checking": "checking.csv",
        "checkingaccount": "checkingAccount.csv",
        "savingaccount": "savingAccount.csv",
        "loanapps": "loanApps.csv",
        "loantrans": "loanTrans.csv",
    }
    statements = ["SET SESSION sql_mode='NO_ENGINE_SUBSTITUTION';"]
    for table, filename in files.items():
        path = (data / filename).as_posix().replace("'", "''")
        statements.append(
            f"LOAD DATA LOCAL INFILE '{path}' INTO TABLE `{table}` "
            "FIELDS TERMINATED BY ',' LINES TERMINATED BY '\\n';"
        )
    return "\n".join(statements)


def prepare(port: int, data: Path, expected: Dict[str, int]) -> Dict[str, object]:
    current = counts(port)
    if set(current) == set(expected):
        exact = exact_counts(port, expected)
        if exact == expected:
            return {"port": port, "status": "reused", "counts": exact}
        raise RuntimeError(f"port {port} has a partial/inconsistent HyBench database: {exact}")
    if current:
        raise RuntimeError(f"port {port} has a partial HyBench schema; refusing to append: {current}")

    mysql(port, "CREATE DATABASE hybench CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
    mysql(port, f"source {(HYBENCH / 'conf/ddl_mysql.sql').as_posix()}", database="hybench")
    mysql(
        port,
        "RENAME TABLE savingAccount TO savingaccount_casefix, "
        "savingaccount_casefix TO savingaccount, "
        "checkingAccount TO checkingaccount_casefix, "
        "checkingaccount_casefix TO checkingaccount;",
        database="hybench",
    )
    mysql(port, load_sql(data), database="hybench")
    mysql(port, f"source {(HYBENCH / 'conf/create_index_A2.sql').as_posix()}", database="hybench")
    mysql(port, "ANALYZE TABLE customer,company,transfer,checking,checkingaccount,savingaccount,loanapps,loantrans;", database="hybench")
    exact = exact_counts(port, expected)
    if exact != expected:
        raise RuntimeError(f"port {port} row-count mismatch after load: {exact}")
    return {"port": port, "status": "prepared", "counts": exact}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ports", nargs="+", type=int, required=True)
    parser.add_argument("--max-parallel", type=int, default=3)
    parser.add_argument("--scale", type=int, choices=(1, 10, 100), default=1)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args()
    if len(set(args.ports)) != len(args.ports):
        raise ValueError("ports must be unique")
    data = args.data_root / f"Data_{args.scale}x"
    expected = expected_counts(args.scale)
    for filename in ("customer.csv", "company.csv", "transfer.csv", "checking.csv", "checkingAccount.csv", "savingAccount.csv", "loanApps.csv", "loanTrans.csv"):
        if not (data / filename).is_file():
            raise FileNotFoundError(data / filename)
    failures = 0
    with ThreadPoolExecutor(max_workers=min(args.max_parallel, len(args.ports))) as pool:
        futures = {pool.submit(prepare, port, data, expected): port for port in args.ports}
        for future in as_completed(futures):
            port = futures[future]
            try:
                print(future.result(), flush=True)
            except Exception as exc:  # each worker must report independently
                failures += 1
                print({"port": port, "status": "failed", "error": str(exc)}, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
