#!/usr/bin/env python3
"""Create and verify the session-owned TPC-H SF10 template without rebuilding."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


SOURCE = Path("/disk3/ra_mysql_workers/tpch-w1")
TARGET = Path("/disk2/resource_adaptive/parallel24_20260721_v1/mysql/recost_gradient_descent/templates/tpch")
DATADIR = TARGET / "datadir"
CONTAINER = "p24_mysql_recost_gradient_descent_tpch_prepare"
PORT = 57313


def run(argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=check)


def mysql(sql: str) -> str:
    raw = run(["mysql", "--no-defaults", "-uroot", "-p123456", "-h127.0.0.1", f"-P{PORT}", "-NBe", sql]).stdout
    return "\n".join(line for line in raw.splitlines() if not line.startswith("mysql:"))


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    if not (TARGET / "database_manifest.json").is_file():
        copy_complete = TARGET / ".copy_complete"
        if not copy_complete.is_file():
            if DATADIR.exists():
                run(["sudo", "-n", "rm", "-rf", str(DATADIR)])
            DATADIR.mkdir()
            run(["sudo", "-n", "cp", "--archive", "--reflink=auto", f"{SOURCE}/.", str(DATADIR)])
            run(["sudo", "-n", "chown", "-R", "999:999", str(DATADIR)])
            copy_complete.write_text(dt.datetime.now(dt.timezone.utc).isoformat() + "\n")
        live_probe = run(["mysql", "--no-defaults", "-uroot", "-p123456", "-h127.0.0.1", f"-P{PORT}", "-NBe", "select 1"], check=False)
        if live_probe.returncode != 0:
            run(["docker", "rm", "--force", CONTAINER], check=False)
            run([
            "docker", "run", "-d", "--name", CONTAINER,
            "--cpus", "8", "--cpuset-cpus", "224-231,480-487", "--cpuset-mems", "1",
            "--memory", "2147483648", "--memory-swap", "2147483648", "--pids-limit", "512",
            "-p", f"127.0.0.1:{PORT}:3306", "-v", f"{DATADIR}:/var/lib/mysql",
            "mysql:8.0", "--skip-log-bin", "--innodb-buffer-pool-size=1536M",
            "--innodb-buffer-pool-instances=1", "--innodb-buffer-pool-chunk-size=64M",
            "--tmp-table-size=16M", "--max-heap-table-size=16M", "--max-connections=64",
            "--transaction-isolation=READ-COMMITTED", "--innodb-adaptive-hash-index=OFF",
            ])
        deadline = time.time() + 600
        while time.time() < deadline:
            probe = run(["mysql", "--no-defaults", "-uroot", "-p123456", "-h127.0.0.1", f"-P{PORT}", "-NBe", "select 1"], check=False)
            if probe.returncode == 0:
                break
            time.sleep(2)
        else:
            raise RuntimeError("TPC-H template verification container did not become ready")
        databases = set(mysql("show databases").split())
        if "tpch_sf10" not in databases:
            if "tpch" not in databases:
                raise RuntimeError(f"TPC-H source has neither tpch nor tpch_sf10: {databases}")
            mysql("CREATE DATABASE tpch_sf10; " + " ".join(
                f"RENAME TABLE tpch.{table} TO tpch_sf10.{table};"
                for table in ("customer", "lineitem", "nation", "orders", "part", "partsupp", "region", "supplier")
            ))
        metadata = mysql("SELECT TABLE_NAME,TABLE_ROWS,DATA_LENGTH+INDEX_LENGTH FROM information_schema.TABLES WHERE TABLE_SCHEMA='tpch_sf10' ORDER BY TABLE_NAME")
        rows = {
            fields[0]: {"estimated_rows": int(fields[1]), "bytes": int(fields[2])}
            for fields in (line.split("\t") for line in metadata.splitlines())
        }
        exact_small = {
            table: int(mysql(f"SELECT COUNT(*) FROM tpch_sf10.{table}").strip())
            for table in ("nation", "region")
        }
        schema_rows = mysql("SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_KEY FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='tpch_sf10' ORDER BY TABLE_NAME,ORDINAL_POSITION")
        index_rows = mysql("SELECT TABLE_NAME,INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA='tpch_sf10' ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX")
        run(["docker", "stop", "--time", "120", CONTAINER])
        run(["docker", "rm", CONTAINER])
        manifest = {
            "schema_version": 1, "status": "complete", "campaign_id": "parallel24_20260721_v1",
            "engine": "mysql", "workload": "tpch", "database": "tpch_sf10", "scale_factor": 10,
            "source_datadir": str(SOURCE), "source_container_state": "stopped_before_copy",
            "copy_method": "cp --archive --reflink=auto", "table_size_readback": rows,
            "exact_small_table_row_counts": exact_small,
            "schema_sha256": hashlib.sha256(schema_rows.encode()).hexdigest(),
            "indexes_sha256": hashlib.sha256(index_rows.encode()).hexdigest(),
            "verified_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        (TARGET / "database_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
