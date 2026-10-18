#!/usr/bin/env python3
"""Load one isolated JOB/TPC-H/TPC-C database and immutable replay template."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


JOB_TABLES = (
    "aka_name",
    "aka_title",
    "cast_info",
    "char_name",
    "comp_cast_type",
    "company_name",
    "company_type",
    "complete_cast",
    "info_type",
    "keyword",
    "kind_type",
    "link_type",
    "movie_companies",
    "movie_info",
    "movie_info_idx",
    "movie_keyword",
    "movie_link",
    "name",
    "person_info",
    "role_type",
    "title",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_tpch_row_counts(
    args: argparse.Namespace,
    env: Dict[str, str],
) -> Dict[str, Any]:
    """Verify that a partitioned TPC-H seed is complete before templating it."""
    config = Path(args.benchbase_config).resolve()
    scale_text = ET.parse(config).getroot().findtext("scalefactor")
    if scale_text is None:
        raise RuntimeError(f"TPC-H config has no scalefactor: {config}")
    scale_factor = float(scale_text)
    if scale_factor <= 0 or not scale_factor.is_integer():
        raise RuntimeError(
            f"TPC-H seed verification requires a positive integer scale: {scale_factor}"
        )
    scale = int(scale_factor)
    tables = (
        "region",
        "nation",
        "supplier",
        "customer",
        "part",
        "partsupp",
        "orders",
        "lineitem",
    )
    row_counts = {
        table: int(
            psql_scalar(
                args,
                env,
                args.database,
                f"SELECT count(*) FROM {table};",
            )
        )
        for table in tables
    }
    expected = {
        "region": 5,
        "nation": 25,
        "supplier": 10_000 * scale,
        "customer": 150_000 * scale,
        "part": 200_000 * scale,
        "partsupp": 800_000 * scale,
        "orders": 1_500_000 * scale,
    }
    mismatches = {
        table: {"expected": count, "actual": row_counts[table]}
        for table, count in expected.items()
        if row_counts[table] != count
    }
    lineitem_lower = int(expected["orders"] * 3.9)
    lineitem_upper = int(expected["orders"] * 4.1)
    if not lineitem_lower <= row_counts["lineitem"] <= lineitem_upper:
        mismatches["lineitem"] = {
            "expected_range": [lineitem_lower, lineitem_upper],
            "actual": row_counts["lineitem"],
        }
    verification = {
        "scale_factor": scale,
        "row_counts": row_counts,
        "fixed_expected_counts": expected,
        "lineitem_expected_range": [lineitem_lower, lineitem_upper],
        "passed": not mismatches,
        "mismatches": mismatches,
    }
    if mismatches:
        raise RuntimeError(f"TPC-H seed row-count verification failed: {mismatches}")
    return verification


def acquire_endpoint_lease(args: argparse.Namespace) -> Tuple[Any, Dict[str, Any]]:
    """Hold the same kernel endpoint lock used by formal controllers."""
    lease_dir = Path(args.endpoint_lease_dir).resolve()
    lease_dir.mkdir(parents=True, exist_ok=True)
    endpoint = f"{args.host}:{args.port}"
    safe_key = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        f"{args.vm_name}_{endpoint}",
    )
    lease_path = lease_dir / f"{safe_key}.lock"
    handle = lease_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.seek(0)
        owner = handle.read().strip() or "unknown owner"
        handle.close()
        raise RuntimeError(
            f"physical isolation violation: endpoint {endpoint} / VM "
            f"{args.vm_name} already leased by {owner}"
        ) from exc
    owner = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "purpose": "isolated_generic_database_provision",
        "vm_name": args.vm_name,
        "postgres_endpoint": endpoint,
        "acquired_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(owner, ensure_ascii=False, sort_keys=True) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
    return handle, {**owner, "lease_path": str(lease_path)}


def host_qemu_identity(vm_name: str) -> Dict[str, Any]:
    """Read-only fail-closed QEMU identity fallback for unavailable libvirt."""
    matches: List[Dict[str, Any]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        argv = [
            value.decode("utf-8", errors="replace")
            for value in raw.split(b"\0")
            if value
        ]
        if not argv or not Path(argv[0]).name.startswith("qemu-system-"):
            continue

        def option_value(name: str) -> str:
            for index, value in enumerate(argv[:-1]):
                if value == name:
                    return argv[index + 1]
            return ""

        name_value = option_value("-name").split(",", 1)[0]
        guest_name = (
            name_value[len("guest=") :]
            if name_value.startswith("guest=")
            else name_value
        )
        if guest_name != vm_name:
            continue
        matches.append(
            {
                "pid": int(entry.name),
                "vm_name": guest_name,
                "uuid": option_value("-uuid").strip().lower(),
                "command_source": str(entry / "cmdline"),
            }
        )
    if len(matches) != 1 or not matches[0]["uuid"]:
        raise RuntimeError(
            f"expected exactly one UUID-bearing QEMU process for {vm_name!r}, "
            f"found {len(matches)}"
        )
    return matches[0]


def verify_vm_identity(args: argparse.Namespace) -> Dict[str, Any]:
    """Prove host VM UUID and guest DMI UUID without changing VM state."""
    expected_uuid = str(args.expected_vm_uuid).strip().lower()
    identity_source = "libvirt"
    qemu_process: Dict[str, Any] = {}
    virsh_error = ""
    try:
        state_proc = subprocess.run(
            ["virsh", "-c", "qemu:///system", "domstate", args.vm_name],
            check=True,
            capture_output=True,
            text=True,
            timeout=30.0,
        )
        state = state_proc.stdout.strip().lower()
        if not state.startswith("running"):
            raise RuntimeError(f"VM is not running: {state!r}")
        uuid_proc = subprocess.run(
            ["virsh", "-c", "qemu:///system", "domuuid", args.vm_name],
            check=True,
            capture_output=True,
            text=True,
            timeout=30.0,
        )
        host_uuid = uuid_proc.stdout.strip().lower()
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        virsh_error = f"{type(exc).__name__}: {exc}"
        qemu_process = host_qemu_identity(args.vm_name)
        host_uuid = str(qemu_process["uuid"]).lower()
        state = "running (host-qemu-process-proof)"
        identity_source = "host_qemu_process_fallback"
    if host_uuid != expected_uuid:
        raise RuntimeError(
            f"host VM UUID {host_uuid!r} does not match expected "
            f"{expected_uuid!r} for {args.vm_name!r}"
        )
    guest_proc = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(args.ssh_port),
            args.ssh_target,
            "sudo",
            "-n",
            "cat",
            "/sys/class/dmi/id/product_uuid",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    guest_uuid = guest_proc.stdout.strip().lower()
    if guest_uuid != expected_uuid:
        raise RuntimeError(
            f"guest endpoint UUID {guest_uuid!r} does not match expected "
            f"{expected_uuid!r} for {args.vm_name!r}"
        )
    return {
        "passed": True,
        "vm_name": args.vm_name,
        "domain_state": state,
        "expected_vm_uuid": expected_uuid,
        "host_vm_uuid": host_uuid,
        "guest_product_uuid": guest_uuid,
        "ssh_target": args.ssh_target,
        "identity_source": identity_source,
        "qemu_process": qemu_process or None,
        "virsh_error": virsh_error or None,
    }


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_command(
    command: Sequence[str],
    *,
    env: Dict[str, str],
    cwd: Path,
    timeout_s: float,
) -> Dict[str, Any]:
    started = time.time()
    proc = subprocess.run(
        list(command),
        cwd=str(cwd),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=max(1.0, timeout_s),
    )
    row = {
        "command": list(command),
        "returncode": int(proc.returncode),
        "elapsed_s": time.time() - started,
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-4000:],
    }
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed exit={proc.returncode}: {list(command)!r}; "
            f"{((proc.stdout or '') + (proc.stderr or ''))[-1000:]}"
        )
    return row


def psql_command(
    args: argparse.Namespace,
    database: str,
    *extra: str,
) -> List[str]:
    return [
        "psql",
        "-h",
        args.host,
        "-p",
        str(args.port),
        "-U",
        args.user,
        "-d",
        database,
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        *extra,
    ]


def psql_scalar(
    args: argparse.Namespace,
    env: Dict[str, str],
    database: str,
    sql: str,
) -> str:
    proc = subprocess.run(
        psql_command(args, database, "-A", "-t", "-c", sql),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=60.0,
    )
    if proc.returncode != 0:
        raise RuntimeError(((proc.stdout or "") + (proc.stderr or "")).strip())
    return (proc.stdout or "").strip()


def database_exists(
    args: argparse.Namespace,
    env: Dict[str, str],
    database: str,
) -> bool:
    quoted = database.replace("'", "''")
    return psql_scalar(
        args,
        env,
        "postgres",
        f"SELECT count(*) FROM pg_database WHERE datname='{quoted}';",
    ) == "1"


def assert_endpoint_unoccupied(
    args: argparse.Namespace,
    env: Dict[str, str],
) -> List[Dict[str, Any]]:
    raw = psql_scalar(
        args,
        env,
        "postgres",
        (
            "SELECT COALESCE(json_agg(json_build_object("
            "'pid',pid,'database',datname,'application_name',application_name,"
            "'client_addr',client_addr,'state',state) ORDER BY pid),"
            "'[]'::json)::text FROM pg_stat_activity "
            "WHERE backend_type='client backend' AND pid<>pg_backend_pid();"
        ),
    )
    sessions = json.loads(raw or "[]")
    if not isinstance(sessions, list):
        raise RuntimeError(f"invalid pg_stat_activity payload: {sessions!r}")
    if sessions:
        raise RuntimeError(
            "dedicated endpoint is occupied; no session was terminated: "
            f"{sessions}"
        )
    return sessions


def reset_database(
    args: argparse.Namespace,
    env: Dict[str, str],
    database: str,
    *,
    steps: List[Dict[str, Any]],
) -> None:
    occupied = int(
        psql_scalar(
            args,
            env,
            "postgres",
            "SELECT count(*) FROM pg_stat_activity "
            f"WHERE datname='{database}' AND backend_type='client backend' "
            "AND pid<>pg_backend_pid();",
        )
    )
    if occupied:
        raise RuntimeError(
            f"database {database!r} is occupied by {occupied} session(s); "
            "no session was terminated"
        )
    # DROP/CREATE DATABASE must each be a top-level command.  Sending both in
    # one psql -c request wraps them in one implicit transaction and PostgreSQL
    # rejects DROP DATABASE inside that transaction.
    for sql in (
        f"DROP DATABASE IF EXISTS {database};",
        f"CREATE DATABASE {database} OWNER {args.user};",
    ):
        steps.append(
            run_command(
                psql_command(args, "postgres", "-c", sql),
                env=env,
                cwd=Path.cwd(),
                timeout_s=600.0,
            )
        )


def load_job(
    args: argparse.Namespace,
    env: Dict[str, str],
    *,
    steps: List[Dict[str, Any]],
) -> None:
    sql_dir = Path(args.job_sql_dir).resolve()
    data_dir = Path(args.job_data_dir).resolve()
    for required in (sql_dir / "schema.sql", sql_dir / "fkindexes.sql"):
        if not required.is_file():
            raise FileNotFoundError(required)
    steps.append(
        run_command(
            psql_command(args, args.database, "-f", str(sql_dir / "schema.sql")),
            env=env,
            cwd=sql_dir,
            timeout_s=600.0,
        )
    )
    for table in JOB_TABLES:
        csv_path = data_dir / f"{table}.csv"
        if not csv_path.is_file():
            raise FileNotFoundError(csv_path)
        copy_sql = (
            f"\\copy {table} FROM '{csv_path}' "
            "WITH (FORMAT csv, ESCAPE '\\')"
        )
        row = run_command(
            psql_command(args, args.database, "-c", copy_sql),
            env=env,
            cwd=data_dir,
            timeout_s=args.load_timeout_s,
        )
        row["table"] = table
        row["source_csv"] = str(csv_path)
        steps.append(row)
    steps.append(
        run_command(
            psql_command(args, args.database, "-f", str(sql_dir / "fkindexes.sql")),
            env=env,
            cwd=sql_dir,
            timeout_s=args.load_timeout_s,
        )
    )


def load_benchbase(
    args: argparse.Namespace,
    env: Dict[str, str],
    *,
    steps: List[Dict[str, Any]],
) -> None:
    root = Path(args.benchbase_root).resolve()
    config = Path(args.benchbase_config).resolve()
    for required in (root / "benchbase.jar", config):
        if not required.is_file():
            raise FileNotFoundError(required)

    def benchbase_phase(*, create: bool, load: bool) -> None:
        steps.append(
            run_command(
                [
                    "java",
                    f"-Xmx{args.java_xmx}",
                    f"-Dbenchbase.tpch.lineitemPartitions={args.tpch_lineitem_partitions}",
                    "-jar",
                    "benchbase.jar",
                    "-b",
                    args.benchmark,
                    "-c",
                    str(config),
                    f"--create={str(create).lower()}",
                    f"--load={str(load).lower()}",
                ],
                env=env,
                cwd=root,
                timeout_s=args.load_timeout_s,
            )
        )

    def sql_step(database: str, sql: str, timeout_s: float = 600.0) -> None:
        steps.append(
            run_command(
                psql_command(args, database, "-c", sql),
                env=env,
                cwd=Path.cwd(),
                timeout_s=timeout_s,
            )
        )

    if not args.fast_seed_load:
        benchbase_phase(create=True, load=True)
        return
    if args.benchmark != "tpch":
        raise ValueError("--fast-seed-load currently supports only TPC-H")

    # Create the schema first so the largest table can avoid per-row WAL
    # serialization during seed construction.  SET LOGGED below rewrites it
    # before verification/template creation, so formal runs still receive a
    # normal crash-safe table.
    benchbase_phase(create=True, load=False)
    sql_step(args.database, "ALTER TABLE lineitem SET UNLOGGED;")
    for sql in (
        "ALTER SYSTEM SET synchronous_commit='off';",
        "ALTER SYSTEM SET max_wal_size='16GB';",
        "ALTER SYSTEM SET checkpoint_timeout='30min';",
        "SELECT pg_reload_conf();",
    ):
        sql_step("postgres", sql)

    primary_error: Optional[Exception] = None
    cleanup_error: Optional[Exception] = None
    try:
        benchbase_phase(create=False, load=True)
    except Exception as exc:  # noqa: BLE001 - restore durability settings
        primary_error = exc
    finally:
        cleanup_commands = (
            (args.database, "ALTER TABLE lineitem SET LOGGED;"),
            ("postgres", "ALTER SYSTEM RESET synchronous_commit;"),
            ("postgres", "ALTER SYSTEM RESET max_wal_size;"),
            ("postgres", "ALTER SYSTEM RESET checkpoint_timeout;"),
            ("postgres", "SELECT pg_reload_conf();"),
            ("postgres", "CHECKPOINT;"),
        )
        for database, sql in cleanup_commands:
            try:
                sql_step(database, sql, timeout_s=args.load_timeout_s)
            except Exception as exc:  # noqa: BLE001 - try every restore
                if cleanup_error is None:
                    cleanup_error = exc
    if primary_error is not None:
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error


def create_immutable_template(
    args: argparse.Namespace,
    env: Dict[str, str],
    *,
    steps: List[Dict[str, Any]],
) -> None:
    occupied = int(
        psql_scalar(
            args,
            env,
            "postgres",
            "SELECT count(*) FROM pg_stat_activity "
            f"WHERE datname IN ('{args.database}', '{args.template_database}') "
            "AND backend_type='client backend' AND pid<>pg_backend_pid();",
        )
    )
    if occupied:
        raise RuntimeError(
            "template creation refused because source/template has "
            f"{occupied} client session(s); no session was terminated"
        )
    for sql in (
        f"DROP DATABASE IF EXISTS {args.template_database};",
        f"CREATE DATABASE {args.template_database} WITH TEMPLATE {args.database} OWNER {args.user};",
        (
            f"ALTER DATABASE {args.template_database} WITH "
            "IS_TEMPLATE true ALLOW_CONNECTIONS false;"
        ),
    ):
        steps.append(
            run_command(
                psql_command(args, "postgres", "-c", sql),
                env=env,
                cwd=Path.cwd(),
                timeout_s=args.load_timeout_s,
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", choices=("job", "tpch", "tpcc"), required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=25432)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default='CHANGE_ME')
    parser.add_argument("--database", required=True)
    parser.add_argument("--template-database", required=True)
    parser.add_argument("--audit-path", required=True)
    parser.add_argument("--vm-name", required=True)
    parser.add_argument("--expected-vm-uuid", required=True)
    parser.add_argument("--endpoint-lease-dir", required=True)
    parser.add_argument("--ssh-target", required=True)
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument(
        "--template-only",
        action="store_true",
        help="preserve an already loaded target database and only rebuild its immutable template",
    )
    parser.add_argument(
        "--job-sql-dir",
        default="/home/anonymous/dataset/pg_data/join-order-benchmark",
    )
    parser.add_argument(
        "--job-data-dir",
        default="/home/anonymous/dataset/pg_data/jobdata",
    )
    parser.add_argument("--benchbase-root", default="")
    parser.add_argument("--benchbase-config", default="")
    parser.add_argument("--java-xmx", default="4g")
    parser.add_argument("--tpch-lineitem-partitions", type=int, default=1)
    parser.add_argument(
        "--fast-seed-load",
        action="store_true",
        help="Use batched TPC-H seed loading with temporary durability relaxations.",
    )
    parser.add_argument("--load-timeout-s", type=float, default=14400.0)
    args = parser.parse_args()

    if args.tpch_lineitem_partitions < 1:
        parser.error("--tpch-lineitem-partitions must be positive")
    if args.benchmark != "tpch" and args.tpch_lineitem_partitions != 1:
        parser.error("--tpch-lineitem-partitions is only valid for TPC-H")
    if args.database == args.template_database:
        raise ValueError("database and template database must differ")
    for label, identifier in (
        ("database", args.database),
        ("template database", args.template_database),
        ("user", args.user),
    ):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
            raise ValueError(f"unsafe PostgreSQL {label}: {identifier!r}")
    env = dict(os.environ)
    env["PGPASSWORD"] = args.password
    env["PGAPPNAME"] = "double-loop-sgd-generic-provision"
    audit_path = Path(args.audit_path).resolve()
    audit: Dict[str, Any] = {
        "event": "isolated_generic_database_provision",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "benchmark": args.benchmark,
        "postgres_endpoint": f"{args.host}:{args.port}",
        "database": args.database,
        "template_database": args.template_database,
        "historical_optimizer_inputs_used": False,
        "external_sessions_terminated": False,
        "source_file": str(Path(__file__).resolve()),
        "source_sha256": sha256_file(Path(__file__).resolve()),
        "tpch_lineitem_partitions": args.tpch_lineitem_partitions,
        "steps": [],
    }
    lease_handle: Any = None
    try:
        lease_handle, audit["endpoint_kernel_lease"] = acquire_endpoint_lease(
            args
        )
        audit["vm_identity"] = verify_vm_identity(args)
        audit["preexisting_client_sessions"] = assert_endpoint_unoccupied(args, env)
        existing = {
            args.database: database_exists(args, env, args.database),
            args.template_database: database_exists(
                args, env, args.template_database
            ),
        }
        audit["preexisting_databases"] = existing
        audit["mode"] = "template_only" if args.template_only else "full_load"
        if args.template_only:
            if not existing.get(args.database):
                raise RuntimeError(
                    f"template-only repair requires loaded database {args.database!r}"
                )
        else:
            if any(existing.values()) and not args.rebuild:
                raise RuntimeError(
                    f"target/template already exists; use --rebuild explicitly: {existing}"
                )
            reset_database(args, env, args.template_database, steps=audit["steps"])
            reset_database(args, env, args.database, steps=audit["steps"])
            if args.benchmark == "job":
                load_job(args, env, steps=audit["steps"])
            else:
                if not args.benchbase_root or not args.benchbase_config:
                    raise ValueError(
                        "TPC-H/TPC-C require --benchbase-root and --benchbase-config"
                    )
                load_benchbase(args, env, steps=audit["steps"])
        audit["steps"].append(
            run_command(
                psql_command(args, args.database, "-c", "ANALYZE; CHECKPOINT;"),
                env=env,
                cwd=Path.cwd(),
                timeout_s=args.load_timeout_s,
            )
        )
        audit["verification"] = {
            "user_table_count": int(
                psql_scalar(
                    args,
                    env,
                    args.database,
                    "SELECT count(*) FROM pg_class WHERE relkind='r' AND relnamespace=(SELECT oid FROM pg_namespace WHERE nspname='public');",
                )
            ),
            "database_size_bytes": int(
                psql_scalar(
                    args,
                    env,
                    args.database,
                    "SELECT pg_database_size(current_database());",
                )
            ),
        }
        if audit["verification"]["user_table_count"] <= 0:
            raise RuntimeError("loaded database has no public user tables")
        if args.benchmark == "tpch":
            audit["verification"]["tpch_row_counts"] = verify_tpch_row_counts(
                args,
                env,
            )
        create_immutable_template(args, env, steps=audit["steps"])
        audit["verification"]["template_size_bytes"] = int(
            psql_scalar(
                args,
                env,
                "postgres",
                f"SELECT pg_database_size('{args.template_database}');",
            )
        )
        audit["status"] = "ok"
        write_json(audit_path, audit)
    except Exception as exc:
        audit["status"] = "failed"
        audit["error"] = f"{type(exc).__name__}: {exc}"
        write_json(audit_path, audit)
        raise
    finally:
        if lease_handle is not None:
            fcntl.flock(lease_handle.fileno(), fcntl.LOCK_UN)
            lease_handle.close()
    print(str(audit_path), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
