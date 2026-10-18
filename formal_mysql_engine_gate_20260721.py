#!/usr/bin/env python3
"""Build and validate the frozen MySQL v3 templates and engine gate.

This program is deliberately fail closed.  It consumes the campaign copy of
the frozen configuration, never reads a data source below /disk3, and holds
the common host flock for every database mutation.  Long operations are
append-only and resumable: a completed template is never overwritten and an
incomplete build attempt remains available as negative evidence.
"""

from __future__ import annotations

import argparse
import configparser
import csv
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence
import xml.etree.ElementTree as ET

from formal_session_runtime_20260721 import (
    FREEZE_ID,
    GLOBAL_CAMPAIGN_ID,
    GLOBAL_ROOT,
    SHARED_FROZEN_ROOT,
    ProtocolError,
    load_json,
    sha256_file,
    utc_now,
    validate_shared_bundle,
)


SESSION_ID = "mysql_brute_force"
ENGINE = "mysql"
PREFLIGHT_ROOT = GLOBAL_ROOT / ENGINE / "preflight"
ATTEMPT_ROOT = PREFLIGHT_ROOT / "template_build_attempts"
MYSQL_CREDENTIAL = Path("/home/anonymous/.my.cnf")
HYBENCH_ROOT = Path("/home/anonymous/HyBench-2024-master")
HYBENCH_DATA = HYBENCH_ROOT / "Data_10x"
HYBENCH_JAR = Path(
    "/disk2/resource_adaptive_mysql/gd_20260718/hybench_client/target/"
    "HyBench-1.0-SNAPSHOT-jar-with-dependencies.jar"
)
MYSQL_CONNECTOR = HYBENCH_ROOT / "lib/mysql-connector-java-8.0.30.jar"
JOB_DATA = Path("/home/anonymous/dataset/pg_data/jobdata")
JOB_SQL_ROOT = Path("/home/anonymous/dataset/pg_data/join-order-benchmark")
BENCHBASE_ROOT = Path("/home/anonymous/benchbase-mysql")
BENCHBASE_JAR = BENCHBASE_ROOT / "benchbase.jar"
TPCH_COUNT_AUDIT_SOURCE = (
    Path(__file__).resolve().parent
    / "formal_clients_20260721/com/oltpbenchmark/benchmarks/tpch/util/TpchSfCountAudit.java"
)
TPCH_COUNT_AUDIT_CLASSES = Path(__file__).resolve().parent / "formal_clients_20260721/classes"
TPCH_COUNT_AUDIT_CLASS = (
    TPCH_COUNT_AUDIT_CLASSES
    / "com/oltpbenchmark/benchmarks/tpch/util/TpchSfCountAudit.class"
)
HYBENCH_CANONICAL_TABLE_NAMES = (
    "checkingAccount", "savingAccount", "company", "customer",
    "checking", "loanapps", "loantrans", "transfer",
)


class GateError(RuntimeError):
    """A frozen MySQL prerequisite or gate invariant failed."""


