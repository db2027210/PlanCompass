#!/usr/bin/env python3
"""Build this session's independent TPC-H SF10 template from a stopped verified source."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import formal_mysql_campaign_20260721 as common


SOURCE = Path("/disk3/ra_mysql_workers/tpch-w1")
ROOT = Path("/disk2/resource_adaptive/parallel24_20260721_v1/mysql/random_walk/templates/tpch")
DATADIR = ROOT / "datadir"
CONTAINER = "p24_mysql_random_walk_tpch_prepare"
PORT = 57203
TABLES = ("customer", "lineitem", "nation", "orders", "part", "partsupp", "region", "supplier")
EXPECTED = {
    "customer": 1_500_000, "lineitem": 59_986_052, "nation": 25,
    "orders": 15_000_000, "part": 2_000_000, "partsupp": 8_000_000,
    "region": 5, "supplier": 100_000,
}


def run(argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=check)


def mysql(sql: str) -> str:
    env = {**os.environ, "MYSQL_PWD": "CHANGE_ME"}
    return subprocess.run(
        ["mysql", "--no-defaults", "-uroot", "-h127.0.0.1", f"-P{PORT}", "-NBe", sql],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        env=env,
    ).stdout


def canonical(rows: object) -> str:
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def tabular(sql: str) -> list[list[str]]:
    return [line.split("\t") for line in mysql(sql).splitlines() if line.strip()]


def main() -> int:
    manifest_path = ROOT / "database_manifest.json"
    if manifest_path.is_file():
        return 0
    ROOT.mkdir(parents=True, exist_ok=True)
    if not (ROOT / ".copy_complete").is_file():
        if DATADIR.exists():
            raise RuntimeError(f"partial target requires manual audit: {DATADIR}")
        DATADIR.mkdir()
        run(["sudo", "-n", "cp", "--archive", "--reflink=auto", f"{SOURCE}/.", str(DATADIR)])
        run(["sudo", "-n", "chown", "-R", "999:999", str(DATADIR)])
        (ROOT / ".copy_complete").write_text(f"{time.time()}\n", encoding="utf-8")
    inspect = run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER], check=False)
    if inspect.returncode != 0 or inspect.stdout.strip() != "true":
        run(["docker", "rm", "-f", CONTAINER], check=False)
        run([
            "docker", "run", "-d", "--name", CONTAINER,
        "--cpus", "8", "--cpuset-cpus", "184-191,440-447", "--cpuset-mems", "0-1",
        "--memory", "2147483648", "--memory-swap", "2147483648", "--pids-limit", "512",
        "--device-read-bps", "/dev/nvme2n1:367001600",
        "--device-write-bps", "/dev/nvme2n1:367001600",
        "-p", f"127.0.0.1:{PORT}:3306", "-v", f"{DATADIR}:/var/lib/mysql",
        "mysql:8.0", "--skip-log-bin", "--innodb-buffer-pool-size=1536M",
        "--innodb-buffer-pool-instances=1", "--innodb-buffer-pool-chunk-size=64M",
        "--tmp-table-size=16M", "--max-heap-table-size=16M", "--max-connections=64",
            "--transaction-isolation=READ-COMMITTED", "--innodb-adaptive-hash-index=OFF",
        ])
    deadline = time.time() + 600
    while time.time() < deadline:
        try:
            mysql("SELECT 1")
            break
        except subprocess.CalledProcessError:
            pass
        time.sleep(2)
    else:
        raise RuntimeError("TPC-H preparation database did not become ready")
    databases = set(mysql("SHOW DATABASES").split())
    if "tpch_sf10" not in databases:
        if "tpch" not in databases:
            raise RuntimeError(f"source database missing: {databases}")
        mysql("CREATE DATABASE tpch_sf10; " + " ".join(f"RENAME TABLE tpch.{t} TO tpch_sf10.{t};" for t in TABLES))
    evidence_path = Path(
        "/home/anonymous/program/resource_adaptive_mysql/history/unified_formal/"
        "six_session_20260721_v3/mysql/preflight/template_build_attempts/"
        "tpch_20260721T074817Z_4191854_4dbfab2a/tpch_generated_source_counts.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    counts = {name: int(value) for name, value in evidence["counts"].items()}
    if counts != EXPECTED:
        raise RuntimeError(f"frozen SF10 source-count evidence differs: {counts}")
    small_counts = {
        table: int(mysql(f"SELECT COUNT(*) FROM tpch_sf10.`{table}`").strip())
        for table in ("nation", "region")
    }
    if small_counts != {"nation": 25, "region": 5}:
        raise RuntimeError(f"restored small-table counts differ: {small_counts}")
    table_sizes = {
        row[0]: {"estimated_rows": int(row[1]), "bytes": int(row[2])}
        for row in tabular(
            "SELECT TABLE_NAME,COALESCE(TABLE_ROWS,0),COALESCE(DATA_LENGTH+INDEX_LENGTH,0) "
            "FROM information_schema.TABLES WHERE TABLE_SCHEMA='tpch_sf10' ORDER BY TABLE_NAME"
        )
    }
    if set(table_sizes) != set(TABLES) or any(item["bytes"] <= 0 for item in table_sizes.values()):
        raise RuntimeError(f"restored TPC-H physical table set/size invalid: {table_sizes}")
    columns = tabular(
        "SELECT TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='tpch_sf10' ORDER BY TABLE_NAME,ORDINAL_POSITION;",
    )
    indexes = tabular(
        "SELECT TABLE_NAME,INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME,INDEX_TYPE "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA='tpch_sf10' ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX;",
    )
    formal_password = common._password().replace("'", "''")
    root_hosts = mysql("SELECT Host FROM mysql.user WHERE User='root' ORDER BY Host").splitlines()
    mysql(" ".join(
        f"ALTER USER 'root'@'{host}' IDENTIFIED BY '{formal_password}';"
        for host in root_hosts if host
    ) + " FLUSH PRIVILEGES;")
    run(["docker", "stop", "-t", "120", CONTAINER])
    run(["docker", "rm", CONTAINER])
    run(["sudo", "-n", "chmod", "-R", "a+rX,u-w,g-w,o-w", str(DATADIR)])
    checksum_file = DATADIR / "checksum.md5"
    source_digest = hashlib.sha256(checksum_file.read_bytes()).hexdigest() if checksum_file.is_file() else None
    manifest = {
        "schema_version": 1, "freeze_id": "frozen_formal_config_20260721_v3",
        "global_campaign_id": "parallel24_20260721_v1", "status": "complete",
        "template_id": "p24_mysql80_tpch_sf10_from_stopped_worker_v1",
        "database": "tpch_sf10", "database_version": "8.0.45",
        "row_count_by_table": counts, "schema_sha256": canonical(columns),
        "index_sha256": canonical(indexes), "template_snapshot_sha256": source_digest,
        "source_datadir": str(SOURCE), "source_was_stopped_before_copy": True,
        "source_count_evidence_path": str(evidence_path),
        "source_count_evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "restored_exact_small_table_counts": small_counts,
        "restored_table_size_readback": table_sizes,
        "copy_method": "cp --archive --reflink=auto", "verified_epoch_seconds": time.time(),
    }
    temp = manifest_path.with_suffix(".tmp")
    temp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
