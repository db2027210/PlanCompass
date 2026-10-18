#!/usr/bin/env python3
"""Restore the existing PostgreSQL TPC-H SF10 dump on node01."""

import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Dict, List


CONTAINER = "pg5_tpch_ap_slot_node01_20260726"
HOST_DUMP = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "source_sf10_migration_20260727/tpch_sf10_pg17.dump"
)
CONTAINER_DUMP = "/tmp/tpch_sf10_pg17.dump"
EXPECTED_SHA256 = "5300a00ce288b67293bbbef7cf9879a4cd932b01e9c956f112614a6e115eea32"
DATABASE = "fullgrid_tpch_sf10_migrated_20260727"
TEMPLATE_DATABASE = "template_fullgrid_tpch_sf10_migrated_20260727"
OUTPUT_ROOT = HOST_DUMP.parent
STATE_PATH = OUTPUT_ROOT / "restore_state.json"
MANIFEST_PATH = OUTPUT_ROOT / "dataset_manifest.json"
TABLES = (
    "region",
    "nation",
    "supplier",
    "customer",
    "part",
    "partsupp",
    "orders",
    "lineitem",
)


def run(command: List[str], *, capture: bool = False) -> str:
    process = subprocess.run(
        command,
        check=False,
        universal_newlines=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout or "").strip()
        raise RuntimeError(
            f"command failed ({process.returncode}): {' '.join(command)}: {detail}"
        )
    return (process.stdout or "").strip()


def docker_command(*args: str) -> List[str]:
    return ["docker", "exec", CONTAINER, *args]


def psql(sql: str, *, database: str = "postgres") -> str:
    return run(
        docker_command(
            "/usr/lib/postgresql/17/bin/psql",
            "-h",
            "127.0.0.1",
            "-p",
            "64304",
            "-U",
            "postgres",
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-Atc",
            sql,
        ),
        capture=True,
    )


def write_state(status: str, phase: str, **extra: object) -> None:
    payload = {
        "schema_version": 1,
        "status": status,
        "phase": phase,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "database": DATABASE,
        "template_database": TEMPLATE_DATABASE,
        "source_dump": str(HOST_DUMP),
        "source_dump_sha256": EXPECTED_SHA256,
        **extra,
    }
    STATE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        write_state("running", "verify_source")
        actual_sha256 = file_sha256(HOST_DUMP)
        if actual_sha256 != EXPECTED_SHA256:
            raise RuntimeError(
                f"source SHA256 mismatch: {actual_sha256} != {EXPECTED_SHA256}"
            )
        existing = set(
            psql(
                "SELECT datname FROM pg_database "
                f"WHERE datname IN ('{DATABASE}','{TEMPLATE_DATABASE}')"
            ).splitlines()
        )
        if existing:
            raise RuntimeError(
                "refusing to overwrite pre-existing migration databases: "
                + ",".join(sorted(existing))
            )

        write_state("running", "copy_dump_into_container")
        run(["docker", "cp", str(HOST_DUMP), f"{CONTAINER}:{CONTAINER_DUMP}"])
        run(
            docker_command(
                "/usr/lib/postgresql/17/bin/pg_restore",
                "-l",
                CONTAINER_DUMP,
            ),
            capture=True,
        )

        write_state("running", "create_database")
        psql(
            f'CREATE DATABASE "{DATABASE}" '
            "WITH TEMPLATE template0 OWNER postgres ENCODING 'UTF8'"
        )
        write_state("running", "restore_dump")
        run(
            docker_command(
                "/usr/lib/postgresql/17/bin/pg_restore",
                "-h",
                "127.0.0.1",
                "-p",
                "64304",
                "-U",
                "postgres",
                "--dbname",
                DATABASE,
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--jobs",
                "8",
                CONTAINER_DUMP,
            )
        )

        write_state("running", "analyze_and_verify")
        psql("VACUUM (ANALYZE)", database=DATABASE)
        row_counts: Dict[str, int] = {}
        for table in TABLES:
            row_counts[table] = int(
                psql(f'SELECT count(*) FROM "{table}"', database=DATABASE)
            )
        database_size = int(
            psql(f"SELECT pg_database_size('{DATABASE}')")
        )

        write_state("running", "create_immutable_template")
        psql(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname='{DATABASE}' AND pid<>pg_backend_pid()"
        )
        psql(
            f'CREATE DATABASE "{TEMPLATE_DATABASE}" '
            f'WITH TEMPLATE "{DATABASE}" OWNER postgres'
        )
        psql(
            f'ALTER DATABASE "{TEMPLATE_DATABASE}" '
            "WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
        )
        manifest = {
            "schema_version": 1,
            "benchmark": "tpch",
            "scale_factor": 10,
            "database": DATABASE,
            "template_database": TEMPLATE_DATABASE,
            "database_size_bytes": database_size,
            "table_row_counts": row_counts,
            "source_dump": str(HOST_DUMP),
            "source_dump_sha256": actual_sha256,
            "postgresql_version": psql("SHOW server_version"),
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "dataset_policy": "migrated_existing_sf10_no_scale_expansion",
        }
        MANIFEST_PATH.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_state(
            "complete",
            "complete",
            dataset_manifest=str(MANIFEST_PATH),
            database_size_bytes=database_size,
            table_row_counts=row_counts,
        )
        print(json.dumps(manifest, ensure_ascii=False), flush=True)
        return 0
    except Exception as exc:
        write_state("failed", "failed", error=f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