def log(event: str, **fields: Any) -> None:
    payload = {"at_utc": utc_now(), "event": event, **fields}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def atomic_json(path: Path, payload: Any, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def run(
    argv: Sequence[str | Path],
    *,
    input_text: str | None = None,
    cwd: Path | None = None,
    timeout: float | None = None,
    capture: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [str(item) for item in argv]
    proc = subprocess.run(
        command,
        input=input_text,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture,
        timeout=timeout,
        check=False,
    )
    if check and proc.returncode:
        detail = ((proc.stderr or "") + "\n" + (proc.stdout or ""))[-4000:].strip()
        raise GateError(f"command failed rc={proc.returncode}, executable={command[0]!r}: {detail}")
    return proc


def _frozen() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    validate_shared_bundle()
    machine = load_json(SHARED_FROZEN_ROOT / "machine_and_resources.json")
    fixed = load_json(SHARED_FROZEN_ROOT / "fixed_db_settings.json")
    benchmark = load_json(SHARED_FROZEN_ROOT / "benchmark_matrix.json")
    return machine, fixed, benchmark


def _assert_no_disk3(path: Path, label: str) -> None:
    resolved = path.resolve(strict=False)
    forbidden = Path("/disk3")
    if resolved == forbidden or forbidden in resolved.parents:
        raise GateError(f"{label} resolves below forbidden /disk3: {resolved}")


def _require_regular(path: Path, label: str) -> None:
    _assert_no_disk3(path, label)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise GateError(f"missing {label}: {path}") from exc
    if path.is_symlink() or not stat.S_ISREG(mode):
        raise GateError(f"{label} is not a regular non-symlink file: {path}")


def _verify_artifacts(benchmark: Mapping[str, Any]) -> dict[str, Any]:
    artifacts = benchmark["artifacts"]
    paths = {
        "hybench_launcher": HYBENCH_ROOT / "hybench",
        "hybench_jar": HYBENCH_JAR,
        "hybench_mysql_statements": Path(artifacts["hybench_mysql_statements"]["path"]),
        "job_26a": Path(artifacts["job_26a"]["path"]),
        "benchbase_mysql_jar": BENCHBASE_JAR,
        "mysql_connector": MYSQL_CONNECTOR,
    }
    expected = {
        "hybench_launcher": artifacts["hybench_launcher_sha256"],
        "hybench_jar": artifacts["hybench_jar_sha256"],
        "hybench_mysql_statements": artifacts["hybench_mysql_statements"]["sha256"],
        "job_26a": artifacts["job_26a"]["sha256"],
        "benchbase_mysql_jar": artifacts["benchbase_mysql_jar_sha256"],
    }
    result: dict[str, Any] = {}
    for name, path in paths.items():
        _require_regular(path, name)
        actual = sha256_file(path)
        if name in expected and actual != expected[name]:
            raise GateError(f"frozen artifact SHA mismatch for {name}: {actual}")
        result[name] = {"path": str(path), "sha256": actual}
    java_version = run(["java", "-version"], check=True).stderr.splitlines()[0]
    if artifacts["java_version"] not in java_version:
        raise GateError(f"Java readback mismatch: {java_version}")
    result["java"] = {"readback": java_version, "expected": artifacts["java_version"]}
    return result


def _resolve_mount_device(mount_root: Path) -> str:
    proc = run(["findmnt", "-n", "-o", "MAJ:MIN", "-T", mount_root])
    value = proc.stdout.strip()
    if not value or ":" not in value:
        raise GateError(f"cannot resolve block device for {mount_root}")
    return value


def _docker_image_readback(machine: Mapping[str, Any]) -> dict[str, str]:
    reference = machine["images"]["mysql"]["reference"]
    expected = machine["images"]["mysql"]["digest"]
    raw = run(["docker", "image", "inspect", reference, "--format", "{{json .RepoDigests}}"])
    digests = json.loads(raw.stdout)
    matches = [item for item in digests if item.rsplit("@", 1)[-1] == expected]
    if not matches:
        raise GateError(f"MySQL image digest mismatch: expected={expected}, actual={digests}")
    image_id = run(["docker", "image", "inspect", reference, "--format", "{{.Id}}"])
    return {"reference": reference, "digest": expected, "repo_digest": matches[0], "image_id": image_id.stdout.strip()}


def _write_sysfs(path: Path, value: str) -> None:
    proc = run(["sudo", "-n", "tee", path], input_text=value + "\n", check=False)
    if proc.returncode:
        raise GateError(f"cannot apply frozen host setting {path}: {(proc.stderr or proc.stdout).strip()}")


def _selected_bracket_value(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    for token in text.split():
        if token.startswith("[") and token.endswith("]"):
            return token[1:-1]
    return text


def apply_and_read_machine(machine: Mapping[str, Any]) -> dict[str, Any]:
    policies = sorted(Path("/sys/devices/system/cpu/cpufreq").glob("policy*/scaling_governor"))
    if not policies:
        raise GateError("no CPU frequency policy governor files found")
    for path in policies:
        if path.read_text(encoding="utf-8").strip() != "performance":
            _write_sysfs(path, "performance")
    governor_readback = {str(path): path.read_text(encoding="utf-8").strip() for path in policies}
    if set(governor_readback.values()) != {"performance"}:
        raise GateError(f"CPU governor readback mismatch: {governor_readback}")

    thp_path = Path("/sys/kernel/mm/transparent_hugepage/enabled")
    if _selected_bracket_value(thp_path) != "madvise":
        _write_sysfs(thp_path, "madvise")
    thp = _selected_bracket_value(thp_path)
    if thp != "madvise":
        raise GateError(f"transparent hugepage readback mismatch: {thp}")

    boost_candidates = [
        Path("/sys/devices/system/cpu/cpufreq/boost"),
        Path("/sys/devices/system/cpu/cpu0/cpufreq/boost"),
    ]
    boost_path = next((path for path in boost_candidates if path.is_file()), None)
    if boost_path is None:
        raise GateError("CPU boost readback file is absent")
    boost = boost_path.read_text(encoding="utf-8").strip()
    if boost not in {"1", "enabled"}:
        _write_sysfs(boost_path, "1")
        boost = boost_path.read_text(encoding="utf-8").strip()
    if boost not in {"1", "enabled"}:
        raise GateError(f"CPU boost is not enabled: {boost}")

    swap_lines = Path("/proc/swaps").read_text(encoding="utf-8").splitlines()
    if len(swap_lines) != 1:
        raise GateError("host swap is enabled")
    return {
        "status": "passed",
        "governor": "performance",
        "governor_files": governor_readback,
        "transparent_hugepage": thp,
        "boost": "enabled",
        "boost_path": str(boost_path),
        "swap_enabled": False,
        "kernel": os.uname().release,
    }


def _credential() -> tuple[str, bool]:
    if MYSQL_CREDENTIAL.exists():
        mode = stat.S_IMODE(MYSQL_CREDENTIAL.stat().st_mode)
        if mode != 0o600 or MYSQL_CREDENTIAL.is_symlink():
            raise GateError(f"credential file must be regular mode 0600: {MYSQL_CREDENTIAL}")
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(MYSQL_CREDENTIAL, encoding="utf-8")
        if not parser.has_section("client") or parser.get("client", "user", fallback="") != "root":
            raise GateError("credential file must contain [client] user=root")
        password = parser.get("client", "password", fallback="")
        if not password:
            raise GateError("credential file has no password")
        return password.strip('"\''), False
    password = secrets.token_urlsafe(36)
    data = (
        "[client]\n"
        "user=root\n"
        f"password={password}\n"
        "host=127.0.0.1\n"
        "protocol=tcp\n"
    ).encode("utf-8")
    descriptor = os.open(MYSQL_CREDENTIAL, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return password, True


def _mysql(
    port: int,
    sql: str,
    *,
    database: str | None = None,
    local_infile: bool = False,
    empty_password: bool = False,
    timeout: float | None = None,
) -> str:
    argv: list[str | Path] = ["mysql"]
    if empty_password:
        argv.extend(["--no-defaults", "--user=root"])
    else:
        argv.extend([f"--defaults-extra-file={MYSQL_CREDENTIAL}"])
    argv.extend([
        "--protocol=tcp",
        "--host=127.0.0.1",
        f"--port={port}",
        "--batch",
        "--raw",
        "--skip-column-names",
    ])
    if local_infile:
        argv.append("--local-infile=1")
    if database:
        argv.append(database)
    proc = run(argv, input_text=sql, timeout=timeout)
    return proc.stdout


def _wait_mysql(port: int, *, empty_password: bool, timeout: int = 600) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc = run(
            [
                "mysql",
                "--no-defaults" if empty_password else f"--defaults-extra-file={MYSQL_CREDENTIAL}",
                "--protocol=tcp",
                "--host=127.0.0.1",
                f"--port={port}",
                "--user=root",
                "--batch",
                "--skip-column-names",
                "-e",
                "SELECT 1",
            ],
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip() == "1":
            return
        time.sleep(2)
    raise GateError(f"MySQL did not become ready on port {port}")


def _fixed_args(fixed: Mapping[str, Any], profile: str) -> list[str]:
    common = fixed["mysql"]["all_profiles"]
    selected = fixed["mysql"][profile]
    return [
        f"--innodb-flush-method={common['innodb_flush_method']}",
        f"--innodb-redo-log-capacity={common['innodb_redo_log_capacity']}",
        f"--innodb-flush-log-at-trx-commit={common['innodb_flush_log_at_trx_commit']}",
        f"--innodb-doublewrite={common['innodb_doublewrite']}",
        f"--innodb-adaptive-hash-index={common['innodb_adaptive_hash_index']}",
        "--skip-log-bin",
        f"--transaction-isolation={common['transaction_isolation']}",
        f"--performance-schema={common['performance_schema']}",
        f"--innodb-buffer-pool-size={selected['innodb_buffer_pool_size']}",
        f"--innodb-buffer-pool-instances={selected['innodb_buffer_pool_instances']}",
        f"--innodb-buffer-pool-chunk-size={selected['innodb_buffer_pool_chunk_size']}",
        f"--tmp-table-size={selected['tmp_table_size']}",
        f"--max-heap-table-size={selected['max_heap_table_size']}",
        f"--max-connections={selected['max_connections']}",
        "--local-infile=ON",
    ]


def _docker_run_args(
    machine: Mapping[str, Any],
    fixed: Mapping[str, Any],
    *,
    name: str,
    datadir: Path,
    port: int,
    profile: str,
    initialize_empty: bool,
) -> list[str | Path]:
    resource = machine["resource_profiles"][profile]
    image = machine["images"]["mysql"]["reference"]
    argv: list[str | Path] = [
        "docker", "run", "--detach", "--name", name,
        "--cpus", str(resource["cpu_quota_cores"]),
        "--cpuset-cpus", resource["cpuset_cpus"],
        "--cpuset-mems", resource["cpuset_mems"],
        "--memory", str(resource["memory_bytes"]),
        "--memory-swap", str(resource["memory_swap_bytes"]),
        "--pids-limit", str(resource["pids_limit"]),
        "--network", resource["network_mode"],
        "--publish", f"127.0.0.1:{port}:3306",
        "--volume", f"{datadir}:/var/lib/mysql",
    ]
    device = _resolve_mount_device(Path(machine["engines"]["mysql"]["mount_root"]))
    if resource["io_read_bps"] is not None:
        argv.extend(["--device-read-bps", f"/dev/block/{device}:{resource['io_read_bps']}"])
    if resource["io_write_bps"] is not None:
        argv.extend(["--device-write-bps", f"/dev/block/{device}:{resource['io_write_bps']}"])
    if initialize_empty:
        argv.extend(["--env", "MYSQL_ALLOW_EMPTY_PASSWORD=yes", "--env", "MYSQL_ROOT_HOST=%"])
    argv.extend([image, *_fixed_args(fixed, profile)])
    return argv


def _remove_own_container(name: str) -> None:
    exists = run(["docker", "inspect", name], check=False)
    if exists.returncode == 0:
        run(["docker", "rm", "--force", name], check=False)


def _set_initial_password(port: int, password: str) -> None:
    escaped = password.replace("'", "''")
    hosts = _mysql(port, "SELECT Host FROM mysql.user WHERE User='root' ORDER BY Host;", empty_password=True)
    host_values = [line.strip() for line in hosts.splitlines() if line.strip()]
    statements = [f"ALTER USER 'root'@'{host}' IDENTIFIED BY '{escaped}';" for host in host_values]
    if "%" not in host_values:
        statements.extend([
            f"CREATE USER 'root'@'%' IDENTIFIED BY '{escaped}';",
            "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;",
        ])
    statements.append("FLUSH PRIVILEGES;")
    _mysql(port, "\n".join(statements), empty_password=True)
    _wait_mysql(port, empty_password=False)


def _column_metadata(port: int, database: str, table: str) -> list[dict[str, str]]:
    sql = (
        "SELECT COLUMN_NAME,DATA_TYPE FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA='{database}' AND TABLE_NAME='{table}' ORDER BY ORDINAL_POSITION;"
    )
    values = []
    for line in _mysql(port, sql).splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 2:
            raise GateError(f"malformed column metadata for {database}.{table}: {line!r}")
        values.append({"name": fields[0], "data_type": fields[1].lower()})
    if not values:
        raise GateError(f"cannot discover columns for {database}.{table}")
    return values


def _csv_source_record_count(
    path: Path,
    column_names: Sequence[str],
    *,
    quoted: bool,
    csv_escape_model: str,
    load_transforms: Mapping[str, str],
    trailing_empty_source_fields: int,
) -> dict[str, Any]:
    """Count logical CSV records with the same enclosure/escape model as LOAD DATA."""
    _require_regular(path, "CSV source record audit")
    unknown = sorted(set(load_transforms) - set(column_names))
    if unknown:
        raise GateError(f"CSV load transforms refer to absent columns for {path}: {unknown}")
    supported = {"datetime_to_date"}
    unsupported = sorted(set(load_transforms.values()) - supported)
    if unsupported:
        raise GateError(f"unsupported CSV load transforms for {path}: {unsupported}")
    transform_indexes = {
        column_names.index(name): (name, transform)
        for name, transform in load_transforms.items()
    }
    validated_values = {name: 0 for name in load_transforms}
    if (
        isinstance(trailing_empty_source_fields, bool)
        or not isinstance(trailing_empty_source_fields, int)
        or trailing_empty_source_fields < 0
    ):
        raise GateError(f"invalid trailing empty source field count for {path}")
    expected_source_columns = len(column_names) + trailing_empty_source_fields
    trailing_empty_records_validated = 0
    previous_limit = csv.field_size_limit()
    csv.field_size_limit(sys.maxsize)
    count = 0
    if csv_escape_model not in {"mysql_backslash", "rfc4180_doublequote"}:
        raise GateError(f"unsupported CSV escape model for {path}: {csv_escape_model}")
    if not quoted and csv_escape_model != "mysql_backslash":
        raise GateError(f"unquoted CSV requires the mysql_backslash model: {path}")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(
                handle,
                delimiter=",",
                quotechar='"',
                escapechar="\\" if csv_escape_model == "mysql_backslash" else None,
                doublequote=True,
                strict=True,
                quoting=csv.QUOTE_MINIMAL if quoted else csv.QUOTE_NONE,
            )
            for record_number, row in enumerate(reader, 1):
                if len(row) != expected_source_columns:
                    raise GateError(
                        f"CSV logical record column mismatch for {path}: "
                        f"record={record_number} expected={expected_source_columns} actual={len(row)}"
                    )
                if trailing_empty_source_fields:
                    trailing = row[len(column_names):]
                    if any(value != "" for value in trailing):
                        raise GateError(
                            f"declared trailing empty CSV source field is nonempty: "
                            f"path={path} record={record_number} values={trailing!r}"
                        )
                    trailing_empty_records_validated += 1
                for index, (name, transform) in transform_indexes.items():
                    value = row[index]
                    if not value:
                        continue
                    if transform == "datetime_to_date":
                        try:
                            datetime.fromisoformat(value)
                        except ValueError as exc:
                            raise GateError(
                                f"invalid datetime source value for deterministic DATE projection: "
                                f"path={path} record={record_number} column={name} value={value!r}"
                            ) from exc
                    validated_values[name] += 1
                count += 1
    except (csv.Error, UnicodeError, OSError) as exc:
        raise GateError(f"cannot semantically parse CSV source {path}: {exc}") from exc
    finally:
        csv.field_size_limit(previous_limit)
    return {
        "source_path": str(path),
        "source_sha256": sha256_file(path),
        "source_bytes": path.stat().st_size,
        "source_records": count,
        "source_parser": (
            "python_csv_logical_records_mysql_backslash_escape"
            if csv_escape_model == "mysql_backslash"
            else "python_csv_logical_records_rfc4180_doublequote"
        ),
        "csv_escape_model": csv_escape_model,
        "quoted_fields": quoted,
        "expected_table_columns": len(column_names),
        "expected_source_columns": expected_source_columns,
        "trailing_empty_source_fields": trailing_empty_source_fields,
        "trailing_empty_records_validated": trailing_empty_records_validated,
        "load_column_transforms": dict(load_transforms),
        "transform_source_values_validated": validated_values,
    }


def _load_csv(
    port: int, database: str, table: str, path: Path, *, quoted: bool,
    csv_escape_model: str = "mysql_backslash",
    load_transforms: Mapping[str, str] | None = None,
    trailing_empty_source_fields: int = 0,
    audit_path: Path | None = None,
) -> dict[str, Any]:
    _require_regular(path, f"CSV for {database}.{table}")
    transforms = dict(load_transforms or {})
    metadata = _column_metadata(port, database, table)
    columns = [item["name"] for item in metadata]
    types = {item["name"]: item["data_type"] for item in metadata}
    for name, transform in transforms.items():
        if name not in types:
            raise GateError(f"load transform column is absent: {database}.{table}.{name}")
        if transform == "datetime_to_date" and types[name] != "date":
            raise GateError(
                f"datetime_to_date transform target is not DATE: "
                f"{database}.{table}.{name} type={types[name]}"
            )
    source = _csv_source_record_count(
        path, columns, quoted=quoted, csv_escape_model=csv_escape_model,
        load_transforms=transforms,
        trailing_empty_source_fields=trailing_empty_source_fields,
    )
    before = int(_mysql(port, f"SELECT COUNT(*) FROM `{table}`;", database=database).strip())
    if before != 0:
        raise GateError(f"target table is nonempty before load: {database}.{table} rows={before}")
    variables = [f"@v{index}" for index in range(1, len(columns) + 1)]
    variables.extend(
        f"@formal_trailing_empty_{index}"
        for index in range(1, trailing_empty_source_fields + 1)
    )
    assignments_list = []
    for index, name in enumerate(columns, 1):
        variable = f"@v{index}"
        transform = transforms.get(name)
        if transform == "datetime_to_date":
            expression = (
                f"CASE WHEN {variable}='' THEN NULL "
                f"ELSE STR_TO_DATE(LEFT({variable},10),'%Y-%m-%d') END"
            )
        elif transform is None:
            expression = f"NULLIF({variable},'')"
        else:  # guarded above and in the source parser
            raise GateError(f"unsupported load transform: {transform}")
        assignments_list.append(f"`{name}`={expression}")
    assignments = ",".join(assignments_list)
    escaped_path = str(path).replace("\\", "\\\\").replace("'", "\\'")
    if quoted and csv_escape_model == "mysql_backslash":
        enclosure = " OPTIONALLY ENCLOSED BY '\"' ESCAPED BY '\\\\'"
    elif quoted and csv_escape_model == "rfc4180_doublequote":
        enclosure = " OPTIONALLY ENCLOSED BY '\"' ESCAPED BY '\"'"
    elif not quoted and csv_escape_model == "mysql_backslash":
        enclosure = ""
    else:
        raise GateError(
            f"unsupported LOAD DATA quoting/escape combination: "
            f"quoted={quoted} model={csv_escape_model}"
        )
    diagnostic_token = secrets.token_hex(8)
    marker = f"FORMAL_LOAD_AUDIT_{diagnostic_token}"
    loaded_rows_variable = f"@formal_loaded_rows_{diagnostic_token}"
    warning_count_variable = f"@formal_warning_count_{diagnostic_token}"
    sql = (
        f"LOAD DATA LOCAL INFILE '{escaped_path}' INTO TABLE `{table}` "
        f"FIELDS TERMINATED BY ','{enclosure} LINES TERMINATED BY '\\n' "
        f"({','.join(variables)}) SET {assignments};\n"
        f"GET DIAGNOSTICS {warning_count_variable}=NUMBER,"
        f"{loaded_rows_variable}=ROW_COUNT;\n"
        "SHOW WARNINGS;\n"
        f"SELECT '{marker}',{loaded_rows_variable},{warning_count_variable};"
    )
    log(
        "csv_load_started", database=database, table=table,
        bytes=path.stat().st_size, source_records=source["source_records"],
    )
    output = _mysql(
        port, sql, database=database, local_infile=True,
        timeout=None,
    )
    rows = [line.split("\t") for line in output.splitlines() if line]
    audit_rows = [row for row in rows if row and row[0] == marker]
    if len(audit_rows) != 1 or len(audit_rows[0]) != 3:
        raise GateError(f"LOAD DATA audit marker missing or malformed for {database}.{table}")
    loaded_rows = int(audit_rows[0][1])
    warning_count = int(audit_rows[0][2])
    warning_details = [row for row in rows if not (row and row[0] == marker)]
    final_rows = int(_mysql(port, f"SELECT COUNT(*) FROM `{table}`;", database=database).strip())
    audit = {
        **source,
        "database": database,
        "table": table,
        "loaded_rows": loaded_rows,
        "final_count_rows": final_rows,
        "warnings": warning_count,
        "warning_count": warning_count,
        "warning_details": warning_details,
        "warning_capture": "mysql_get_diagnostics_then_show_warnings",
        "load_mechanism": "mysql_load_data_local_infile",
        "column_data_types": types,
        "closure_passed": (
            source["source_records"] == loaded_rows == final_rows and warning_count == 0
        ),
    }
    if audit_path is not None:
        atomic_json(audit_path, audit)
    if not audit["closure_passed"]:
        raise GateError(
            f"LOAD DATA count/warning closure failed for {database}.{table}: "
            f"source_records={source['source_records']} loaded_rows={loaded_rows} "
            f"final_count={final_rows} warnings={warning_count}"
        )
    log(
        "csv_load_completed", database=database, table=table,
        source_records=source["source_records"], loaded_rows=loaded_rows,
        final_count_rows=final_rows, warnings=warning_count,
    )
    return audit


def _sql_file(port: int, database: str, path: Path) -> None:
    _require_regular(path, f"SQL file for {database}")
    _mysql(port, path.read_text(encoding="utf-8"), database=database, timeout=None)


def _normalize_hybench_table_identifier_case(text: str) -> str:
    """Resolve the upstream SQL's identifier-case inconsistency without editing it."""
    normalized = text
    for table in HYBENCH_CANONICAL_TABLE_NAMES:
        normalized = re.sub(
            rf"\b{re.escape(table)}\b", table, normalized, flags=re.IGNORECASE,
        )
    return normalized


def _build_hybench(port: int, campaign_attempt: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    database = "hybench_sf10x"
    _mysql(port, f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
    _sql_file(port, database, HYBENCH_ROOT / "conf/ddl_mysql.sql")
    tables = [
        ("customer", "customer.csv", {"created_date": "datetime_to_date"}, 0),
        ("company", "company.csv", {"created_date": "datetime_to_date"}, 0),
        ("transfer", "transfer.csv", {}, 0),
        ("checking", "checking.csv", {}, 0),
        ("checkingAccount", "checkingAccount.csv", {}, 0),
        ("savingAccount", "savingAccount.csv", {}, 1),
        ("loanapps", "loanApps.csv", {}, 0),
        ("loantrans", "loanTrans.csv", {}, 0),
    ]
    audit = {}
    for table, filename, transforms, trailing_empty_fields in tables:
        audit[table] = _load_csv(
            port, database, table, HYBENCH_DATA / filename, quoted=False,
            load_transforms=transforms,
            trailing_empty_source_fields=trailing_empty_fields,
            audit_path=campaign_attempt / "load_audit_tables" / f"{table}.json",
        )
    index_source = HYBENCH_ROOT / "conf/create_index_A2.sql"
    index_source_text = index_source.read_text(encoding="utf-8")
    normalized_index_text = _normalize_hybench_table_identifier_case(index_source_text)
    adaptation = {
        "type": "hybench_table_identifier_case_normalization",
        "reason": "upstream MySQL DDL and SQL mix identifier case on a case-sensitive Linux server",
        "canonical_table_names": list(HYBENCH_CANONICAL_TABLE_NAMES),
        "source_path": str(index_source),
        "source_sha256": sha256_file(index_source),
        "normalized_sha256": hashlib.sha256(normalized_index_text.encode("utf-8")).hexdigest(),
        "source_file_modified": False,
        "semantic_change": False,
    }
    atomic_json(campaign_attempt / "hybench_identifier_case_adaptation.json", adaptation)
    _mysql(port, normalized_index_text, database=database, timeout=None)
    return audit, adaptation


def _canonicalize_postgres_csv(
    source: Path, target: Path, expected_columns: int,
) -> dict[str, Any]:
    """Canonicalize PostgreSQL CSV without treating backslash-comma as escaped.

    The source uses PostgreSQL CSV escape semantics: inside a quoted field a
    backslash only escapes quote or backslash.  Outside quotes it is always
    literal, so a following comma remains a delimiter.  The output is strict
    RFC4180-style CSV with doubled quotes and is safe to load with MySQL
    ``ESCAPED BY '"'``.
    """
    _require_regular(source, "PostgreSQL CSV canonicalization source")
    if isinstance(expected_columns, bool) or not isinstance(expected_columns, int) or expected_columns <= 0:
        raise GateError(f"invalid canonical CSV expected column count: {expected_columns}")
    if source.resolve() == target.resolve(strict=False):
        raise GateError("canonical CSV target must not overwrite the formal source")
    if os.path.lexists(target):
        raise GateError(f"canonical CSV target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
    counters = {
        "literal_backslash_before_delimiter_count": 0,
        "records_with_literal_backslash_before_delimiter": 0,
        "backslash_quote_escape_count": 0,
        "backslash_escape_escape_count": 0,
        "doublequote_escape_count": 0,
        "embedded_newline_record_count": 0,
    }
    row: list[str] = []
    field: list[str] = []
    in_quotes = False
    quote_pending = False
    backslash_pending = False
    after_quoted_field = False
    at_field_start = True
    pending_cr = False
    record_open = False
    record_has_embedded_newline = False
    record_has_backslash_delimiter = False
    source_records = 0
    physical_line = 1

    try:
        with source.open("r", encoding="utf-8", newline="") as source_handle, temporary.open(
            "x", encoding="utf-8", newline=""
        ) as target_handle:
            writer = csv.writer(
                target_handle,
                delimiter=",",
                quotechar='"',
                escapechar=None,
                doublequote=True,
                lineterminator="\n",
                quoting=csv.QUOTE_MINIMAL,
            )

            def finish_record() -> None:
                nonlocal row, field, at_field_start, record_open
                nonlocal record_has_embedded_newline, record_has_backslash_delimiter
                nonlocal source_records
                row.append("".join(field))
                if len(row) != expected_columns:
                    raise GateError(
                        f"PostgreSQL CSV logical record column mismatch for {source}: "
                        f"record={source_records + 1} physical_line={physical_line} "
                        f"expected={expected_columns} actual={len(row)}"
                    )
                writer.writerow(row)
                source_records += 1
                if record_has_embedded_newline:
                    counters["embedded_newline_record_count"] += 1
                if record_has_backslash_delimiter:
                    counters["records_with_literal_backslash_before_delimiter"] += 1
                row = []
                field = []
                at_field_start = True
                record_open = False
                record_has_embedded_newline = False
                record_has_backslash_delimiter = False

            for chunk in iter(lambda: source_handle.read(1024 * 1024), ""):
                for character in chunk:
                    reprocess = True
                    while reprocess:
                        reprocess = False
                        if pending_cr:
                            if character != "\n":
                                raise GateError(
                                    f"bare CR outside quoted field in {source} at physical line {physical_line}"
                                )
                            pending_cr = False
                            finish_record()
                            physical_line += 1
                            continue
                        if backslash_pending:
                            backslash_pending = False
                            if character == '"':
                                field.append('"')
                                counters["backslash_quote_escape_count"] += 1
                                at_field_start = False
                                record_open = True
                                continue
                            if character == "\\":
                                field.append("\\")
                                counters["backslash_escape_escape_count"] += 1
                                at_field_start = False
                                record_open = True
                                continue
                            field.append("\\")
                            at_field_start = False
                            record_open = True
                            reprocess = True
                            continue
                        if quote_pending:
                            quote_pending = False
                            if character == '"':
                                field.append('"')
                                counters["doublequote_escape_count"] += 1
                                at_field_start = False
                                record_open = True
                                continue
                            in_quotes = False
                            after_quoted_field = True
                            reprocess = True
                            continue
                        if in_quotes:
                            if character == "\\":
                                backslash_pending = True
                            elif character == '"':
                                quote_pending = True
                            else:
                                field.append(character)
                                at_field_start = False
                                record_open = True
                                if character == "\n":
                                    physical_line += 1
                                    record_has_embedded_newline = True
                            continue
                        if after_quoted_field:
                            if character not in {",", "\n", "\r"}:
                                raise GateError(
                                    f"unexpected character after quoted field in {source}: "
                                    f"physical_line={physical_line} character={character!r}"
                                )
                            after_quoted_field = False
                        if character == ",":
                            if field and field[-1] == "\\":
                                counters["literal_backslash_before_delimiter_count"] += 1
                                record_has_backslash_delimiter = True
                            row.append("".join(field))
                            field = []
                            at_field_start = True
                            record_open = True
                        elif character == "\n":
                            finish_record()
                            physical_line += 1
                        elif character == "\r":
                            pending_cr = True
                            record_open = True
                        elif character == '"':
                            if not at_field_start:
                                raise GateError(
                                    f"quote in unquoted PostgreSQL CSV field: "
                                    f"source={source} physical_line={physical_line}"
                                )
                            in_quotes = True
                            at_field_start = False
                            record_open = True
                        else:
                            field.append(character)
                            at_field_start = False
                            record_open = True

            if pending_cr:
                raise GateError(f"PostgreSQL CSV ends with a bare CR: {source}")
            if backslash_pending:
                field.append("\\")
                backslash_pending = False
            if quote_pending:
                quote_pending = False
                in_quotes = False
                after_quoted_field = True
            if in_quotes:
                raise GateError(f"unterminated quoted field in PostgreSQL CSV: {source}")
            if record_open or row or field or after_quoted_field:
                finish_record()
            target_handle.flush()
            os.fsync(target_handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    except (OSError, UnicodeError, csv.Error) as exc:
        temporary.unlink(missing_ok=True)
        raise GateError(f"PostgreSQL CSV canonicalization failed for {source}: {exc}") from exc
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    result = {
        "source_path": str(source),
        "source_sha256": sha256_file(source),
        "source_bytes": source.stat().st_size,
        "source_records": source_records,
        "source_parser": "postgresql_csv_selective_backslash_escape_state_machine",
        "source_escape_semantics": "backslash_escapes_quote_or_escape_only_not_delimiter",
        "canonical_path": str(target),
        "canonical_sha256": sha256_file(target),
        "canonical_bytes": target.stat().st_size,
        "canonical_records": source_records,
        "canonical_format": "rfc4180_doublequote_lf",
        "expected_columns": expected_columns,
        "adaptation_counts": counters,
        "arbitrary_missing_column_padding": False,
        "closure_passed": True,
    }
    return result


def _build_job(
    port: int, campaign_attempt: Path, physical_attempt: Path,
) -> dict[str, Any]:
    database = "imdb_job"
    _mysql(port, f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;")
    _sql_file(port, database, JOB_SQL_ROOT / "schema.sql")
    table_names = [path.stem for path in JOB_DATA.glob("*.csv")]
    table_names.sort()
    canonical_root = physical_attempt / "canonical_sources"
    canonical_root.mkdir(mode=0o700)
    audit = {}
    for table in table_names:
        source = JOB_DATA / f"{table}.csv"
        canonical = canonical_root / f"{table}.canonical.csv"
        expected_columns = len(_column_metadata(port, database, table))
        canonicalization = _canonicalize_postgres_csv(
            source, canonical, expected_columns,
        )
        canonicalization_path = campaign_attempt / "canonicalization_tables" / f"{table}.json"
        atomic_json(canonicalization_path, canonicalization)
        load = _load_csv(
            port, database, table, canonical, quoted=True,
            csv_escape_model="rfc4180_doublequote",
            audit_path=campaign_attempt / "load_audit_tables" / f"{table}.json",
        )
        if canonicalization["source_records"] != load["source_records"]:
            raise GateError(
                f"JOB source/canonical record closure failed for {table}: "
                f"source={canonicalization['source_records']} canonical={load['source_records']}"
            )
        canonical_load_source_audit = {
            key: load[key]
            for key in (
                "source_path", "source_sha256", "source_bytes", "source_records",
                "source_parser", "csv_escape_model", "quoted_fields",
            )
        }
        canonical.unlink()
        canonicalization["canonical_removed_after_verified_load"] = True
        atomic_json(canonicalization_path, canonicalization)
        final_audit = dict(load)
        final_audit.update({
            "source_path": canonicalization["source_path"],
            "source_sha256": canonicalization["source_sha256"],
            "source_bytes": canonicalization["source_bytes"],
            "source_records": canonicalization["source_records"],
            "source_parser": canonicalization["source_parser"],
            "source_escape_semantics": canonicalization["source_escape_semantics"],
            "canonical_path": canonicalization["canonical_path"],
            "canonical_sha256": canonicalization["canonical_sha256"],
            "canonical_bytes": canonicalization["canonical_bytes"],
            "canonical_records": canonicalization["canonical_records"],
            "canonical_format": canonicalization["canonical_format"],
            "canonicalization_adaptation_counts": canonicalization["adaptation_counts"],
            "canonical_removed_after_verified_load": True,
            "arbitrary_missing_column_padding": False,
            "canonical_load_source_audit": canonical_load_source_audit,
            "closure_passed": (
                load["closure_passed"] is True
                and canonicalization["source_records"]
                == canonicalization["canonical_records"]
                == load["loaded_rows"]
                == load["final_count_rows"]
                and load["warnings"] == 0
            ),
        })
        if final_audit["closure_passed"] is not True:
            raise GateError(f"JOB canonical/load closure failed for {table}")
        audit[table] = final_audit
        atomic_json(
            campaign_attempt / "load_audit_tables" / f"{table}.json",
            final_audit,
        )
    canonical_root.rmdir()
    _sql_file(port, database, JOB_SQL_ROOT / "fkindexes.sql")
    return audit


def _benchbase_config(port: int, password: str, target: Path) -> None:
    source = BENCHBASE_ROOT / "config/mysql/sample_tpch_config.xml"
    _require_regular(source, "BenchBase TPC-H configuration")
    tree = ET.parse(source)
    root = tree.getroot()
    values = {
        "url": (
            f"jdbc:mysql://127.0.0.1:{port}/tpch_sf10?"
            "rewriteBatchedStatements=true&allowLoadLocalInfile=true&allowPublicKeyRetrieval=true&"
            "sslMode=DISABLED&useServerPrepStmts=true&cachePrepStmts=true&"
            "useCursorFetch=true&defaultFetchSize=1000"
        ),
        "username": "root",
        "password": password,
        "isolation": "TRANSACTION_READ_COMMITTED",
        "scalefactor": "10",
        "terminals": "2",
        "loaderThreads": "8",
        "randomSeed": "4508333",
    }
    for tag, value in values.items():
        node = root.find(tag)
        if node is None:
            node = ET.Element(tag)
            root.insert(0, node)
        node.text = value
    works = root.find("works")
    if works is None:
        raise GateError("BenchBase configuration lacks works")
    work_nodes = works.findall("work")
    for extra in work_nodes[1:]:
        works.remove(extra)
    work = work_nodes[0]
    work.find("serial").text = "false"  # type: ignore[union-attr]
    work.find("rate").text = "unlimited"  # type: ignore[union-attr]
    work.find("weights").text = ",".join("100" if index == 18 else "0" for index in range(1, 23))  # type: ignore[union-attr]
    # BenchBase validates a positive time bound for every non-serial work
    # definition even when invoked with --execute=false for create/load only.
    # This value is therefore a parser precondition, not a measurement
    # duration; formal measurements generate their own frozen-duration config.
    time_node = work.find("time")
    if time_node is None:
        time_node = ET.SubElement(work, "time")
    time_node.text = "1"
    tree.write(target, encoding="utf-8", xml_declaration=True)
    os.chmod(target, 0o600)


def _tpch_generated_source_counts() -> dict[str, int]:
    """Derive SF10 cardinalities from the frozen BenchBase generator implementation."""
    for path, label in (
        (TPCH_COUNT_AUDIT_SOURCE, "TPC-H count-audit source"),
        (TPCH_COUNT_AUDIT_CLASS, "TPC-H count-audit class"),
    ):
        _require_regular(path, label)
    proc = run(
        [
            "java", "-cp", os.pathsep.join((str(TPCH_COUNT_AUDIT_CLASSES), str(BENCHBASE_JAR))),
            "com.oltpbenchmark.benchmarks.tpch.util.TpchSfCountAudit", "10",
        ],
        timeout=600,
    )
    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        pieces = line.split("\t")
        if len(pieces) != 2 or not pieces[1].isdigit():
            raise GateError(f"malformed TPC-H generator count-audit output: {line!r}")
        if pieces[0] in counts:
            raise GateError(f"duplicate TPC-H generator count-audit table: {pieces[0]}")
        counts[pieces[0]] = int(pieces[1])
    expected_tables = {
        "region", "nation", "supplier", "customer", "part", "partsupp", "orders", "lineitem",
    }
    if set(counts) != expected_tables or any(value <= 0 for value in counts.values()):
        raise GateError(f"TPC-H generator count-audit table/count mismatch: {counts}")
    return counts


def _benchbase_warning_lines(stdout: str, stderr: str) -> list[str]:
    pattern = re.compile(r"(?i)(?:\bWARN(?:ING)?\b|\bERROR\b|Exception|SQLState)")
    return [line for line in (stdout + "\n" + stderr).splitlines() if pattern.search(line)]


def _build_tpch(port: int, password: str, campaign_attempt: Path) -> dict[str, Any]:
    source_counts = _tpch_generated_source_counts()
    atomic_json(campaign_attempt / "tpch_generated_source_counts.json", {
        "schema_version": 1,
        "freeze_id": FREEZE_ID,
        "scale_factor": 10,
        "generator": "frozen_benchbase_tpch_generator",
        "benchbase_jar": str(BENCHBASE_JAR),
        "benchbase_jar_sha256": sha256_file(BENCHBASE_JAR),
        "count_audit_source": str(TPCH_COUNT_AUDIT_SOURCE),
        "count_audit_source_sha256": sha256_file(TPCH_COUNT_AUDIT_SOURCE),
        "counts": source_counts,
    })
    _mysql(port, "CREATE DATABASE `tpch_sf10` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;")
    private = campaign_attempt / "private"
    private.mkdir(mode=0o700)
    config = private / "tpch.xml"
    _benchbase_config(port, password, config)
    output = campaign_attempt / "benchbase_prepare"
    output.mkdir()
    log("benchbase_tpch_load_started", scale_factor=10, loader_threads=8)
    proc = run(
        [
            "java", "-jar", BENCHBASE_JAR,
            "-b", "tpch", "-c", config,
            "--create=true", "--load=true", "--execute=false", "-d", output,
        ],
        cwd=BENCHBASE_ROOT,
        timeout=None,
        check=False,
    )
    (campaign_attempt / "benchbase_prepare.stdout.log").write_text(proc.stdout, encoding="utf-8")
    (campaign_attempt / "benchbase_prepare.stderr.log").write_text(proc.stderr, encoding="utf-8")
    config.unlink()
    private.rmdir()
    if proc.returncode != 0:
        raise GateError(f"BenchBase TPC-H prepare failed with rc={proc.returncode}")
    warning_lines = _benchbase_warning_lines(proc.stdout, proc.stderr)
    digest_rows = _tabular(
        port,
        "SELECT COALESCE(DIGEST_TEXT,''),SUM_WARNINGS,SUM_ERRORS "
        "FROM performance_schema.events_statements_summary_by_digest "
        "WHERE SCHEMA_NAME='tpch_sf10' AND DIGEST_TEXT LIKE 'INSERT%';",
    )
    digest_evidence = [
        {"digest_text": row[0], "sum_warnings": int(row[1]), "sum_errors": int(row[2])}
        for row in digest_rows
    ]
    schema_warning_total = sum(item["sum_warnings"] for item in digest_evidence)
    schema_error_total = sum(item["sum_errors"] for item in digest_evidence)
    loaded_tables = _tables(port, "tpch_sf10")
    if set(loaded_tables) != set(source_counts):
        raise GateError(
            f"TPC-H loaded table set mismatch: expected={sorted(source_counts)} actual={loaded_tables}"
        )
    audit: dict[str, Any] = {}
    attributed_warning_total = 0
    attributed_error_total = 0
    for table in loaded_tables:
        loaded_rows = int(_mysql(port, f"SELECT COUNT(*) FROM `{table}`;", database="tpch_sf10").strip())
        matching_digests = [
            item for item in digest_evidence
            if re.search(rf"(?i)(?:^|[^A-Za-z0-9_])`?{re.escape(table)}`?(?:[^A-Za-z0-9_]|$)", item["digest_text"])
        ]
        table_server_warnings = sum(item["sum_warnings"] for item in matching_digests)
        table_server_errors = sum(item["sum_errors"] for item in matching_digests)
        attributed_warning_total += table_server_warnings
        attributed_error_total += table_server_errors
        warning_count = len(warning_lines) + table_server_warnings
        entry = {
            "database": "tpch_sf10",
            "table": table,
            "source_records": source_counts[table],
            "source_parser": "frozen_benchbase_generator_cardinality_audit",
            "scale_factor": 10,
            "loaded_rows": loaded_rows,
            "final_count_rows": loaded_rows,
            "warnings": warning_count,
            "warning_count": warning_count,
            "warning_log_source": "benchbase_prepare_stdout_and_stderr",
            "server_statement_digest_warnings": table_server_warnings,
            "server_statement_digest_errors": table_server_errors,
            "server_statement_digest_evidence": matching_digests,
            "schema_insert_warning_total": schema_warning_total,
            "schema_insert_error_total": schema_error_total,
            # The bounded performance_schema digest table may evict individual
            # loader INSERT digests during an SF10 parallel load.  Digest
            # presence is useful diagnostics, but absence is not evidence of a
            # warning when BenchBase emitted none, the schema digest totals are
            # zero, and generator/loaded/final counts close exactly.
            "statement_digest_evidence_available": bool(matching_digests),
            "warning_observation_passed": (
                not warning_lines
                and schema_warning_total == 0
                and schema_error_total == 0
            ),
            "closure_passed": (
                source_counts[table] == loaded_rows and warning_count == 0
                and schema_warning_total == 0 and schema_error_total == 0
            ),
        }
        audit[table] = entry
        atomic_json(campaign_attempt / "load_audit_tables" / f"{table}.json", entry)
        if not entry["closure_passed"]:
            raise GateError(
                f"TPC-H generated/load count or warning closure failed for {table}: "
                f"source_records={source_counts[table]} loaded_rows={loaded_rows} "
                f"warnings={warning_count} schema_warnings={schema_warning_total} "
                f"schema_errors={schema_error_total}"
            )
    if attributed_warning_total != schema_warning_total or attributed_error_total != schema_error_total:
        raise GateError(
            "TPC-H server statement warning/error evidence could not be attributed exactly to tables: "
            f"schema_warnings={schema_warning_total} attributed_warnings={attributed_warning_total} "
            f"schema_errors={schema_error_total} attributed_errors={attributed_error_total}"
        )
    log(
        "benchbase_tpch_load_completed", scale_factor=10,
        tables=len(audit), warnings=0, row_count_closure=True,
    )
    return audit


def _tables(port: int, database: str) -> list[str]:
    sql = (
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA='{database}' AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME;"
    )
    return [line.strip() for line in _mysql(port, sql).splitlines() if line.strip()]


def _analyze(port: int, database: str) -> None:
    tables = _tables(port, database)
    if not tables:
        raise GateError(f"no tables found in {database}")
    _mysql(port, "\n".join(f"ANALYZE TABLE `{table}`;" for table in tables), database=database, timeout=None)


def _tabular(port: int, sql: str, *, database: str | None = None) -> list[list[str]]:
    return [line.split("\t") for line in _mysql(port, sql, database=database).splitlines() if line]


def _template_manifest(
    port: int,
    workload: str,
    database: str,
    artifact_readback: Mapping[str, Any],
    image_readback: Mapping[str, Any],
    source_files: Iterable[Path],
    load_audit: Mapping[str, Any],
    runtime_adaptations: Mapping[str, Any],
) -> dict[str, Any]:
    tables = _tables(port, database)
    row_counts: dict[str, int] = {}
    for table in tables:
        value = _mysql(port, f"SELECT COUNT(*) FROM `{table}`;", database=database).strip()
        row_counts[table] = int(value)
    if set(load_audit) != set(row_counts):
        raise GateError(
            f"load-audit table set does not match final database: "
            f"audit={sorted(load_audit)} final={sorted(row_counts)}"
        )
    for table, final_count in row_counts.items():
        item = load_audit[table]
        if (
            not isinstance(item, Mapping)
            or item.get("closure_passed") is not True
            or item.get("source_records") != final_count
            or item.get("loaded_rows") != final_count
            or item.get("warnings") != 0
        ):
            raise GateError(f"final manifest row-count closure failed for {database}.{table}: {item}")
    columns = _tabular(
        port,
        "SELECT TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA "
        "FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA='{database}' ORDER BY TABLE_NAME,ORDINAL_POSITION;",
    )
    indexes = _tabular(
        port,
        "SELECT TABLE_NAME,INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME,INDEX_TYPE "
        "FROM information_schema.STATISTICS "
        f"WHERE TABLE_SCHEMA='{database}' ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX;",
    )
    stats = _tabular(
        port,
        "SELECT database_name,table_name,last_update,n_rows,clustered_index_size,sum_of_other_index_sizes "
        "FROM mysql.innodb_table_stats "
        f"WHERE database_name='{database}' ORDER BY table_name;",
    )
    sources = []
    for path in source_files:
        _require_regular(path, f"template source {path.name}")
        entry: dict[str, Any] = {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        sources.append(entry)
    version = _mysql(port, "SELECT VERSION();").strip()
    canonical_columns = json.dumps(columns, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    canonical_indexes = json.dumps(indexes, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    identity = {
        "hybench": ("mysql80_hybench10x_v1", "hybench-10x-v1", "10x"),
        "job": ("mysql80_job_full_v1", "imdb-job-full-v1", 1),
        "tpch": ("mysql80_tpch_sf10_v1", "tpch-sf10-v1", 10),
    }[workload]
    return {
        "schema_version": 1,
        "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        "engine": ENGINE,
        "template_id": identity[0],
        "database_engine": "mysql",
        "database_version": version,
        "dataset_version": identity[1],
        "scale_factor": identity[2],
        "generator_seed": 4508333 if workload == "tpch" else None,
        "workload": workload,
        "database": database,
        "status": "complete",
        "built_at_utc": utc_now(),
        "mysql_version": version,
        "database_size_bytes": None,
        "image": dict(image_readback),
        "row_counts": row_counts,
        "row_count_by_table": row_counts,
        "load_audit": dict(load_audit),
        "runtime_adaptations": dict(runtime_adaptations),
        "all_source_loaded_final_counts_equal": True,
        "all_load_warning_counts_zero": True,
        "columns": columns,
        "schema_sha256": hashlib.sha256(canonical_columns).hexdigest(),
        "indexes": indexes,
        "index_sha256": hashlib.sha256(canonical_indexes).hexdigest(),
        "innodb_table_stats": stats,
        "statistics_created_at_utc": utc_now(),
        "artifacts": dict(artifact_readback),
        "source_files": sources,
        "source_file_sha256": {entry["path"]: entry["sha256"] for entry in sources},
        "template_snapshot_sha256": None,
        "template_snapshot_sha256_algorithm": "sha256_tree_v1",
        "statistics_frozen_after_analyze": True,
    }


def _make_read_only(root: Path) -> None:
    for current, directories, files in os.walk(root):
        current_path = Path(current)
        for name in files:
            path = current_path / name
            if not path.is_symlink():
                os.chmod(path, 0o444)
        for name in directories:
            path = current_path / name
            if not path.is_symlink():
                os.chmod(path, 0o555)
    os.chmod(root, 0o555)


def _tree_size_and_sha256(root: Path) -> tuple[int, str]:
    """Hash a stopped datadir using sorted relative paths and file bytes."""
    digest = hashlib.sha256()
    total = 0
    entries = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    for path in entries:
        if path.is_symlink():
            relative = path.relative_to(root).as_posix().encode("utf-8")
            target = os.readlink(path).encode("utf-8")
            digest.update(b"L\0" + relative + b"\0" + target + b"\0")
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_dir():
            digest.update(b"D\0" + relative + b"\0")
            continue
        if not path.is_file():
            raise GateError(f"template datadir contains a non-regular entry: {path}")
        size = path.stat().st_size
        total += size
        digest.update(b"F\0" + relative + b"\0" + str(size).encode("ascii") + b"\0")
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(block)
    return total, digest.hexdigest()


def _adopt_existing_physical_template(
    workload: str,
    final: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Revalidate immutable v2 bytes for v3 without reusing performance evidence."""

    source = json.loads(manifest_path.read_text(encoding="utf-8"))
    if source.get("freeze_id") == FREEZE_ID:
        if source.get("global_campaign_id") != GLOBAL_CAMPAIGN_ID:
            raise GateError(f"existing v3 template campaign mismatch: {manifest_path}")
        return source
    for field, expected in {
        "schema_version": 1,
        "freeze_id": "frozen_formal_config_20260721_v2",
        "global_campaign_id": "six_session_20260721_v2",
        "engine": ENGINE,
        "workload": workload,
        "status": "complete",
    }.items():
        if source.get(field) != expected:
            raise GateError(f"existing physical template {field} mismatch: {manifest_path}")
    # A MySQL datadir may contain mysql.sock as a symlink to the runtime
    # socket directory.  Symlink modes are reported as 0777 on Linux and are
    # not permission bits that make the template target writable.  The tree
    # digest below hashes both the link path and target, so keep validating
    # the link bytes while applying the no-write-bits rule to non-links only.
    writable = run(
        [
            "find", str(final), "-xdev", "!", "-type", "l",
            "-perm", "/222", "-print", "-quit",
        ]
    ).stdout.strip()
    if writable:
        raise GateError(f"existing physical template contains a writable path: {writable}")
    observed_size, observed_sha = _tree_size_and_sha256(final / "datadir")
    if observed_size != source.get("database_size_bytes"):
        raise GateError(
            f"existing physical template size mismatch for {workload}: "
            f"observed={observed_size} expected={source.get('database_size_bytes')}"
        )
    if observed_sha != source.get("template_snapshot_sha256"):
        raise GateError(f"existing physical template snapshot SHA mismatch for {workload}")
    adoption = {
        "schema_version": 1,
        "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        "engine": ENGINE,
        "workload": workload,
        "status": "passed",
        "adopted_at_utc": utc_now(),
        "reason": "v3_only_adds_recost_estimator_contract_database_bytes_unchanged",
        "physical_template_root": str(final),
        "source_manifest_path": str(manifest_path),
        "source_manifest_sha256": sha256_file(manifest_path),
        "source_freeze_id": source["freeze_id"],
        "recursive_no_write_bits": True,
        "database_size_bytes_revalidated": observed_size,
        "template_snapshot_sha256_revalidated": observed_sha,
        "historical_plan_or_performance_results_reused": False,
        "current_v3_p0_g0_g10_gate_still_required": True,
    }
    adoption_path = PREFLIGHT_ROOT / "template_adoptions" / f"{workload}.json"
    if adoption_path.exists() or adoption_path.is_symlink():
        existing = json.loads(adoption_path.read_text(encoding="utf-8"))
        comparable = dict(existing)
        comparable.pop("adopted_at_utc", None)
        expected = dict(adoption)
        expected.pop("adopted_at_utc", None)
        if comparable != expected:
            raise GateError(f"existing v3 template adoption differs: {adoption_path}")
        adoption = existing
    else:
        atomic_json(adoption_path, adoption)
    adapted = dict(source)
    adapted["freeze_id"] = FREEZE_ID
    adapted["global_campaign_id"] = GLOBAL_CAMPAIGN_ID
    adapted["physical_template_adoption"] = adoption
    return adapted


def _build_one(
    workload: str,
    database: str,
    port: int,
    machine: Mapping[str, Any],
    fixed: Mapping[str, Any],
    artifact_readback: Mapping[str, Any],
    image_readback: Mapping[str, Any],
    password: str,
) -> dict[str, Any]:
    template_root = Path(machine["engines"]["mysql"]["template_root"])
    mount_root = Path(machine["engines"]["mysql"]["mount_root"])
    template_root.mkdir(parents=True, exist_ok=True)
    physical_attempt_root = template_root / ".build_attempts"
    physical_attempt_root.mkdir(exist_ok=True)
    _assert_no_disk3(physical_attempt_root, "physical template staging root")
    if template_root.stat().st_dev != mount_root.stat().st_dev:
        raise GateError(
            f"template_root is not on frozen mount filesystem: template_dev={template_root.stat().st_dev} "
            f"mount_dev={mount_root.stat().st_dev}"
        )
    if physical_attempt_root.stat().st_dev != template_root.stat().st_dev:
        raise GateError("physical staging root and final template root are on different filesystems")
    final = template_root / workload
    manifest_path = final / "database_manifest.json"
    if manifest_path.is_file():
        manifest = _adopt_existing_physical_template(workload, final, manifest_path)
        if manifest.get("status") != "complete" or manifest.get("freeze_id") != FREEZE_ID:
            raise GateError(f"existing template manifest is not reusable: {manifest_path}")
        if not (final / "datadir").is_dir():
            raise GateError(f"existing template lacks datadir: {final}")
        log("template_already_complete", workload=workload, manifest_sha256=sha256_file(manifest_path))
        return manifest
    if final.exists():
        raise GateError(f"template target exists without a complete manifest: {final}")

    nonce = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{os.getpid()}_{secrets.token_hex(4)}"
    attempt_name = f"{workload}_{nonce}"
    campaign_attempt = ATTEMPT_ROOT / attempt_name
    physical_attempt = physical_attempt_root / attempt_name
    datadir = physical_attempt / "datadir"
    datadir.mkdir(parents=True)
    campaign_attempt.mkdir(parents=True)
    atomic_json(campaign_attempt / "attempt.json", {
        "schema_version": 1,
        "freeze_id": FREEZE_ID,
        "workload": workload,
        "status": "building",
        "started_at_utc": utc_now(),
        "source_policy": "fresh_load_no_historical_result_reuse",
        "forbidden_mount_root": "/disk3",
        "physical_staging_root": str(physical_attempt),
        "physical_staging_device": _resolve_mount_device(physical_attempt),
        "final_template_root": str(final),
        "final_template_device": _resolve_mount_device(template_root),
        "same_filesystem_staging_and_final": physical_attempt.stat().st_dev == template_root.stat().st_dev,
    })
    name = f"ra_mysql_v3_build_{workload}_{os.getpid()}"
    _remove_own_container(name)
    try:
        run(_docker_run_args(
            machine, fixed, name=name, datadir=datadir, port=port,
            profile="none", initialize_empty=True,
        ))
        _wait_mysql(port, empty_password=True)
        _set_initial_password(port, password)
        log(
            "template_build_started",
            workload=workload,
            campaign_attempt=str(campaign_attempt),
            physical_attempt=str(physical_attempt),
        )
        runtime_adaptations: dict[str, Any] = {}
        if workload == "hybench":
            load_audit, hybench_case_adaptation = _build_hybench(port, campaign_attempt)
            runtime_adaptations["hybench_identifier_case"] = hybench_case_adaptation
            source_files = [
                HYBENCH_ROOT / "conf/ddl_mysql.sql",
                HYBENCH_ROOT / "conf/create_index_A2.sql",
                *sorted(path for path in HYBENCH_DATA.iterdir() if path.is_file()),
            ]
        elif workload == "job":
            load_audit = _build_job(port, campaign_attempt, physical_attempt)
            source_files = [JOB_SQL_ROOT / "schema.sql", JOB_SQL_ROOT / "fkindexes.sql", *sorted(JOB_DATA.glob("*.csv"))]
        elif workload == "tpch":
            load_audit = _build_tpch(port, password, campaign_attempt)
            source_files = [
                BENCHBASE_JAR,
                BENCHBASE_ROOT / "config/mysql/sample_tpch_config.xml",
                TPCH_COUNT_AUDIT_SOURCE,
                TPCH_COUNT_AUDIT_CLASS,
            ]
        else:
            raise GateError(f"unsupported template workload: {workload}")
        atomic_json(campaign_attempt / "load_audit.json", {
            "schema_version": 1,
            "freeze_id": FREEZE_ID,
            "global_campaign_id": GLOBAL_CAMPAIGN_ID,
            "engine": ENGINE,
            "workload": workload,
            "database": database,
            "status": "passed",
            "all_source_loaded_final_counts_equal": True,
            "all_load_warning_counts_zero": True,
            "runtime_adaptations": runtime_adaptations,
            "tables": load_audit,
        })
        _analyze(port, database)
        manifest = _template_manifest(
            port, workload, database, artifact_readback, image_readback, source_files,
            load_audit, runtime_adaptations,
        )
        fixed_readback = _fixed_setting_readback(port)
        manifest["fixed_setting_readback"] = fixed_readback
        run(["docker", "stop", "--time", "120", name], timeout=180)
        run(["docker", "rm", name])
        name = ""
        run(["sudo", "-n", "chown", "-R", f"{os.getuid()}:{os.getgid()}", datadir])
        database_size, snapshot_sha = _tree_size_and_sha256(datadir)
        manifest["database_size_bytes"] = database_size
        manifest["template_snapshot_sha256"] = snapshot_sha
        manifest["clean_database_shutdown_before_snapshot"] = True
        publication_before_stat = physical_attempt.stat()
        publication_before = {
            "device": publication_before_stat.st_dev,
            "inode": publication_before_stat.st_ino,
        }
        if publication_before_stat.st_dev != final.parent.stat().st_dev:
            raise GateError("template staging and final parent differ at publication time")
        manifest["publication_protocol"] = {
            "staging_root": str(physical_attempt),
            "final_root": str(final),
            "same_filesystem_required": True,
            "inode_preserving_rename_required": True,
            "campaign_preflight_contains_only_manifests_and_logs": True,
            "method": "chmod_staging_root_then_os_rename_then_reseal",
        }
        atomic_json(physical_attempt / "database_manifest.json", manifest)
        _make_read_only(physical_attempt)
        # Moving a directory to a different parent updates its `..` entry on
        # Linux and therefore requires write permission on the directory
        # itself.  Keep every child immutable, briefly make only the staging
        # root movable, perform the same-filesystem atomic rename, then seal
        # the final root again.  A failed rename restores the staging root.
        os.chmod(physical_attempt, 0o755)
        try:
            os.rename(physical_attempt, final)
        except OSError:
            os.chmod(physical_attempt, 0o555)
            raise
        os.chmod(final, 0o555)
        publication_after_stat = final.stat()
        publication_after = {
            "device": publication_after_stat.st_dev,
            "inode": publication_after_stat.st_ino,
        }
        if publication_after != publication_before:
            raise GateError(
                f"template publication did not preserve same-filesystem inode: "
                f"before={publication_before} after={publication_after}"
            )
        publication = {
            "status": "passed",
            "method": "chmod_staging_root_then_os_rename_then_reseal",
            "source": str(physical_attempt),
            "target": str(final),
            "same_filesystem_verified_before_publish": True,
            "inode_preserved": True,
            "before": publication_before,
            "after": publication_after,
            "final_mode": oct(stat.S_IMODE(final.stat().st_mode)),
            "published_at_utc": utc_now(),
        }
        atomic_json(campaign_attempt / "publication.json", publication)
        shutil.copyfile(final / "database_manifest.json", campaign_attempt / "database_manifest.json")
        atomic_json(campaign_attempt / "completion.json", {
            "schema_version": 1,
            "freeze_id": FREEZE_ID,
            "workload": workload,
            "status": "complete",
            "completed_at_utc": utc_now(),
            "final_template_root": str(final),
            "final_template_device": _resolve_mount_device(final),
            "final_template_root_mode": oct(stat.S_IMODE(final.stat().st_mode)),
            "same_filesystem_atomic_directory_rename": True,
            "database_manifest_sha256": sha256_file(final / "database_manifest.json"),
            "publication": publication,
        })
        log("template_build_completed", workload=workload, manifest_sha256=sha256_file(final / "database_manifest.json"))
        return manifest
    except Exception as exc:
        atomic_json(campaign_attempt / "failure.json", {
            "schema_version": 1,
            "freeze_id": FREEZE_ID,
            "workload": workload,
            "status": "failed",
            "failed_at_utc": utc_now(),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "physical_staging_root": str(physical_attempt),
            "physical_staging_retained": physical_attempt.exists(),
        })
        raise
    finally:
        if name:
            _remove_own_container(name)


def _fixed_setting_readback(port: int) -> dict[str, Any]:
    variable_names = [
        "innodb_flush_method", "innodb_redo_log_capacity",
        "innodb_flush_log_at_trx_commit", "innodb_doublewrite",
        "innodb_adaptive_hash_index", "transaction_isolation",
        "performance_schema", "innodb_buffer_pool_size",
        "innodb_buffer_pool_instances", "innodb_buffer_pool_chunk_size",
        "tmp_table_size", "max_heap_table_size", "max_connections",
    ]
    quoted = ",".join(f"'{name}'" for name in variable_names)
    rows = _tabular(
        port,
        f"SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema.global_variables WHERE VARIABLE_NAME IN ({quoted}) ORDER BY VARIABLE_NAME;",
    )
    values = {row[0].lower(): row[1] for row in rows}
    missing = sorted(set(variable_names) - set(values))
    if missing:
        raise GateError(f"fixed setting readback missing variables: {missing}")
    binary_log = _mysql(port, "SELECT @@global.log_bin;").strip()
    if binary_log != "0":
        raise GateError(f"skip_log_bin readback mismatch: log_bin={binary_log}")
    values["log_bin"] = binary_log
    values["query_execution_dop"] = "1"
    values["query_execution_dop_evidence"] = "MySQL Community has no parallel query DOP variable; all frozen clients are configured serial per query"
    return values


def build_templates() -> dict[str, Any]:
    machine, fixed, benchmark = _frozen()
    engine = machine["engines"]["mysql"]
    for key in ("template_root", "working_root", "mount_root"):
        _assert_no_disk3(Path(engine[key]), f"frozen mysql {key}")
    if engine.get("forbidden_mount_root") != "/disk3":
        raise GateError("frozen MySQL forbidden mount root is not /disk3")
    device = _resolve_mount_device(Path(engine["mount_root"]))
    if device != engine["observed_device"]:
        raise GateError(f"/disk2 device readback mismatch: expected={engine['observed_device']} actual={device}")
    artifact_readback = _verify_artifacts(benchmark)
    image_readback = _docker_image_readback(machine)
    password, created = _credential()
    log("credential_ready", path=str(MYSQL_CREDENTIAL), mode="0600", created=created)

    lock_path = Path(machine["global_isolation"]["measurement_lock"])
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_handle:
        log("waiting_for_host_measurement_lock", lock=str(lock_path))
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        log("host_measurement_lock_acquired", lock=str(lock_path))
        machine_readback = apply_and_read_machine(machine)
        templates = {
            "hybench": _build_one(
                "hybench", "hybench_sf10x", 54361, machine, fixed,
                artifact_readback, image_readback, password,
            ),
            "job": _build_one(
                "job", "imdb_job", 54362, machine, fixed,
                artifact_readback, image_readback, password,
            ),
            "tpch": _build_one(
                "tpch", "tpch_sf10", 54363, machine, fixed,
                artifact_readback, image_readback, password,
            ),
        }
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        log("host_measurement_lock_released", lock=str(lock_path))
    summary = {
        "schema_version": 1,
        "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        "engine": ENGINE,
        "status": "complete",
        "completed_at_utc": utc_now(),
        "device": device,
        "machine": machine_readback,
        "image": image_readback,
        "artifacts": artifact_readback,
        "templates": {
            name: {
                "database": manifest["database"],
                "row_counts": manifest["row_counts"],
                "manifest": str(Path(engine["template_root"]) / name / "database_manifest.json"),
                "manifest_sha256": sha256_file(Path(engine["template_root"]) / name / "database_manifest.json"),
                "physical_template_adoption": manifest.get("physical_template_adoption"),
            }
            for name, manifest in templates.items()
        },
    }
    atomic_json(PREFLIGHT_ROOT / "template_build_summary.json", summary)
    return summary


def verify_staging_layout() -> dict[str, Any]:
    machine, _, _ = _frozen()
    engine = machine["engines"]["mysql"]
    mount_root = Path(engine["mount_root"])
    template_root = Path(engine["template_root"])
    physical_staging = template_root / ".build_attempts"
    campaign_log_root = ATTEMPT_ROOT
    template_root.mkdir(parents=True, exist_ok=True)
    physical_staging.mkdir(exist_ok=True)
    campaign_log_root.mkdir(parents=True, exist_ok=True)
    for path, label in (
        (mount_root, "mount_root"),
        (template_root, "template_root"),
        (physical_staging, "physical_staging"),
    ):
        _assert_no_disk3(path, label)
    devices = {
        "mount_root": _resolve_mount_device(mount_root),
        "template_root": _resolve_mount_device(template_root),
        "physical_staging": _resolve_mount_device(physical_staging),
        "campaign_log_root": _resolve_mount_device(campaign_log_root),
    }
    expected = engine["observed_device"]
    if any(devices[key] != expected for key in ("mount_root", "template_root", "physical_staging")):
        raise GateError(f"physical staging device mismatch: expected={expected} actual={devices}")
    same = mount_root.stat().st_dev == template_root.stat().st_dev == physical_staging.stat().st_dev
    if not same:
        raise GateError("physical staging and final template roots are not on one filesystem")
    if campaign_log_root.stat().st_dev == physical_staging.stat().st_dev:
        raise GateError("campaign log root unexpectedly shares the physical template filesystem")
    payload = {
        "status": "passed",
        "mount_root": str(mount_root),
        "template_root": str(template_root),
        "physical_staging_root": str(physical_staging),
        "campaign_log_root": str(campaign_log_root),
        "devices": devices,
        "same_filesystem_physical_staging_and_final": same,
        "campaign_preflight_contains_only_manifests_and_logs": True,
    }
    atomic_json(PREFLIGHT_ROOT / "staging_layout_readback.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "verify-staging", "build-templates"))
    args = parser.parse_args()
    if args.command == "verify":
        machine, _, benchmark = _frozen()
        payload = {
            "status": "passed",
            "device": _resolve_mount_device(Path(machine["engines"]["mysql"]["mount_root"])),
            "image": _docker_image_readback(machine),
            "artifacts": _verify_artifacts(benchmark),
        }
    elif args.command == "verify-staging":
        payload = verify_staging_layout()
    else:
        verify_staging_layout()
        payload = build_templates()
    print("FORMAL_MYSQL_GATE=" + json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GateError, ProtocolError, OSError) as exc:
        log("fatal", error_type=type(exc).__name__, error=str(exc))
        raise SystemExit(2)
