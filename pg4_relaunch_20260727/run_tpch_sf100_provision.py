#!/usr/bin/env python3
"""Module."""


import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Sequence


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


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run(
    command: Sequence[str],
    *,
    env: Dict[str, str],
    cwd: Optional[Path] = None,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    print("+", " ".join(command), flush=True)
    return subprocess.run(
        list(command),
        check=True,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def psql(
    args: argparse.Namespace,
    env: Dict[str, str],
    database: str,
    sql: str,
    *,
    capture: bool = False,
) -> str:
    process = run(
        [
            "docker",
            "exec",
            "-e",
            "PGPASSWORD=" + args.password,
            "-e",
            "PGAPPNAME=tpch-sf100-formal-provision",
            args.container,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            "127.0.0.1",
            "-p",
            str(args.container_port),
            "-U",
            args.user,
            "-d",
            database,
            "-At",
            "-c",
            sql,
        ],
        env=env,
        capture=capture,
    )
    return (process.stdout or "").strip()


def update_state(path: Path, state: Dict[str, Any], phase: str) -> None:
    state["phase"] = phase
    state["updated_at_utc"] = now()
    write_json(path, state)
    print(json.dumps({"phase": phase, "state": str(path)}, ensure_ascii=False), flush=True)


def build_config(args: argparse.Namespace, destination: Path) -> None:
    source = args.benchbase_root / "config/postgres/sample_tpch_config.xml"
    tree = ET.parse(source)
    root = tree.getroot()
    replacements = {
        "url": (
            f"jdbc:postgresql://{args.host}:{args.port}/{args.database}"
            "?sslmode=disable&ApplicationName=tpch-sf100-provision"
            "&reWriteBatchedInserts=true"
        ),
        "username": args.user,
        "password": args.password,
        "scalefactor": str(args.scale_factor),
        "batchsize": str(args.batch_size),
        "terminals": str(args.loader_terminals),
    }
    for name, value in replacements.items():
        element = root.find(name)
        if element is None:
            raise RuntimeError(f"BenchBase config missing <{name}>")
        element.text = value
    destination.parent.mkdir(parents=True, exist_ok=True)
    tree.write(destination, encoding="UTF-8", xml_declaration=True)


def benchbase(
    args: argparse.Namespace,
    env: Dict[str, str],
    config: Path,
    *,
    create: bool,
    load: bool,
) -> None:
    run(
        [
            "java",
            f"-Xmx{args.java_xmx}",
            f"-Dbenchbase.tpch.lineitemPartitions={args.lineitem_partitions}",
            "-cp",
            "benchbase.jar:lib/*",
            "com.oltpbenchmark.DBWorkload",
            "-b",
            "tpch",
            "-c",
            str(config),
            f"--create={str(create).lower()}",
            f"--load={str(load).lower()}",
        ],
        env=env,
        cwd=args.benchbase_root,
    )


def validate_identifier(value: str) -> None:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
        raise ValueError(f"unsafe PostgreSQL identifier: {value!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--benchbase-root", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=63301)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default="postgres")
    parser.add_argument("--database", default="fullgrid_tpch_sf100")
    parser.add_argument("--template-database", default="template_fullgrid_tpch_sf100")
    parser.add_argument("--container", default="pg5_tpch_ap_slot_node01_20260726")
    parser.add_argument("--container-port", type=int, default=64304)
    parser.add_argument("--scale-factor", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--loader-terminals", type=int, default=16)
    parser.add_argument("--lineitem-partitions", type=int, default=16)
    parser.add_argument("--java-xmx", default="12g")
    parser.add_argument("--minimum-free-bytes", type=int, default=350 * 1024 ** 3)
    args = parser.parse_args()

    for identifier in (args.user, args.database, args.template_database):
        validate_identifier(identifier)
    if args.scale_factor != 100:
        raise ValueError("this frozen formal provisioner requires scale factor 100")
    if args.database == args.template_database:
        raise ValueError("database and template database must differ")

    args.output_root = args.output_root.resolve()
    args.benchbase_root = args.benchbase_root.resolve()
    args.output_root.mkdir(parents=True, exist_ok=True)
    lock_handle = (args.output_root / "provision.lock").open("w", encoding="utf-8")
    fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    env = dict(os.environ)
    env["PGPASSWORD"] = args.password
    env["PGAPPNAME"] = "tpch-sf100-formal-provision"
    state_path = args.output_root / "provision_state.json"
    state: Dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "started_at_utc": now(),
        "phase": "preflight",
        "scale_factor": args.scale_factor,
        "database": args.database,
        "template_database": args.template_database,
        "container": args.container,
        "external_processes_terminated": False,
        "existing_sf10_preserved": True,
    }
    write_json(state_path, state)

    try:
        jar = args.benchbase_root / "benchbase.jar"
        sample_config = args.benchbase_root / "config/postgres/sample_tpch_config.xml"
        if not jar.is_file() or not sample_config.is_file():
            raise FileNotFoundError("incomplete BenchBase bundle")
        free_bytes = shutil.disk_usage(args.output_root).free
        if free_bytes < args.minimum_free_bytes:
            raise RuntimeError(
                f"insufficient free space: {free_bytes} < {args.minimum_free_bytes}"
            )
        existing = psql(
            args,
            env,
            "postgres",
            (
                "SELECT datname FROM pg_database "
                f"WHERE datname IN ('{args.database}','{args.template_database}') "
                "ORDER BY datname;"
            ),
            capture=True,
        )
        if existing:
            raise RuntimeError(f"refusing to overwrite existing databases: {existing}")

        config_path = args.output_root / "benchbase_tpch_sf100.xml"
        build_config(args, config_path)
        state.update(
            {
                "benchbase_jar_sha256": sha256_file(jar),
                "benchbase_config_sha256": sha256_file(config_path),
                "free_bytes_at_start": free_bytes,
            }
        )
        update_state(state_path, state, "create_database")
        psql(
            args,
            env,
            "postgres",
            f"CREATE DATABASE {args.database} OWNER {args.user} TEMPLATE template0;",
        )

        update_state(state_path, state, "create_schema")
        benchbase(args, env, config_path, create=True, load=False)
        psql(args, env, args.database, "ALTER TABLE lineitem SET UNLOGGED;")
        for sql in (
            "ALTER SYSTEM SET synchronous_commit='off';",
            "ALTER SYSTEM SET max_wal_size='32GB';",
            "ALTER SYSTEM SET checkpoint_timeout='30min';",
            "SELECT pg_reload_conf();",
        ):
            psql(args, env, "postgres", sql)

        update_state(state_path, state, "load_sf100")
        load_error: Optional[BaseException] = None
        try:
            benchbase(args, env, config_path, create=False, load=True)
        except BaseException as exc:
            load_error = exc
        finally:
            update_state(state_path, state, "restore_durability")
            cleanup_errors: List[str] = []
            for database, sql in (
                (args.database, "ALTER TABLE lineitem SET LOGGED;"),
                ("postgres", "ALTER SYSTEM RESET synchronous_commit;"),
                ("postgres", "ALTER SYSTEM RESET max_wal_size;"),
                ("postgres", "ALTER SYSTEM RESET checkpoint_timeout;"),
                ("postgres", "SELECT pg_reload_conf();"),
                ("postgres", "CHECKPOINT;"),
            ):
                try:
                    psql(args, env, database, sql)
                except Exception as exc:  # continue every restoration step
                    cleanup_errors.append(str(exc))
            if cleanup_errors:
                state["durability_restore_errors"] = cleanup_errors
                write_json(state_path, state)
            if load_error is not None:
                raise load_error
            if cleanup_errors:
                raise RuntimeError("durability restoration failed")

        update_state(state_path, state, "analyze_and_verify")
        psql(args, env, args.database, "VACUUM (ANALYZE);")
        counts_sql = " UNION ALL ".join(
            f"SELECT '{table}', count(*)::bigint FROM {table}" for table in TABLES
        )
        raw_counts = psql(args, env, args.database, counts_sql, capture=True)
        row_counts = {
            line.split("|", 1)[0]: int(line.split("|", 1)[1])
            for line in raw_counts.splitlines()
            if "|" in line
        }
        database_size = int(
            psql(
                args,
                env,
                args.database,
                "SELECT pg_database_size(current_database());",
                capture=True,
            )
        )
        expected_exact = {
            "region": 5,
            "nation": 25,
            "supplier": 1_000_000,
            "customer": 15_000_000,
            "part": 20_000_000,
            "partsupp": 80_000_000,
            "orders": 150_000_000,
        }
        mismatches = {
            table: {"expected": expected, "actual": row_counts.get(table)}
            for table, expected in expected_exact.items()
            if row_counts.get(table) != expected
        }
        lineitem_count = row_counts.get("lineitem", 0)
        if not 590_000_000 <= lineitem_count <= 610_000_000:
            mismatches["lineitem"] = {
                "expected_range": [590_000_000, 610_000_000],
                "actual": lineitem_count,
            }
        if mismatches:
            raise RuntimeError(f"TPC-H SF100 row-count mismatch: {mismatches}")

        update_state(state_path, state, "create_immutable_template")
        psql(
            args,
            env,
            "postgres",
            (
                f"CREATE DATABASE {args.template_database} "
                f"WITH TEMPLATE {args.database} OWNER {args.user};"
            ),
        )
        psql(
            args,
            env,
            "postgres",
            (
                f"ALTER DATABASE {args.template_database} "
                "WITH IS_TEMPLATE true ALLOW_CONNECTIONS false;"
            ),
        )
        template_size = int(
            psql(
                args,
                env,
                "postgres",
                f"SELECT pg_database_size('{args.template_database}');",
                capture=True,
            )
        )
        manifest = {
            "schema_version": 1,
            "status": "complete",
            "benchmark": "tpch",
            "scale_factor": 100,
            "generator": "BenchBase",
            "benchbase_jar_sha256": sha256_file(jar),
            "benchbase_config_sha256": sha256_file(config_path),
            "database": args.database,
            "template_database": args.template_database,
            "row_counts": row_counts,
            "pg_database_size_bytes": database_size,
            "template_database_size_bytes": template_size,
            "created_at_utc": now(),
            "cold_snapshot_status": "pending",
        }
        manifest_path = args.output_root / "dataset_manifest.json"
        write_json(manifest_path, manifest)
        state["status"] = "complete"
        state["dataset_manifest_path"] = str(manifest_path)
        update_state(state_path, state, "complete_template_ready_snapshot_pending")
        return 0
    except BaseException as exc:
        state["status"] = "failed"
        state["error_type"] = type(exc).__name__
        state["error"] = str(exc)
        update_state(state_path, state, "failed")
        raise


if __name__ == "__main__":
    sys.exit(main())
