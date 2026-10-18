#!/usr/bin/env python3
"""Prepare and seal isolated TPC-C candidate sessions without changing PG defaults."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence
import urllib.parse
import xml.etree.ElementTree as ET


PARAMETERS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
ATTEMPT15_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt15_direction_retrain_cgroup_sealed_20260727"
)
DRIVER_CLASS = "resourceadaptive.tpcc.SessionReadbackDriver"
SEMANTICS = {
    "formal_contract": "SET_LOCAL_plus_immediate_readback",
    "requested_scope": "candidate_connections_only",
    "effective_scope": "transaction",
    "application_method": "jdbc_connection_transaction_wrapper",
    "transaction_local": True,
    "set_local_used": True,
    "alter_system_used": False,
    "session_startup_options_used": False,
    "reapply_after_commit_or_rollback": True,
    "ordering": "seven_SET_LOCAL_then_current_setting_readback_then_first_business_SQL",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_new(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def canonical_params(value: Mapping[str, Any]) -> Dict[str, str]:
    keys = set(value)
    expected = set(PARAMETERS)
    if keys != expected:
        raise ValueError(
            f"candidate must contain exactly the seven planner parameters; "
            f"missing={sorted(expected - keys)}, extra={sorted(keys - expected)}"
        )
    result: Dict[str, str] = {}
    for name in PARAMETERS:
        try:
            number = float(value[name])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be numeric") from exc
        if not math.isfinite(number) or number < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
        result[name] = "0" if number == 0 else format(number, ".17g")
    return result


def candidate_jdbc_url(base_url: str, application_name: str) -> str:
    prefix = "jdbc:postgresql:"
    if not base_url.startswith(prefix):
        raise ValueError("only PostgreSQL JDBC URLs are supported")
    parsed = urllib.parse.urlsplit(base_url[len("jdbc:") :])
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if any(key.lower() == "options" for key, _ in query):
        raise ValueError("JDBC startup options are forbidden by the transaction-local contract")
    query = [(key, value) for key, value in query if key.lower() != "applicationname"]
    query.append(("ApplicationName", application_name))
    rebuilt = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )
    return "jdbc:ra-" + rebuilt


def set_xml_text(root: ET.Element, name: str, value: str) -> None:
    node = root.find(name)
    if node is None:
        raise ValueError(f"BenchBase XML is missing <{name}>")
    node.text = value


def read_json_object(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def ensure_new_bundle_path(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == ATTEMPT15_ROOT or ATTEMPT15_ROOT in resolved.parents:
        raise ValueError("refusing to create session-local artifacts inside attempt15 evidence")
    if resolved.exists():
        raise ValueError(f"output directory already exists: {resolved}")
    return resolved


def compile_jdbc_bundle(output_dir: Path, postgresql_jdbc_jar: Path) -> Path:
    source_dir = Path(__file__).with_name("tpcc_session_jdbc")
    sources = [
        source_dir / "SessionReadbackDriver.java",
        source_dir / "TpccSensitiveSessionClient.java",
    ]
    for source in sources:
        if not source.is_file():
            raise ValueError(f"JDBC bundle source is missing: {source}")
    if not postgresql_jdbc_jar.is_file():
        raise ValueError(f"PostgreSQL JDBC jar is missing: {postgresql_jdbc_jar}")
    classes_dir = output_dir / "jdbc_classes"
    classes_dir.mkdir()
    subprocess.run(
        [
            "javac",
            "-cp",
            str(postgresql_jdbc_jar.resolve()),
            "-d",
            str(classes_dir),
            *(str(source) for source in sources),
        ],
        check=True,
    )
    driver_jar = output_dir / "lib/tpcc-session-readback-driver.jar"
    driver_jar.parent.mkdir()
    subprocess.run(
        ["jar", "--create", "--file", str(driver_jar), "-C", str(classes_dir), "."],
        check=True,
    )
    return driver_jar


def prepare_bundle(args: argparse.Namespace) -> Path:
    output_dir = ensure_new_bundle_path(args.output_dir)
    if min(
        args.native_terminals,
        args.sensitive_clients,
        args.duration_seconds,
        args.warehouses,
    ) <= 0:
        raise ValueError("native terminals, sensitive clients, duration, and warehouses must be positive")
    if not args.benchbase_jar.is_file():
        raise ValueError(f"BenchBase jar is missing: {args.benchbase_jar}")
    params_document = read_json_object(args.params, label="candidate params")
    params = canonical_params(params_document)
    tree = ET.parse(args.benchbase_xml)
    root = tree.getroot()
    url_node = root.find("url")
    if url_node is None or not (url_node.text or "").strip():
        raise ValueError("BenchBase XML is missing a PostgreSQL JDBC URL")
    base_jdbc_url = (
        str(args.jdbc_url)
        if getattr(args, "jdbc_url", None)
        else url_node.text.strip()
    )
    native_application = f"ra-tpcc-native-{args.calibration_id}"
    sensitive_application = f"ra-tpcc-sensitive-{args.calibration_id}"
    native_url = candidate_jdbc_url(base_jdbc_url, native_application)
    sensitive_url = candidate_jdbc_url(base_jdbc_url, sensitive_application)
    set_xml_text(root, "driver", DRIVER_CLASS)
    set_xml_text(root, "url", native_url)
    set_xml_text(root, "username", args.database_user)
    password_environment = getattr(args, "database_password_env", "PGPASSWORD")
    password = os.environ.get(password_environment)
    if password is None:
        raise ValueError(f"database password environment is missing: {password_environment}")
    set_xml_text(root, "password", password)
    set_xml_text(root, "scalefactor", str(args.warehouses))
    set_xml_text(root, "terminals", str(args.native_terminals))
    set_xml_text(root, "works/work/time", str(args.duration_seconds))

    output_dir.mkdir(parents=True)
    driver_jar = compile_jdbc_bundle(output_dir, args.postgresql_jdbc_jar)
    native_xml = output_dir / "benchbase_native_session.xml"
    tree.write(native_xml, encoding="utf-8", xml_declaration=True)
    readbacks = output_dir / "connection_readbacks.jsonl"
    sensitive_metrics = output_dir / "sensitive_metrics.json"
    native_environment = {
        "RA_SESSION_READBACK_PATH": str(readbacks),
        "RA_SESSION_CLIENT_KIND": "native",
        "RA_SESSION_CALIBRATION_ID": args.calibration_id,
        **{
            f"RA_TPCC_PARAM_{name.upper()}": params[name]
            for name in PARAMETERS
        },
    }
    sensitive_environment = {
        "RA_SESSION_READBACK_PATH": str(readbacks),
        "RA_SESSION_CLIENT_KIND": "sensitive",
        "RA_SESSION_CALIBRATION_ID": args.calibration_id,
        **{
            f"RA_TPCC_PARAM_{name.upper()}": params[name]
            for name in PARAMETERS
        },
    }
    native_command = [
        "java",
        args.java_xmx,
        "-cp",
        (
            f"{driver_jar}:{args.benchbase_jar.resolve()}:"
            f"{args.benchbase_jar.resolve().parent / 'lib'}/*"
        ),
        "com.oltpbenchmark.DBWorkload",
        "-b",
        "tpcc",
        "-c",
        str(native_xml),
        "--execute=true",
    ]
    sensitive_command = [
        "java",
        "-cp",
        f"{driver_jar}:{args.postgresql_jdbc_jar.resolve()}",
        "resourceadaptive.tpcc.TpccSensitiveSessionClient",
        "--url",
        sensitive_url,
        "--user",
        args.database_user,
        "--clients",
        str(args.sensitive_clients),
        "--duration-seconds",
        str(args.duration_seconds),
        "--warehouses",
        str(args.warehouses),
        "--output",
        str(sensitive_metrics),
    ]
    manifest = {
        "schema_version": 1,
        "artifact_type": "tpcc_session_local_calibration_manifest_v1",
        "calibration_id": args.calibration_id,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidate_params": params,
        "parameter_semantics": SEMANTICS,
        "connection_contract": {
            "native": {
                "implementation": "BenchBase JDBC through audit driver",
                "minimum_connection_readbacks": args.native_terminals,
                "application_name": native_application,
                "jdbc_url": native_url,
                "environment": native_environment,
                "command": native_command,
            },
            "sensitive": {
                "implementation": "dedicated JDBC StockLevel client through audit driver",
                "minimum_connection_readbacks": args.sensitive_clients,
                "application_name": sensitive_application,
                "jdbc_url": sensitive_url,
                "environment": sensitive_environment,
                "required_runtime_secret_environment": ["TPCC_DB_PASSWORD"],
                "command": sensitive_command,
            },
            "readback_timing": (
                "inside_each_transaction_after_seven_SET_LOCAL_and_before_first_business_SQL"
            ),
            "readback_path": str(readbacks),
        },
        "global_default_contract": {
            "snapshot_tool": "snapshot-defaults subcommand",
            "before_required": True,
            "after_required": True,
            "exact_match_required": True,
            "candidate_startup_options_forbidden_on_auditor_connection": True,
        },
        "build_contract": {
            "driver_source": str(
                Path(__file__).with_name("tpcc_session_jdbc") / "SessionReadbackDriver.java"
            ),
            "sensitive_source": str(
                Path(__file__).with_name("tpcc_session_jdbc") / "TpccSensitiveSessionClient.java"
            ),
            "postgresql_jdbc_jar": str(args.postgresql_jdbc_jar.resolve()),
            "driver_jar": str(driver_jar),
        },
        "dataset_builder_contract": {
            "seal_artifact_type": "tpcc_session_local_calibration_seal_v1",
            "admit_only_when_seal_usable": True,
            "parameter_scope_field": "parameter_semantics.effective_scope",
            "transaction_local_required": True,
            "session_startup_only_evidence_forbidden": True,
        },
    }
    manifest_path = output_dir / "session_local_manifest.json"
    write_json_new(manifest_path, manifest)
    return manifest_path


def connect_without_candidate_options(args: argparse.Namespace) -> Any:
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2 is required for defaults snapshots") from exc
    return psycopg2.connect(
        host=args.host,
        port=args.port,
        dbname=args.database,
        user=args.user,
        password=args.password,
        application_name="ra-tpcc-global-default-auditor",
        connect_timeout=10,
    )


def snapshot_defaults(args: argparse.Namespace) -> Path:
    connection = connect_without_candidate_options(args)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT name, setting, reset_val, source, COALESCE(sourcefile, '')
                FROM pg_settings
                WHERE name = ANY(%s)
                ORDER BY name
                """,
                (list(PARAMETERS),),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    if len(rows) != len(PARAMETERS):
        raise RuntimeError("global-default snapshot did not return all seven parameters")
    document = {
        "schema_version": 1,
        "artifact_type": "tpcc_pg_global_defaults_snapshot_v1",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "auditor_connection_candidate_options": False,
        "parameters": {
            name: {
                "setting": setting,
                "reset_val": reset_val,
                "source": source,
                "sourcefile": sourcefile or None,
            }
            for name, setting, reset_val, source, sourcefile in rows
        },
    }
    write_json_new(args.output, document)
    return args.output


def load_readbacks(path: Path) -> list[Mapping[str, Any]]:
    records = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"connection readback file is unreadable: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid readback JSON at line {line_number}") from exc
        if not isinstance(record, Mapping):
            raise ValueError(f"readback line {line_number} is not an object")
        records.append(record)
    return records


def validate_readbacks(
    manifest: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    expected = canonical_params(manifest.get("candidate_params") or {})
    calibration_id = str(manifest.get("calibration_id") or "")
    if not calibration_id:
        raise ValueError("manifest calibration_id is missing")
    grouped: Dict[str, list[Mapping[str, Any]]] = {"native": [], "sensitive": []}
    connection_ids = set()
    ordered_records = list(records)
    readbacks_by_transaction: Dict[str, Mapping[str, Any]] = {}
    dispatches_by_transaction: Dict[str, Mapping[str, Any]] = {}
    readback_positions: Dict[str, int] = {}
    dispatch_positions: Dict[str, int] = {}
    last_position_by_connection: Dict[str, int] = {}
    record_count_by_connection: Dict[str, int] = {}
    for position, record in enumerate(ordered_records):
        kind = str(record.get("client_kind") or "")
        if kind not in grouped:
            raise ValueError(f"unexpected readback client_kind: {kind!r}")
        connection_id = str(record.get("connection_id") or "")
        if not connection_id:
            raise ValueError("transaction audit connection_id is missing")
        connection_ids.add(connection_id)
        connection_position = record_count_by_connection.get(connection_id, 0)
        record_count_by_connection[connection_id] = connection_position + 1
        last_position_by_connection[connection_id] = connection_position
        if record.get("calibration_id") != calibration_id:
            raise ValueError(f"{kind} transaction audit belongs to another calibration")
        transaction_id = str(record.get("transaction_id") or "")
        if not transaction_id:
            raise ValueError("transaction audit transaction_id is missing")
        event = record.get("event")
        if event == "tpcc_transaction_parameter_readback":
            if transaction_id in readbacks_by_transaction:
                raise ValueError("transaction has duplicate parameter readbacks")
            actual = canonical_params(record.get("settings") or {})
            if actual != expected:
                raise ValueError(
                    f"{kind} transaction {transaction_id} readback mismatch: "
                    f"expected={expected}, actual={actual}"
                )
            if record.get("readback_phase") != "after_set_local_before_business_sql":
                raise ValueError(f"{kind} transaction did not prove ordered readback")
            readbacks_by_transaction[transaction_id] = record
            readback_positions[transaction_id] = connection_position
            grouped[kind].append(record)
        elif event == "tpcc_transaction_business_sql_dispatch":
            if transaction_id in dispatches_by_transaction:
                raise ValueError("transaction has duplicate first-business dispatch")
            dispatches_by_transaction[transaction_id] = record
            dispatch_positions[transaction_id] = connection_position
        else:
            raise ValueError(f"unexpected transaction audit event: {event!r}")
    if not set(dispatches_by_transaction).issubset(readbacks_by_transaction):
        raise ValueError("business dispatch is missing its transaction readback")
    incomplete_transactions = (
        set(readbacks_by_transaction) - set(dispatches_by_transaction)
    )
    incomplete_by_connection: Dict[str, int] = {}
    for transaction_id in incomplete_transactions:
        readback = readbacks_by_transaction[transaction_id]
        connection_id = str(readback["connection_id"])
        incomplete_by_connection[connection_id] = (
            incomplete_by_connection.get(connection_id, 0) + 1
        )
        if (
            incomplete_by_connection[connection_id] > 1
            or readback_positions[transaction_id]
            != last_position_by_connection[connection_id]
        ):
            raise ValueError(
                "readback-only transaction must be the sole trailing audit "
                "record for its connection"
            )
    for transaction_id, dispatch in dispatches_by_transaction.items():
        readback = readbacks_by_transaction[transaction_id]
        if (
            readback.get("client_kind") != dispatch.get("client_kind")
            or readback.get("connection_id") != dispatch.get("connection_id")
            or int(readback.get("sequence") or 0) + 1
            != int(dispatch.get("sequence") or 0)
            or readback_positions[transaction_id] + 1
            != dispatch_positions[transaction_id]
        ):
            raise ValueError("business SQL was not dispatched immediately after readback")
    contract = manifest.get("connection_contract")
    if not isinstance(contract, Mapping):
        raise ValueError("manifest connection_contract is missing")
    result: Dict[str, Any] = {}
    for kind, values in grouped.items():
        kind_contract = contract.get(kind)
        if not isinstance(kind_contract, Mapping):
            raise ValueError(f"manifest {kind} connection contract is missing")
        expected_application_name = str(kind_contract.get("application_name") or "")
        if not expected_application_name:
            raise ValueError(f"manifest {kind} application_name is missing")
        if any(
            record.get("application_name") != expected_application_name
            for record in values
        ):
            raise ValueError(f"{kind} connection application_name mismatch")
        minimum = int(kind_contract.get("minimum_connection_readbacks") or 0)
        unique_connections = {str(record["connection_id"]) for record in values}
        if len(unique_connections) < minimum:
            raise ValueError(
                f"{kind} audited connections insufficient: {len(unique_connections)}<{minimum}"
            )
        result[kind] = {
            "minimum_required": minimum,
            "observed_connections": len(unique_connections),
            "observed_transactions": len(values),
            "observed_complete_transactions": sum(
                str(record["transaction_id"]) in dispatches_by_transaction
                for record in values
            ),
            "aborted_before_business_transactions": sum(
                str(record["transaction_id"]) in incomplete_transactions
                for record in values
            ),
            "all_matched": True,
            "all_business_sql_after_readback": True,
        }
    return result


def seal_calibration(args: argparse.Namespace) -> Path:
    manifest = read_json_object(args.manifest, label="session-local manifest")
    if manifest.get("artifact_type") != "tpcc_session_local_calibration_manifest_v1":
        raise ValueError("unexpected session-local manifest artifact_type")
    semantics = manifest.get("parameter_semantics")
    if semantics != SEMANTICS:
        raise ValueError("manifest parameter semantics are missing or altered")
    before = read_json_object(args.defaults_before, label="defaults-before snapshot")
    after = read_json_object(args.defaults_after, label="defaults-after snapshot")
    expected_snapshot_type = "tpcc_pg_global_defaults_snapshot_v1"
    if (
        before.get("artifact_type") != expected_snapshot_type
        or after.get("artifact_type") != expected_snapshot_type
    ):
        raise ValueError("unexpected global-default snapshot artifact_type")
    if before.get("parameters") != after.get("parameters"):
        raise ValueError("PostgreSQL global defaults changed during candidate measurement")
    if (
        before.get("auditor_connection_candidate_options") is not False
        or after.get("auditor_connection_candidate_options") is not False
    ):
        raise ValueError("global-default snapshots were not taken on clean auditor connections")
    readback_audit = validate_readbacks(manifest, load_readbacks(args.readbacks))
    measurement = read_json_object(
        args.measurement_summary,
        label="measurement summary",
    )
    measurement_usable = (
        measurement.get("status") == "complete"
        and (measurement.get("window_mean_stability") or {}).get("stable") is True
        and (measurement.get("worker_cleanup") or {}).get("fully_released") is True
        and int(measurement.get("native_sql_exception_occurrences") or 0) == 0
    )
    if not measurement_usable:
        raise ValueError("measurement summary is not a usable stable TPC-C point")
    seal = {
        "schema_version": 1,
        "artifact_type": "tpcc_session_local_calibration_seal_v1",
        "status": "usable",
        "usable": True,
        "calibration_id": manifest.get("calibration_id"),
        "candidate_params": manifest.get("candidate_params"),
        "parameter_semantics": SEMANTICS,
        "connection_readback_audit": readback_audit,
        "global_defaults": {
            "unchanged": True,
            "before_path": str(args.defaults_before.resolve()),
            "before_sha256": sha256(args.defaults_before),
            "after_path": str(args.defaults_after.resolve()),
            "after_sha256": sha256(args.defaults_after),
        },
        "measurement_summary": {
            "path": str(args.measurement_summary.resolve()),
            "sha256": sha256(args.measurement_summary),
            "stable": True,
            "worker_cleanup_passed": True,
            "native_sql_exception_occurrences": 0,
        },
        "manifest": {
            "path": str(args.manifest.resolve()),
            "sha256": sha256(args.manifest),
        },
        "dataset_builder_contract": manifest.get("dataset_builder_contract"),
    }
    write_json_new(args.output, seal)
    return args.output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--calibration-id", required=True)
    prepare.add_argument("--params", type=Path, required=True)
    prepare.add_argument("--benchbase-xml", type=Path, required=True)
    prepare.add_argument("--benchbase-jar", type=Path, required=True)
    prepare.add_argument("--postgresql-jdbc-jar", type=Path, required=True)
    prepare.add_argument("--database-user", default="postgres")
    prepare.add_argument("--database-password-env", default="PGPASSWORD")
    prepare.add_argument("--jdbc-url")
    prepare.add_argument("--native-terminals", type=int, default=32)
    prepare.add_argument("--sensitive-clients", type=int, default=1)
    prepare.add_argument("--duration-seconds", type=int, required=True)
    prepare.add_argument("--warehouses", type=int, default=10)
    prepare.add_argument("--java-xmx", default="-Xmx4g")

    snapshot = subparsers.add_parser("snapshot-defaults")
    snapshot.add_argument("--host", required=True)
    snapshot.add_argument("--port", type=int, required=True)
    snapshot.add_argument("--database", required=True)
    snapshot.add_argument("--user", required=True)
    snapshot.add_argument("--password", required=True)
    snapshot.add_argument("--output", type=Path, required=True)

    seal = subparsers.add_parser("seal")
    seal.add_argument("--manifest", type=Path, required=True)
    seal.add_argument("--readbacks", type=Path, required=True)
    seal.add_argument("--defaults-before", type=Path, required=True)
    seal.add_argument("--defaults-after", type=Path, required=True)
    seal.add_argument("--measurement-summary", type=Path, required=True)
    seal.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        path = prepare_bundle(args)
    elif args.command == "snapshot-defaults":
        path = snapshot_defaults(args)
    elif args.command == "seal":
        path = seal_calibration(args)
    else:
        raise AssertionError(f"unhandled command: {args.command}")
    print(path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
