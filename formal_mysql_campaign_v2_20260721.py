#!/usr/bin/env python3
"""Execute the frozen MySQL v2 engine gate and brute-force schedule entries."""

from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
import csv
import fcntl
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import random
import secrets
import shutil
import signal
import statistics
import subprocess
import tempfile
import time
import tomllib
from typing import Any, Iterable, Mapping, Protocol, Sequence
import xml.etree.ElementTree as ET

from formal_mysql_engine_gate_v2_20260721 import (
    BENCHBASE_JAR,
    BENCHBASE_ROOT,
    HYBENCH_DATA,
    HYBENCH_CANONICAL_TABLE_NAMES,
    HYBENCH_JAR,
    HYBENCH_ROOT,
    JOB_SQL_ROOT,
    MYSQL_CONNECTOR,
    MYSQL_CREDENTIAL,
    PREFLIGHT_ROOT,
    _assert_no_disk3,
    _docker_image_readback,
    _docker_run_args,
    _fixed_setting_readback,
    _mysql,
    _normalize_hybench_table_identifier_case,
    _resolve_mount_device,
    _tabular,
    _tables,
    _verify_artifacts,
    _wait_mysql,
    apply_and_read_machine,
    atomic_json,
    run,
    GateError,
)
from formal_resource_sampler_20260721 import (
    CgroupV1Adapter,
    ResourceError,
    append_jsonl,
    coefficient_of_variation,
    stability_summary,
)
from formal_session_runtime_v2_20260721 import (
    FREEZE_ID,
    GLOBAL_CAMPAIGN_ID,
    GLOBAL_ROOT,
    RESOURCE_AWARENESS_SHA256,
    SHARED_FROZEN_ROOT,
    ProtocolError,
    claim_scheduler_entry,
    complete_scheduler_entry,
    load_json,
    load_schedule,
    publish_mysql_engine_ready,
    sha256_file,
    utc_now,
    validate_all_ready,
    validate_engine_ready,
    validate_scheduler_state,
    validate_shared_bundle,
)


SESSION_ID = "mysql_brute_force"
ENGINE = "mysql"
FORMAL_CLASSES = Path(__file__).resolve().parent / "formal_clients_20260721/classes"
HYBENCH_LAUNCHER_SOURCE = Path(__file__).resolve().parent / "formal_clients_20260721/com/hybench/HyBenchFormalMain.java"
SQL_CLIENT_SOURCE = Path(__file__).resolve().parent / "formal_clients_20260721/com/formal/FormalSQLWorkload.java"
TPCH_AGENT_SOURCE = Path(__file__).resolve().parent / "formal_clients_20260721/com/formal/TpchSeedAgent.java"
TPCH_AGENT = Path(__file__).resolve().parent / "formal_clients_20260721/tpch-seed-agent.jar"
HYBENCH_LAUNCHER_CLASS = FORMAL_CLASSES / "com/hybench/HyBenchFormalMain.class"
SQL_CLIENT_CLASS = FORMAL_CLASSES / "com/formal/FormalSQLWorkload.class"
BENCHMARK_CONFIG_ROOT = PREFLIGHT_ROOT / "benchmark_configs"
GATE_ROOT = PREFLIGHT_ROOT / "gate_attempts"
RESOURCE_CONTRACT = Path(os.environ.get(
    "RA_RESOURCE_CONTRACT",
    str(Path(__file__).resolve().parent / "config/formal/unified_resource_awareness_20260721_v3.json"),
))
HOST_MEASUREMENT_LOCK = Path(
    "/home/anonymous/program/resource_adaptive_mysql/history/unified_formal/.host_measurement.lock"
)


AP10_SQL_NAME = "mysql_ap10.sql"
Q18_SQL_NAME = "mysql_tpch_q18.sql"
Q18_SQL = """SELECT
   c_name,
   c_custkey,
   o_orderkey,
   o_orderdate,
   o_totalprice,
   SUM(l_quantity)
FROM
   customer,
   orders,
   lineitem
WHERE
   o_orderkey IN
   (
      SELECT
         l_orderkey
      FROM
         lineitem
      GROUP BY
         l_orderkey
      HAVING
         SUM(l_quantity) > ?
   )
   AND c_custkey = o_custkey
   AND o_orderkey = l_orderkey
GROUP BY
   c_name,
   c_custkey,
   o_orderkey,
   o_orderdate,
   o_totalprice
ORDER BY
   o_totalprice DESC,
   o_orderdate LIMIT 100
"""


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"at_utc": utc_now(), "event": event, **fields}, ensure_ascii=False, sort_keys=True), flush=True)


@contextmanager
def host_measurement_lock() -> Iterable[Any]:
    HOST_MEASUREMENT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with HOST_MEASUREMENT_LOCK.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield handle
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _host_cpu_counters() -> tuple[int, int]:
    pieces = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
    if not pieces or pieces[0] != "cpu" or len(pieces) < 9:
        raise GateError("cannot parse aggregate /proc/stat CPU counters")
    counters = [int(value) for value in pieces[1:]]
    total = sum(counters)
    idle = counters[3] + counters[4]
    return total, idle


def _host_disk_io_time_ms(device: str) -> int:
    major, minor = (int(item) for item in device.split(":", 1))
    for line in Path("/proc/diskstats").read_text(encoding="utf-8").splitlines():
        pieces = line.split()
        if len(pieces) >= 14 and int(pieces[0]) == major and int(pieces[1]) == minor:
            return int(pieces[12])
    raise GateError(f"host admission device absent from /proc/diskstats: {device}")


def _host_memory_psi() -> dict[str, Any]:
    path = Path("/proc/pressure/memory")
    if not path.is_file():
        return {"available": False}
    result: dict[str, Any] = {"available": True}
    for line in path.read_text(encoding="utf-8").splitlines():
        pieces = line.split()
        result[pieces[0]] = {
            key: (int(value) if key == "total" else float(value))
            for key, value in (piece.split("=", 1) for piece in pieces[1:])
        }
    return result


def host_admission(machine: Mapping[str, Any], output: Path) -> dict[str, Any]:
    """Apply the frozen two-pass host idle gate while the common flock is held."""
    policy = machine["global_isolation"]["host_admission"]
    mysql_device = _resolve_mount_device(Path(machine["engines"]["mysql"]["mount_root"]))
    pg_root = Path(machine["engines"]["postgresql"]["mount_root"])
    pg_device = _resolve_mount_device(pg_root)
    devices = {"postgresql": pg_device, "mysql": mysql_device}
    poll_seconds = int(policy["poll_seconds"])
    required = int(policy["required_consecutive_passes"])
    deadline = time.monotonic() + int(policy["timeout_seconds"])
    previous_cpu = _host_cpu_counters()
    previous_disks = {name: _host_disk_io_time_ms(device) for name, device in devices.items()}
    previous_time = time.monotonic()
    consecutive = 0
    observations = []
    while time.monotonic() < deadline:
        time.sleep(poll_seconds)
        current_time = time.monotonic()
        current_cpu = _host_cpu_counters()
        current_disks = {name: _host_disk_io_time_ms(device) for name, device in devices.items()}
        wall = current_time - previous_time
        total_delta = current_cpu[0] - previous_cpu[0]
        idle_delta = current_cpu[1] - previous_cpu[1]
        if total_delta <= 0 or wall <= 0:
            raise GateError("non-positive host admission counter interval")
        cpu_busy = 1.0 - idle_delta / total_delta
        disk_busy = {
            name: (current_disks[name] - previous_disks[name]) / (wall * 1000.0)
            for name in devices
        }
        passed = (
            cpu_busy < float(policy["cpu_busy_max_ratio"])
            and all(value < float(policy["target_disk_busy_max_ratio"]) for value in disk_busy.values())
        )
        consecutive = consecutive + 1 if passed else 0
        observations.append({
            "at_utc": utc_now(), "interval_seconds": wall,
            "cpu_busy_ratio": cpu_busy, "disk_busy_ratio": disk_busy,
            "memory_psi": _host_memory_psi(), "passed": passed,
            "consecutive_passes": consecutive,
        })
        previous_cpu = current_cpu
        previous_disks = current_disks
        previous_time = current_time
        if consecutive >= required:
            payload = {
                "schema_version": 1, "freeze_id": FREEZE_ID,
                "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
                "status": "passed", "policy": policy, "devices": devices,
                "observations": observations, "completed_at_utc": utc_now(),
            }
            atomic_json(output, payload)
            return payload
    payload = {
        "schema_version": 1, "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
        "status": "failed", "policy": policy, "devices": devices,
        "observations": observations, "failed_at_utc": utc_now(),
        "reason": "host_admission_timeout",
    }
    atomic_json(output, payload)
    raise GateError("host admission did not reach the frozen consecutive-pass rule before timeout")


def _configs() -> tuple[dict[str, Any], ...]:
    validate_shared_bundle()
    names = (
        "machine_and_resources.json", "fixed_db_settings.json", "benchmark_matrix.json",
        "preflight_gates.json", "measurement_protocol.json", "search_spaces.json",
        "method_protocols.json", "acceptance_policy.json",
    )
    return tuple(load_json(SHARED_FROZEN_ROOT / name) for name in names)


def _write_immutable(path: Path, data: bytes) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
            raise GateError(f"immutable generated artifact differs: {path}")
        if (path.stat().st_mode & 0o222) != 0:
            raise GateError(f"immutable generated artifact is writable: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def prepare_benchmark_configs() -> dict[str, Any]:
    machine, _, benchmark, *_ = _configs()
    if sha256_file(RESOURCE_CONTRACT) != RESOURCE_AWARENESS_SHA256:
        raise GateError("resource-awareness contract SHA mismatch")
    artifacts = _verify_artifacts(benchmark)
    for path in (
        HYBENCH_LAUNCHER_SOURCE, HYBENCH_LAUNCHER_CLASS,
        SQL_CLIENT_SOURCE, SQL_CLIENT_CLASS, TPCH_AGENT_SOURCE, TPCH_AGENT,
    ):
        if not path.is_file():
            raise GateError(f"formal client adaptation is absent: {path}")
    adapter_artifacts = {
        "hybench_observation_launcher_source": {"path": str(HYBENCH_LAUNCHER_SOURCE), "sha256": sha256_file(HYBENCH_LAUNCHER_SOURCE)},
        "hybench_observation_launcher_class": {"path": str(HYBENCH_LAUNCHER_CLASS), "sha256": sha256_file(HYBENCH_LAUNCHER_CLASS)},
        "sql_prepared_client_source": {"path": str(SQL_CLIENT_SOURCE), "sha256": sha256_file(SQL_CLIENT_SOURCE)},
        "sql_prepared_client_class": {"path": str(SQL_CLIENT_CLASS), "sha256": sha256_file(SQL_CLIENT_CLASS)},
        "tpch_seed_agent_source": {"path": str(TPCH_AGENT_SOURCE), "sha256": sha256_file(TPCH_AGENT_SOURCE)},
        "tpch_seed_agent_jar": {"path": str(TPCH_AGENT), "sha256": sha256_file(TPCH_AGENT)},
    }

    hybench_statement_source = HYBENCH_ROOT / "conf/stmt_mysql.toml"
    with hybench_statement_source.open("rb") as handle:
        statements = tomllib.load(handle)
    normalized_hybench_statements = _normalize_hybench_table_identifier_case(
        hybench_statement_source.read_text(encoding="utf-8")
    )
    adapter_artifacts["hybench_identifier_case_normalization"] = {
        "type": "private_runtime_sql_identifier_case_normalization",
        "reason": "upstream frozen MySQL statements mix table identifier case",
        "canonical_table_names": list(HYBENCH_CANONICAL_TABLE_NAMES),
        "source_path": str(hybench_statement_source),
        "source_sha256": sha256_file(hybench_statement_source),
        "normalized_sha256": hashlib.sha256(
            normalized_hybench_statements.encode("utf-8")
        ).hexdigest(),
        "source_file_modified": False,
        "semantic_change": False,
    }
    ap10 = statements["AP-10"]["sql"].strip()
    sql_root = PREFLIGHT_ROOT / "sql"
    ap10_path = sql_root / AP10_SQL_NAME
    q18_path = sql_root / Q18_SQL_NAME
    _write_immutable(ap10_path, (ap10 + "\n").encode("utf-8"))
    _write_immutable(q18_path, Q18_SQL.encode("utf-8"))

    mysql = machine["engines"]["mysql"]
    port_by_workload = {
        "hybench_ap": mysql["containers"]["hybench"]["port"],
        "hybench_xp": mysql["containers"]["hybench"]["port"],
        "job": mysql["containers"]["job"]["port"],
        "tpch": mysql["containers"]["tpch"]["port"],
    }
    database_by_workload = {
        "hybench_ap": "hybench_sf10x", "hybench_xp": "hybench_sf10x",
        "job": "imdb_job", "tpch": "tpch_sf10",
    }
    sql_by_workload = {
        "hybench_ap": ap10_path, "hybench_xp": ap10_path,
        "job": JOB_SQL_ROOT / "26a.sql", "tpch": q18_path,
    }
    generated: dict[str, Any] = {}
    for workload in ("hybench_ap", "job", "tpch", "hybench_xp"):
        frozen = benchmark["workloads"][workload]
        port = port_by_workload[workload]
        database = database_by_workload[workload]
        jdbc = (
            f"jdbc:mysql://127.0.0.1:{port}/{database}?"
            "useUnicode=true&characterEncoding=utf-8&useSSL=false&allowPublicKeyRetrieval=true&"
            "useServerPrepStmts=true&cachePrepStmts=true&useCursorFetch=true&defaultFetchSize=1000"
        )
        payload = {
            "schema_version": 1,
            "freeze_id": FREEZE_ID,
            "global_campaign_id": GLOBAL_CAMPAIGN_ID,
            "engine": ENGINE,
            "workload": workload,
            "status": "frozen",
            "workload_seed": benchmark["global"]["workload_seed"],
            "closed_loop": True,
            "rate": "unlimited",
            "think_time_seconds": 0,
            "transaction_isolation": "READ COMMITTED",
            "jdbc_autocommit": False,
            "commit_after_each_query_or_transaction": True,
            "fetch_size": 1000,
            "prepared_statements": True,
            "jdbc_url": jdbc,
            "credential_file": str(MYSQL_CREDENTIAL),
            "credential_value_recorded": False,
            "database": database,
            "port": port,
            "frozen_workload": frozen,
            "target_sql": str(sql_by_workload[workload]),
            "target_sql_sha256": sha256_file(sql_by_workload[workload]),
            "frozen_artifacts": artifacts,
            "runner_adaptations": adapter_artifacts,
            "resource_awareness_path": str(RESOURCE_CONTRACT),
            "resource_awareness_sha256": RESOURCE_AWARENESS_SHA256,
            "tpch_seed_resolution": (
                {
                    "benchbase_hardcoded_seed_observed": 15721,
                    "frozen_runtime_seed": 4508333,
                    "adapter": str(TPCH_AGENT),
                    "frozen_benchbase_jar_unchanged": True,
                    "first_q18_parameter": 314,
                } if workload == "tpch" else None
            ),
        }
        path = BENCHMARK_CONFIG_ROOT / workload / "benchmark_config.json"
        _write_immutable(path, _json_bytes(payload))
        generated[workload] = {"path": str(path), "sha256": sha256_file(path)}
    summary = {
        "schema_version": 1, "freeze_id": FREEZE_ID, "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        "engine": ENGINE, "status": "complete", "benchmark_configs": generated,
    }
    atomic_json(PREFLIGHT_ROOT / "benchmark_config_summary.json", summary)
    return summary


def _password() -> str:
    import configparser
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(MYSQL_CREDENTIAL, encoding="utf-8")
    password = parser.get("client", "password", fallback="").strip('"\'')
    if not password:
        raise GateError("MySQL credential has no password")
    return password


def _parse_size(value: str) -> int:
    match = re.fullmatch(r"([0-9]+)([KMG])?", value, flags=re.I)
    if not match:
        raise GateError(f"cannot parse frozen MySQL size: {value}")
    number = int(match.group(1))
    multiplier = {None: 1, "K": 1024, "M": 1024**2, "G": 1024**3}[match.group(2).upper() if match.group(2) else None]
    return number * multiplier


def _owned_container(name: str) -> bool:
    proc = run(["docker", "inspect", name, "--format", "{{json .Config.Labels}}"], check=False)
    if proc.returncode:
        return False
    labels = json.loads(proc.stdout)
    if labels.get("ra.formal_campaign") != GLOBAL_CAMPAIGN_ID or labels.get("ra.formal_engine") != ENGINE:
        raise GateError(f"container name is occupied by a non-campaign container: {name}")
    return True


def _remove_container(name: str) -> None:
    if _owned_container(name):
        run(["docker", "rm", "--force", name])


def _copy_template(source: Path, target: Path, working_root: Path) -> None:
    source = source.resolve()
    target_parent = target.parent.resolve(strict=False)
    root = working_root.resolve()
    if root != target_parent and root not in target_parent.parents:
        raise GateError(f"working target escapes frozen working root: {target}")
    _assert_no_disk3(source, "template source")
    _assert_no_disk3(target, "working datadir")
    if target.exists():
        raise GateError(f"fresh working datadir already exists: {target}")
    target.mkdir(parents=True)
    run(["cp", "--archive", "--reflink=auto", f"{source}/.", target], timeout=None)
    run(["sudo", "-n", "chown", "-R", "999:999", target])
    run(["sudo", "-n", "chmod", "-R", "u+rwX", target])


def _start_container(
    machine: Mapping[str, Any], fixed: Mapping[str, Any], workload: str,
    profile: str, datadir: Path,
) -> tuple[str, int, CgroupV1Adapter]:
    engine = machine["engines"]["mysql"]
    container_key = "hybench" if workload.startswith("hybench") else workload
    container = engine["containers"][container_key]["name"]
    port = int(engine["containers"][container_key]["port"])
    _remove_container(container)
    argv = _docker_run_args(
        machine, fixed, name=container, datadir=datadir, port=port,
        profile=profile, initialize_empty=False,
    )
    image_index = argv.index(machine["images"]["mysql"]["reference"])
    argv[image_index:image_index] = [
        "--label", f"ra.formal_campaign={GLOBAL_CAMPAIGN_ID}",
        "--label", f"ra.formal_engine={ENGINE}",
        "--label", f"ra.formal_workload={workload}",
    ]
    run(argv)
    _wait_mysql(port, empty_password=False, timeout=600)
    device = _resolve_mount_device(Path(engine["mount_root"]))
    adapter = CgroupV1Adapter(container, device, machine["resource_profiles"][profile])
    return container, port, adapter


def _canonical_sha(rows: Any) -> str:
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_restored_template(port: int, template_manifest: Mapping[str, Any]) -> dict[str, Any]:
    database = template_manifest["database"]
    row_counts = {}
    for table, expected in template_manifest["row_count_by_table"].items():
        actual = int(_mysql(port, f"SELECT COUNT(*) FROM `{table}`;", database=database).strip())
        if actual != expected:
            raise GateError(f"restored row count mismatch {database}.{table}: expected={expected} actual={actual}")
        row_counts[table] = actual
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
    schema_sha = _canonical_sha(columns)
    index_sha = _canonical_sha(indexes)
    if schema_sha != template_manifest["schema_sha256"] or index_sha != template_manifest["index_sha256"]:
        raise GateError("restored schema/index SHA mismatch")
    return {
        "status": "passed", "database": database, "row_count_by_table": row_counts,
        "schema_sha256": schema_sha, "index_sha256": index_sha,
        "template_id": template_manifest["template_id"],
        "template_snapshot_sha256": template_manifest["template_snapshot_sha256"],
    }


def validate_fixed_settings(port: int, fixed: Mapping[str, Any], profile: str) -> dict[str, Any]:
    values = _fixed_setting_readback(port)
    expected_common = fixed["mysql"]["all_profiles"]
    expected_profile = fixed["mysql"][profile]
    expected = {
        "innodb_flush_method": expected_common["innodb_flush_method"],
        "innodb_redo_log_capacity": str(_parse_size(expected_common["innodb_redo_log_capacity"])),
        "innodb_flush_log_at_trx_commit": str(expected_common["innodb_flush_log_at_trx_commit"]),
        "innodb_doublewrite": expected_common["innodb_doublewrite"],
        "innodb_adaptive_hash_index": expected_common["innodb_adaptive_hash_index"],
        "transaction_isolation": expected_common["transaction_isolation"],
        "performance_schema": expected_common["performance_schema"],
        "innodb_buffer_pool_size": str(_parse_size(expected_profile["innodb_buffer_pool_size"])),
        "innodb_buffer_pool_instances": str(expected_profile["innodb_buffer_pool_instances"]),
        "innodb_buffer_pool_chunk_size": str(_parse_size(expected_profile["innodb_buffer_pool_chunk_size"])),
        "tmp_table_size": str(_parse_size(expected_profile["tmp_table_size"])),
        "max_heap_table_size": str(_parse_size(expected_profile["max_heap_table_size"])),
        "max_connections": str(expected_profile["max_connections"]),
        "log_bin": "0",
        "query_execution_dop": "1",
    }
    mismatches = {key: {"expected": wanted, "actual": values.get(key)} for key, wanted in expected.items() if values.get(key) != wanted}
    if mismatches:
        raise GateError(f"fixed MySQL setting mismatch: {mismatches}")
    return {"status": "passed", "profile": profile, "expected": expected, "readback": values}


COST_NAMES = (
    "io_block_read_cost", "memory_block_read_cost", "row_evaluate_cost", "key_compare_cost",
    "disk_temptable_create_cost", "disk_temptable_row_cost",
    "memory_temptable_create_cost", "memory_temptable_row_cost",
)


def _cost_readback(port: int) -> dict[str, float]:
    rows = _tabular(
        port,
        "SELECT cost_name,COALESCE(cost_value,default_value) FROM mysql.server_cost "
        "WHERE cost_name IN ('row_evaluate_cost','key_compare_cost','disk_temptable_create_cost','disk_temptable_row_cost','memory_temptable_create_cost','memory_temptable_row_cost') "
        "UNION ALL SELECT cost_name,COALESCE(cost_value,default_value) FROM mysql.engine_cost "
        "WHERE engine_name='default' AND device_type=0 AND cost_name IN ('io_block_read_cost','memory_block_read_cost') "
        "ORDER BY cost_name;",
    )
    values = {row[0]: float(row[1]) for row in rows}
    if set(values) != set(COST_NAMES) or len(rows) != 8:
        raise GateError(f"MySQL cost readback is not exact eight values: {values}")
    return {name: values[name] for name in COST_NAMES}


def apply_costs(port: int, values: Mapping[str, float]) -> dict[str, Any]:
    if set(values) != set(COST_NAMES):
        raise GateError("cost parameter set is not exact frozen eight")
    active = int(_mysql(
        port,
        "SELECT COUNT(*) FROM information_schema.PROCESSLIST "
        "WHERE ID<>CONNECTION_ID() AND USER<>'event_scheduler';",
    ).strip())
    if active != 0:
        raise GateError(f"workload connections were not drained before cost apply: {active}")
    literal = {name: format(float(values[name]), ".17g") for name in COST_NAMES}
    server_names = COST_NAMES[2:]
    engine_names = COST_NAMES[:2]
    server_case = " ".join(f"WHEN '{name}' THEN {literal[name]}" for name in server_names)
    engine_case = " ".join(f"WHEN '{name}' THEN {literal[name]}" for name in engine_names)
    sql = (
        "START TRANSACTION; "
        f"UPDATE mysql.server_cost SET cost_value=CASE cost_name {server_case} END WHERE cost_name IN ({','.join(repr(name) for name in server_names)}); "
        f"UPDATE mysql.engine_cost SET cost_value=CASE cost_name {engine_case} END WHERE engine_name='default' AND device_type=0 AND cost_name IN ({','.join(repr(name) for name in engine_names)}); "
        "COMMIT; FLUSH OPTIMIZER_COSTS;"
    )
    _mysql(port, sql)
    readback = _cost_readback(port)  # a fresh client connection after FLUSH
    mismatches = {
        name: {"requested": float(values[name]), "actual": readback[name]}
        for name in COST_NAMES
        if not math.isclose(readback[name], float(values[name]), rel_tol=1e-12, abs_tol=1e-12)
    }
    if mismatches:
        raise GateError(f"cost readback mismatch: {mismatches}")
    return {
        "status": "passed", "connections_drained_before_apply": True,
        "transaction_committed": True, "flush_optimizer_costs": True,
        "fresh_connection_readback": True, "values": readback,
    }


def gate_points(search_spaces: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    mysql = search_spaces["mysql"]
    result = {"P0": {name: float(mysql["default"][name]) for name in COST_NAMES}}
    for index in range(11):
        t = index / 10.0
        point = {}
        for name in COST_NAMES:
            spec = mysql["parameters"][name]
            lower = float(spec["min"])
            upper = float(spec["max"])
            if spec["transform"] == "log":
                value = math.exp(math.log(lower) + t * (math.log(upper) - math.log(lower)))
            elif spec["transform"] == "linear":
                value = lower + t * (upper - lower)
            else:
                raise GateError(f"unsupported search transform for {name}")
            point[name] = value
        result[f"G{index}"] = point
    if list(result) != ["P0", *[f"G{index}" for index in range(11)]]:
        raise GateError("gate point generation order mismatch")
    return result


def _structural_projection(value: Any) -> Any:
    structural_keys = {
        "select_id", "table_name", "access_type", "key", "used_key_parts",
        "nested_loop", "grouping_operation", "ordering_operation", "duplicates_removal",
        "buffer_result", "materialized_from_subquery", "union_result", "query_specifications",
        "dependent", "cacheable", "using_temporary_table", "using_filesort",
        "first_match", "using_join_buffer", "message",
    }
    if isinstance(value, list):
        return [_structural_projection(item) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in structural_keys:
            result[key] = _structural_projection(item)
        elif isinstance(item, (dict, list)):
            projected = _structural_projection(item)
            if projected not in ({}, []):
                result[key] = projected
    return result


def capture_plan(port: int, database: str, sql_path: Path, output: Path, *, parameter_int: int | None = None) -> dict[str, Any]:
    sql = sql_path.read_text(encoding="utf-8").strip().rstrip(";")
    if parameter_int is not None:
        if sql.count("?") != 1:
            raise GateError("parameterized plan SQL must contain exactly one placeholder")
        sql = sql.replace("?", str(parameter_int))
    elif "?" in sql:
        raise GateError("plan SQL has an unresolved placeholder")
    raw = _mysql(port, "EXPLAIN FORMAT=JSON " + sql, database=database, timeout=600).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GateError(f"MySQL EXPLAIN did not return JSON: {raw[-1000:]}") from exc
    structural = _structural_projection(parsed)
    fingerprint = _canonical_sha(structural)
    payload = {
        "schema_version": 1, "freeze_id": FREEZE_ID, "engine": ENGINE,
        "database": database, "sql_path": str(sql_path), "sql_sha256": sha256_file(sql_path),
        "parameter_int": parameter_int, "capture": "EXPLAIN FORMAT=JSON",
        "raw_plan": parsed, "structural_projection": structural,
        "structural_fingerprint_sha256": fingerprint,
    }
    atomic_json(output, payload)
    return payload


def _private_properties(path: Path, values: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        data = "".join(f"{key}={str(value).replace(chr(10), '')}\n" for key, value in values.items()).encode("utf-8")
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sql_workload_config(
    config: Path, benchmark_config: Mapping[str, Any], sql_path: Path, event_path: Path,
    *, duration: int = 0, concurrency: int = 1, parameter_int: int | None = None,
) -> None:
    values: dict[str, Any] = {
        "url": benchmark_config["jdbc_url"], "user": "root", "password": _password(),
        "sql_file": sql_path, "event_path": event_path,
        "duration_seconds": duration, "concurrency": concurrency,
    }
    if parameter_int is not None:
        values["parameter_int"] = parameter_int
    _private_properties(config, values)


def run_correctness(
    workload: str, benchmark_config: Mapping[str, Any], output: Path,
) -> dict[str, Any]:
    config = output / "private" / "correctness.properties"
    sql_path = Path(benchmark_config["target_sql"])
    parameter = 314 if workload == "tpch" else None
    _sql_workload_config(
        config, benchmark_config, sql_path, output / "correctness_events.jsonl",
        parameter_int=parameter,
    )
    command = [
        "java", "-cp", os.pathsep.join((str(FORMAL_CLASSES), str(MYSQL_CONNECTOR))),
        "com.formal.FormalSQLWorkload", "correctness", config,
    ]
    started = time.monotonic()
    try:
        proc = run(command, timeout=600)
    finally:
        config.unlink(missing_ok=True)
    matches = re.findall(r"FORMAL_SQL_CORRECTNESS_JSON=(\{[^\n]+\})", proc.stdout)
    if len(matches) != 1:
        raise GateError(f"correctness client did not emit one result: {(proc.stderr + proc.stdout)[-2000:]}")
    payload = json.loads(matches[0])
    payload.update({
        "status": "passed", "workload": workload,
        "completion_seconds": time.monotonic() - started,
        "sql_path": str(sql_path), "sql_sha256": sha256_file(sql_path),
        "parameter_int": parameter,
    })
    if payload["completion_seconds"] > 600:
        raise GateError(f"single correctness query exceeded 600 seconds: {payload['completion_seconds']}")
    atomic_json(output / "correctness.json", payload)
    return payload


def _hybench_runtime(
    output: Path, benchmark_config: Mapping[str, Any], workload: str, duration: int,
) -> tuple[Path, Path, Path]:
    benchmark_matrix = load_json(SHARED_FROZEN_ROOT / "benchmark_matrix.json")
    workload_config = benchmark_matrix["workloads"][workload]
    query_ids = [int(value) for value in workload_config["ap_query_ids"]]
    query_weights = [int(value) for value in workload_config["ap_query_weights"]]
    if (
        not query_ids
        or len(query_ids) != len(query_weights)
        or len(set(query_ids)) != len(query_ids)
        or any(query_id < 1 or query_id > 13 for query_id in query_ids)
        or any(weight <= 0 for weight in query_weights)
        or sum(query_weights) != 100
    ):
        raise GateError(f"invalid frozen HyBench AP mix for {workload}")
    runtime = output / "hybench_runtime"
    (runtime / "conf").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HYBENCH_ROOT / "conf/log4j2.properties", runtime / "conf/log4j2.properties")
    data_link = runtime / "Data_10x"
    if not data_link.exists():
        data_link.symlink_to(HYBENCH_DATA, target_is_directory=True)
    config = output / "private" / f"hybench_{workload}_{duration}.props"
    config.parent.mkdir(parents=True, exist_ok=True)
    is_xp = workload == "hybench_xp"
    values = {
        "db": "mysql", "classname": "com.mysql.cj.jdbc.Driver",
        "username": "root", "password": _password(), "url": benchmark_config["jdbc_url"],
        "url_ap": benchmark_config["jdbc_url"], "classname_ap": "com.mysql.cj.jdbc.Driver",
        "username_ap": "root", "password_ap": _password(),
        "sf": "10x",
        "at1_percent": int(workload_config.get("at_percent", {}).get("AT1", 40)),
        "at2_percent": int(workload_config.get("at_percent", {}).get("AT2", 30)),
        "at3_percent": int(workload_config.get("at_percent", {}).get("AT3", 15)),
        "at4_percent": int(workload_config.get("at_percent", {}).get("AT4", 5)),
        "at5_percent": int(workload_config.get("at_percent", {}).get("AT5", 7)),
        "at6_percent": int(workload_config.get("at_percent", {}).get("AT6", 3)),
        "apclient": 4 if not is_xp else 0, "tpclient": 0,
        "xapclient": 8 if is_xp else 0, "xtpclient": 8 if is_xp else 0,
        "risk_rate": 0.1, "fresh_interval": 20, "contention_num": 10000,
        "apRunSecs": duration, "tpRunSecs": duration, "xpRunSecs": duration,
        "progress_windows": duration, "random_seed": 4508333,
        "formal_warmup_seconds": 60,
        "ap_query_ids": ",".join(str(value) for value in query_ids),
        "ap_query_weights": ",".join(str(value) for value in query_weights),
        "dist": "zipfian", "power_alpha": 0.1, "apround": 1,
    }
    _private_properties(config, values)
    statement_source = HYBENCH_ROOT / "conf/stmt_mysql.toml"
    normalized_text = _normalize_hybench_table_identifier_case(
        statement_source.read_text(encoding="utf-8")
    )
    expected = benchmark_config["runner_adaptations"]["hybench_identifier_case_normalization"]
    if expected.get("source_sha256") != sha256_file(statement_source):
        raise GateError("HyBench statement source changed after benchmark config freeze")
    normalized_sha = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    if expected.get("normalized_sha256") != normalized_sha:
        raise GateError("HyBench normalized statement SHA differs from benchmark config")
    normalized_path = runtime / "conf" / "stmt_mysql_formal_case_normalized.toml"
    _write_immutable(normalized_path, normalized_text.encode("utf-8"))
    return runtime, config, normalized_path


def _hybench_command(output: Path, benchmark_config: Mapping[str, Any], workload: str, duration: int) -> tuple[list[str | Path], Path]:
    runtime, config, normalized_statements = _hybench_runtime(
        output, benchmark_config, workload, duration,
    )
    mode = "xp" if workload == "hybench_xp" else "ap"
    classpath = os.pathsep.join((str(FORMAL_CLASSES), str(MYSQL_CONNECTOR), str(HYBENCH_JAR)))
    return [
        "java", "-cp", classpath, "com.hybench.HyBenchFormalMain",
        mode, config, normalized_statements,
    ], runtime


def _benchbase_config(
    target: Path, benchmark_config: Mapping[str, Any], duration: int,
) -> None:
    benchmark_matrix = load_json(SHARED_FROZEN_ROOT / "benchmark_matrix.json")
    workload_config = benchmark_matrix["workloads"]["tpch"]
    query_ids = [int(value) for value in workload_config["query_ids"]]
    query_weights = [int(value) for value in workload_config["query_weights"]]
    if (
        len(query_ids) != 22
        or query_ids != list(range(1, 23))
        or len(query_weights) != 22
        or any(weight <= 0 for weight in query_weights)
        or sum(query_weights) != 100
    ):
        raise GateError("invalid frozen TPC-H Q1-Q22 mix")
    source = BENCHBASE_ROOT / "config/mysql/sample_tpch_config.xml"
    tree = ET.parse(source)
    root = tree.getroot()
    terminals = int(
        workload_config.get("engine_terminals", {}).get(
            "mysql", workload_config.get("terminals", 2)
        )
    )
    if terminals <= 0:
        raise GateError(f"invalid frozen MySQL TPC-H terminal count: {terminals}")
    values = {
        "url": benchmark_config["jdbc_url"] + "&rewriteBatchedStatements=true&allowLoadLocalInfile=true",
        "username": "root", "password": _password(), "isolation": "TRANSACTION_READ_COMMITTED",
        "scalefactor": "10", "terminals": str(terminals), "loaderThreads": "8", "randomSeed": "4508333",
    }
    for tag, value in values.items():
        node = root.find(tag)
        if node is None:
            node = ET.Element(tag)
            root.insert(0, node)
        node.text = value
    works = root.find("works")
    if works is None:
        raise GateError("BenchBase TPC-H config has no works")
    work_nodes = works.findall("work")
    for extra in work_nodes[1:]:
        works.remove(extra)
    work = work_nodes[0]
    work.find("serial").text = "false"  # type: ignore[union-attr]
    work.find("rate").text = "unlimited"  # type: ignore[union-attr]
    work.find("weights").text = ",".join(str(value) for value in query_weights)  # type: ignore[union-attr]
    time_node = work.find("time")
    if time_node is None:
        time_node = ET.Element("time")
        work.insert(2, time_node)
    time_node.text = str(duration)
    target.parent.mkdir(parents=True, exist_ok=True)
    tree.write(target, encoding="utf-8", xml_declaration=True)
    os.chmod(target, 0o600)


def _benchbase_command(output: Path, benchmark_config: Mapping[str, Any], duration: int) -> tuple[list[str | Path], Path]:
    config = output / "private" / f"tpch_{duration}.xml"
    _benchbase_config(config, benchmark_config, duration)
    result_root = output / f"benchbase_{duration}"
    result_root.mkdir(parents=True, exist_ok=True)
    return [
        "java", f"-javaagent:{TPCH_AGENT}", "-jar", BENCHBASE_JAR,
        "-b", "tpch", "-c", config,
        "--create=false", "--load=false", "--execute=true", "-d", result_root,
    ], BENCHBASE_ROOT


def _job_command(output: Path, benchmark_config: Mapping[str, Any], duration: int) -> tuple[list[str | Path], Path]:
    config = output / "private" / f"job_{duration}.properties"
    events = output / f"job_{duration}_events.jsonl"
    _sql_workload_config(config, benchmark_config, Path(benchmark_config["target_sql"]), events, duration=duration, concurrency=1)
    return [
        "java", "-cp", os.pathsep.join((str(FORMAL_CLASSES), str(MYSQL_CONNECTOR))),
        "com.formal.FormalSQLWorkload", "measure", config,
    ], Path(__file__).resolve().parent


DB_STATUS_NAMES = (
    "Threads_running", "Threads_connected", "Questions", "Com_select", "Com_commit",
    "Com_rollback", "Created_tmp_disk_tables", "Created_tmp_tables",
    "Innodb_buffer_pool_reads", "Innodb_buffer_pool_read_requests",
    "Innodb_data_reads", "Innodb_data_writes", "Aborted_clients",
)


def _database_sample(port: int, *, elapsed_s: float, phase: str) -> dict[str, Any]:
    names = ",".join(repr(name) for name in DB_STATUS_NAMES)
    rows = _tabular(
        port,
        f"SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME IN ({names}) ORDER BY VARIABLE_NAME;",
    )
    canonical = {name.lower(): name for name in DB_STATUS_NAMES}
    status = {canonical.get(row[0].lower(), row[0]): int(row[1]) for row in rows}
    missing = set(DB_STATUS_NAMES) - set(status)
    if missing:
        raise GateError(f"database status sample missing counters: {sorted(missing)}")
    return {
        "epoch_ms": time.time_ns() // 1_000_000, "elapsed_s": elapsed_s, "phase": phase,
        "status_counters": status,
    }


def _process_group_stop(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=15)


def _redact_benchbase_configs(root: Path) -> list[str]:
    password = _password()
    redacted = []
    for path in root.rglob("*.config.xml"):
        text = path.read_text(encoding="utf-8")
        if password in text:
            path.write_text(text.replace(password, "[REDACTED_EPHEMERAL_CREDENTIAL]"), encoding="utf-8")
            redacted.append(str(path))
    return redacted


def run_with_sampling(
    command: Sequence[str | Path], cwd: Path, adapter: CgroupV1Adapter, port: int,
    output: Path, *, warmup_seconds: int = 60, measurement_seconds: int = 180,
) -> dict[str, Any]:
    total_seconds = warmup_seconds + measurement_seconds
    stdout_path = output / "workload.stdout.log"
    stderr_path = output / "workload.stderr.log"
    resource_path = output / "resource_samples.jsonl"
    database_path = output / "database_samples.jsonl"
    previous = adapter.snapshot()
    started_ns = time.monotonic_ns()
    started_epoch_ms = time.time_ns() // 1_000_000
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        proc = subprocess.Popen(
            [str(item) for item in command], cwd=str(cwd), stdout=stdout, stderr=stderr,
            text=True, start_new_session=True,
        )
        samples: list[dict[str, Any]] = []
        database_samples: list[dict[str, Any]] = []
        early_exit_tick: int | None = None
        try:
            for tick in range(1, total_seconds + 1):
                target_ns = started_ns + tick * 1_000_000_000
                remaining = (target_ns - time.monotonic_ns()) / 1e9
                if remaining > 0:
                    time.sleep(remaining)
                if proc.poll() is not None and tick < total_seconds:
                    early_exit_tick = tick
                current = adapter.snapshot()
                elapsed = (current.monotonic_ns - started_ns) / 1e9
                phase = "warmup" if tick <= warmup_seconds else "measurement"
                sample = adapter.derive(previous, current, phase=phase, elapsed_s=elapsed)
                append_jsonl(resource_path, sample)
                samples.append(sample)
                db_sample = _database_sample(port, elapsed_s=elapsed, phase=phase)
                append_jsonl(database_path, db_sample)
                database_samples.append(db_sample)
                previous = current
            try:
                returncode = proc.wait(timeout=610)
                tail_timed_out = False
            except subprocess.TimeoutExpired:
                tail_timed_out = True
                _process_group_stop(proc)
                returncode = proc.returncode
        finally:
            _process_group_stop(proc)
    inspect_after = json.loads(run(["docker", "inspect", adapter.container]).stdout)[0]
    state = inspect_after["State"]
    if state["Status"] != "running" or int(state["Pid"]) != adapter.pid:
        raise GateError(f"database container changed state/PID during workload: {state}")
    measurement = [sample for sample in samples if sample["phase"] == "measurement"]
    measurement_db = [sample for sample in database_samples if sample["phase"] == "measurement"]
    coverage = len(measurement) / measurement_seconds
    if early_exit_tick is not None or returncode != 0:
        detail = (stderr_path.read_text(encoding="utf-8") + stdout_path.read_text(encoding="utf-8"))[-4000:]
        raise GateError(f"workload exited early or failed: tick={early_exit_tick} rc={returncode}: {detail}")
    if coverage < 0.9:
        raise GateError(f"measurement sample coverage below 0.9: {coverage}")
    return {
        "started_epoch_ms": started_epoch_ms,
        "warmup_seconds": warmup_seconds, "measurement_seconds": measurement_seconds,
        "resource_samples": measurement, "database_samples": measurement_db,
        "resource_sample_path": str(resource_path), "database_sample_path": str(database_path),
        "sample_coverage": coverage, "returncode": returncode,
        "tail_timed_out": tail_timed_out, "container_state_after": state,
        "container_restart_count_after": int(inspect_after["RestartCount"]),
        "stdout_path": str(stdout_path), "stderr_path": str(stderr_path),
    }


def _cumulative_series(
    sample_epochs: Sequence[int], event_epochs_and_counts: Sequence[tuple[int, float]], baseline_epoch: int,
) -> list[float]:
    ordered = sorted(event_epochs_and_counts)
    result = []
    position = 0
    cumulative = 0.0
    for epoch in sample_epochs:
        while position < len(ordered) and ordered[position][0] <= epoch:
            cumulative = ordered[position][1]
            position += 1
        elapsed = max((epoch - baseline_epoch) / 1000.0, 1.0)
        result.append(cumulative / elapsed)
    return result


def _hybench_metrics(output: Path, sampling: Mapping[str, Any], workload: str) -> dict[str, Any]:
    text = Path(sampling["stdout_path"]).read_text(encoding="utf-8")
    samples = [json.loads(item) for item in re.findall(r"HYBENCH_FORMAL_SAMPLE_JSON=(\{[^\n]+\})", text)]
    resets = [json.loads(item) for item in re.findall(r"HYBENCH_FORMAL_WARMUP_RESET=(\{[^\n]+\})", text)]
    finals = [json.loads(item) for item in re.findall(r"HYBENCH_FORMAL_METRICS_JSON=(\{[^\n]+\})", text)]
    if len(resets) != 1 or len(finals) != 1:
        raise GateError(f"HyBench formal markers are incomplete: reset={len(resets)} final={len(finals)}")
    baseline_epoch = int(resets[0]["epoch_ms"])
    measured = [item for item in samples if item.get("measurement_phase") is True and int(item["epoch_ms"]) >= baseline_epoch]
    if len(measured) < 0.9 * int(sampling["measurement_seconds"]):
        raise GateError(f"HyBench progress sample coverage is insufficient: {len(measured)}")
    resource_epochs = [int(item["epoch_ms"]) for item in sampling["resource_samples"]]
    ap_events = [(int(item["epoch_ms"]), float(item["xap_or_ap_total"])) for item in measured]
    tp_events = [(int(item["epoch_ms"]), float(item["xtp_total"])) for item in measured]
    ap_series = _cumulative_series(resource_epochs, ap_events, baseline_epoch)
    tp_series = _cumulative_series(resource_epochs, tp_events, baseline_epoch)
    last = measured[-1]
    elapsed = max((int(last["epoch_ms"]) - baseline_epoch) / 1000.0, 1.0)
    ap_qps = float(last["xap_or_ap_total"]) / elapsed
    xtp_tps = float(last["xtp_total"]) / elapsed
    final = finals[0]
    result = {
        "qps": ap_qps if workload == "hybench_ap" else None,
        "xap_qps": ap_qps if workload == "hybench_xp" else None,
        "xtp_tps": xtp_tps if workload == "hybench_xp" else None,
        "latency_p95_ms": float(final["latency_p95_ms"]),
        "latency_p99_ms": float(final["latency_p99_ms"]),
        "query_count": float(last["xap_or_ap_total"]),
        "transaction_count": float(last["xtp_total"]),
        "throughput_series": ap_series,
        "xtp_throughput_series": tp_series,
        "at_commit_counts": final["at_commit_counts"],
        "progress_sample_count": len(measured),
    }
    return result


def _job_metrics(output: Path, sampling: Mapping[str, Any]) -> dict[str, Any]:
    text = Path(sampling["stdout_path"]).read_text(encoding="utf-8")
    matches = re.findall(r"FORMAL_SQL_METRICS_JSON=(\{[^\n]+\})", text)
    if len(matches) != 1:
        raise GateError("JOB client did not emit exactly one metric object")
    final = json.loads(matches[0])
    events_path = output / f"job_{sampling['warmup_seconds'] + sampling['measurement_seconds']}_events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]
    boundary = int(sampling["started_epoch_ms"]) + int(sampling["warmup_seconds"]) * 1000
    deadline = boundary + int(sampling["measurement_seconds"]) * 1000
    completions = sorted(
        int(item["epoch_ms"]) for item in events
        if item["status"] == "complete" and int(item["epoch_ms"]) >= boundary
        and int(item["epoch_ms"]) <= deadline and item.get("counted_before_deadline")
    )
    epochs = [int(item["epoch_ms"]) for item in sampling["resource_samples"]]
    cumulative_events = []
    for index, epoch in enumerate(completions, 1):
        cumulative_events.append((epoch, float(index)))
    series = _cumulative_series(epochs, cumulative_events, boundary)
    elapsed = int(sampling["measurement_seconds"])
    measured_latencies = [
        float(item["latency_ms"]) for item in events
        if item["status"] == "complete" and boundary <= int(item["epoch_ms"]) <= deadline
        and item.get("counted_before_deadline")
    ]
    failures = [item for item in events if item["status"] != "complete" and int(item["epoch_ms"]) >= boundary]
    return {
        "qps": len(completions) / elapsed, "xap_qps": None, "xtp_tps": None,
        "query_count": len(completions), "transaction_count": None,
        "latency_p95_ms": _percentile(measured_latencies, 0.95),
        "latency_p99_ms": _percentile(measured_latencies, 0.99),
        "failures": len(failures), "inflight_at_end": int(final["inflight_at_end"]),
        "throughput_series": series, "xtp_throughput_series": None,
    }


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _tpch_metrics(output: Path, sampling: Mapping[str, Any]) -> dict[str, Any]:
    if "FORMAL_TPCH_SEED_AGENT=4508333" not in Path(sampling["stderr_path"]).read_text(encoding="utf-8"):
        raise GateError("TPC-H frozen seed agent did not report activation")
    raw_paths = list(output.rglob("*.raw.csv"))
    summary_paths = list(output.rglob("*.summary.json"))
    if len(raw_paths) != 1 or len(summary_paths) != 1:
        raise GateError(f"BenchBase output cardinality mismatch: raw={raw_paths} summary={summary_paths}")
    boundary_us = (int(sampling["started_epoch_ms"]) + int(sampling["warmup_seconds"]) * 1000) * 1000
    deadline_us = boundary_us + int(sampling["measurement_seconds"]) * 1_000_000
    completions: list[tuple[int, float]] = []
    with raw_paths[0].open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            start_us = float(row["Start Time (microseconds)"]) * 1_000_000 if float(row["Start Time (microseconds)"]) < 1e13 else float(row["Start Time (microseconds)"])
            latency_us = float(row["Latency (microseconds)"])
            completed_us = int(start_us + latency_us)
            if boundary_us <= completed_us <= deadline_us:
                completions.append((completed_us // 1000, latency_us / 1000.0))
    completions.sort()
    epochs = [int(item["epoch_ms"]) for item in sampling["resource_samples"]]
    cumulative = [(epoch, float(index)) for index, (epoch, _) in enumerate(completions, 1)]
    series = _cumulative_series(epochs, cumulative, boundary_us // 1000)
    duration = int(sampling["measurement_seconds"])
    latencies = [latency for _, latency in completions]
    summary = json.loads(summary_paths[0].read_text(encoding="utf-8"))
    return {
        "qps": len(completions) / duration, "xap_qps": None, "xtp_tps": None,
        "query_count": len(completions), "transaction_count": None,
        "latency_p95_ms": _percentile(latencies, 0.95),
        "latency_p99_ms": _percentile(latencies, 0.99),
        "failures": 0, "inflight_at_end": None,
        "throughput_series": series, "xtp_throughput_series": None,
        "benchbase_summary": summary,
    }


CASE_DEFINITIONS = {
    "mysql_hybench_ap_memory": ("hybench_ap", "memory", "hybench"),
    "mysql_job_memory": ("job", "memory", "job"),
    "mysql_tpch_memory": ("tpch", "memory", "tpch"),
    "mysql_hybench_xp_io": ("hybench_xp", "io", "hybench"),
    "mysql_hybench_ap_none": ("hybench_ap", "none", "hybench"),
    "mysql_job_none": ("job", "none", "job"),
    "mysql_tpch_none": ("tpch", "none", "tpch"),
    "mysql_hybench_xp_none": ("hybench_xp", "none", "hybench"),
}


def _mean_resources(samples: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    if not samples:
        raise GateError("no measurement resource samples")
    return {
        "cpu": statistics.fmean(float(item["cpu_utilization_ratio"]) for item in samples),
        "memory": statistics.fmean(float(item["memory_utilization_ratio"]) for item in samples),
        "io": statistics.fmean(float(item["io_utilization_ratio"]) for item in samples),
    }


def _decision_window_samples(
    samples: Sequence[Mapping[str, Any]], *, seconds: int = 180,
) -> list[Mapping[str, Any]]:
    """Return the latest rolling decision window; older samples are audit-only."""
    if seconds <= 0:
        raise ValueError("decision window seconds must be positive")
    if len(samples) < seconds:
        raise GateError(
            f"decision window requires {seconds} one-second samples, got {len(samples)}"
        )
    return list(samples[-seconds:])


def _pressure_evidence(
    samples: Sequence[Mapping[str, Any]], database_samples: Sequence[Mapping[str, Any]], scenario: str,
) -> dict[str, Any]:
    first = samples[0]
    last = samples[-1]
    memory_failcnt_delta = int(last["memory_failcnt"]) - int(first["memory_failcnt"])
    pgmajfault_delta = int(last["memory_pgmajfault"]) - int(first["memory_pgmajfault"])
    refault_delta = int(last["memory_workingset_refault"]) - int(first["memory_workingset_refault"])
    io_psi_delta = None
    memory_psi_delta = None
    try:
        io_psi_delta = int(last["io_psi"]["some"]["total"]) - int(first["io_psi"]["some"]["total"])
    except (KeyError, TypeError):
        pass
    try:
        memory_psi_delta = int(last["memory_psi"]["some"]["total"]) - int(first["memory_psi"]["some"]["total"])
    except (KeyError, TypeError):
        pass
    temp_disk_tables_delta = _server_counter_delta(database_samples, "Created_tmp_disk_tables")
    memory_passed = (
        memory_failcnt_delta > 0 or pgmajfault_delta > 0 or refault_delta > 0
        or (memory_psi_delta is not None and memory_psi_delta > 0)
        or temp_disk_tables_delta > 0
    )
    io_passed = io_psi_delta is not None and io_psi_delta > 0
    return {
        "scenario": scenario,
        "memory": {
            "passed": memory_passed, "failcnt_delta": memory_failcnt_delta,
            "pgmajfault_delta": pgmajfault_delta, "workingset_refault_delta": refault_delta,
            "host_memory_psi_some_total_delta": memory_psi_delta,
            "created_tmp_disk_tables_delta": temp_disk_tables_delta,
        },
        "io": {"passed": io_passed, "host_io_psi_some_total_delta": io_psi_delta},
    }


def _server_counter_delta(samples: Sequence[Mapping[str, Any]], name: str) -> int:
    if len(samples) < 2:
        return 0
    first = samples[0]["status_counters"][name]
    last = samples[-1]["status_counters"][name]
    return int(last) - int(first)


def _measurement_stability(
    workload: str, resources: list[Mapping[str, Any]], metrics: Mapping[str, Any],
) -> dict[str, Any]:
    if workload == "hybench_xp":
        resource = stability_summary(resources)
        xap = stability_summary(resources, list(metrics["throughput_series"]), xp=True)
        xtp = stability_summary(resources, list(metrics["xtp_throughput_series"]), xp=True)
        completed = float(metrics.get("query_count") or 0) > 0 and float(metrics.get("transaction_count") or 0) > 0
        return {
            "stable": bool(resource["stable"] and xap["stable"] and xtp["stable"] and completed),
            "resource": resource, "xap": xap["throughput"], "xtp": xtp["throughput"],
            "both_streams_completed_samples": completed,
        }
    normal = stability_summary(resources, list(metrics["throughput_series"]), xp=False)
    completed = float(metrics.get("query_count") or 0) > 0
    return {
        "stable": bool(normal["stable"] and completed),
        "resource_and_throughput": normal,
        "completed_query_samples": completed,
    }


def _baseline_gate(
    scenario: str, resources: Mapping[str, float], pressure: Mapping[str, Any],
    *, stable: bool, correctness: bool, error_rate: float, oom: bool, restarted: bool,
) -> dict[str, Any]:
    failures = []
    advisories = []
    if not stable:
        advisories.append("per_second_cv_unstable_non_blocking")
    if not correctness:
        failures.append("correctness_failed")
    if error_rate > 0.01:
        failures.append("error_rate_above_0.01")
    if oom:
        failures.append("oom")
    if restarted:
        failures.append("database_restarted")
    if scenario == "none":
        for name, value in resources.items():
            if value >= 0.75:
                failures.append(f"none_{name}_utilization_not_below_0.75")
    else:
        # The active MySQL v2 contract is stricter than the legacy shared
        # six-session file (which only carried a non-target <= 0.8 ceiling).
        # R0 calibration is an exact 85--95 / 30--50 gate; candidate hard
        # gates keep the separate <= 0.8 ceiling later in the outer loop.
        target_min = 0.85
        target_max = 0.95
        non_target_min = 0.30
        non_target_max = 0.50
        non_target_bottleneck_threshold = 0.8
        if resources[scenario] < target_min:
            failures.append(f"target_{scenario}_utilization_below_{target_min:g}")
        if resources[scenario] > target_max:
            failures.append(f"target_{scenario}_utilization_above_{target_max:g}")
        for name, value in resources.items():
            if name == scenario:
                continue
            if not non_target_min <= value <= non_target_max:
                failures.append(
                    f"non_target_{name}_utilization_outside_"
                    f"{non_target_min:g}_{non_target_max:g}"
                )
        if scenario == "memory" and not pressure["memory"]["passed"]:
            failures.append("memory_pressure_evidence_absent")
        if scenario == "io" and not pressure["io"]["passed"]:
            failures.append("io_pressure_evidence_absent")
    return {
        "passed": not failures,
        "failures": failures,
        "advisories": advisories,
        "target_required_range": [0.85, 0.95],
        "non_target_required_range": [0.3, 0.5],
        "non_target_bottleneck_threshold": 0.8,
    }


def _attempt_checksums(root: Path) -> Path:
    files = []
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.name != "checksums.sha256" and "private" not in path.parts:
            files.append(path)
    lines = [f"{sha256_file(path)}  {path.relative_to(root).as_posix()}" for path in sorted(files)]
    target = root / "checksums.sha256"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _cleanup_working(working: Path, working_root: Path) -> None:
    resolved = working.resolve(strict=False)
    root = working_root.resolve()
    if root not in resolved.parents:
        raise GateError(f"cleanup target escapes frozen working root: {working}")
    if working.exists():
        run(["sudo", "-n", "rm", "-rf", "--", working])


def run_gate_attempt(
    case_id: str, point_id: str, repeat_id: int,
    machine: Mapping[str, Any], fixed: Mapping[str, Any], benchmark: Mapping[str, Any],
    points: Mapping[str, Mapping[str, float]],
    *, host_admission_evidence: Mapping[str, Any] | None = None,
    host_machine_readback: Mapping[str, Any] | None = None,
    measurement_seconds: int = 180,
) -> dict[str, Any]:
    if case_id not in CASE_DEFINITIONS or point_id not in points:
        raise GateError(f"unknown gate case/point: {case_id}/{point_id}")
    workload, scenario, template_key = CASE_DEFINITIONS[case_id]
    attempt_parent = GATE_ROOT / case_id / point_id / f"repeat_{repeat_id}"
    attempt_number = 1
    while (attempt_parent / f"attempt_{attempt_number:02d}").exists():
        existing = attempt_parent / f"attempt_{attempt_number:02d}" / "attempt_summary.json"
        if existing.is_file():
            payload = json.loads(existing.read_text(encoding="utf-8"))
            if (
                payload.get("status") == "complete"
                and int(payload.get("sampling", {}).get("measurement_seconds", 0)) == measurement_seconds
            ):
                log("gate_attempt_already_complete", case_id=case_id, point_id=point_id, repeat_id=repeat_id)
                return payload
        attempt_number += 1
    output = attempt_parent / f"attempt_{attempt_number:02d}"
    output.mkdir(parents=True)
    atomic_json(output / "attempt_start.json", {
        "schema_version": 1, "freeze_id": FREEZE_ID, "global_campaign_id": GLOBAL_CAMPAIGN_ID,
        "engine": ENGINE, "case_id": case_id, "workload": workload, "scenario": scenario,
        "point_id": point_id, "repeat_id": repeat_id, "attempt_id": attempt_number,
        "started_at_utc": utc_now(), "cost_values": points[point_id],
    })
    engine = machine["engines"]["mysql"]
    template_root = Path(engine["template_root"]) / template_key
    manifest_path = template_root / "database_manifest.json"
    if not manifest_path.is_file():
        raise GateError(f"formal template manifest is absent: {manifest_path}")
    template_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    working_root = Path(engine["working_root"])
    working = working_root / "preflight_gate" / case_id / point_id / f"repeat_{repeat_id}" / f"attempt_{attempt_number:02d}"
    datadir = working / "datadir"
    container = ""
    try:
        _copy_template(template_root / "datadir", datadir, working_root)
        container, port, adapter = _start_container(machine, fixed, workload, scenario, datadir)
        restored = validate_restored_template(port, template_manifest)
        fixed_before = validate_fixed_settings(port, fixed, scenario)
        cost_apply = apply_costs(port, points[point_id])
        fixed_after_cost = validate_fixed_settings(port, fixed, scenario)
        config_path = BENCHMARK_CONFIG_ROOT / workload / "benchmark_config.json"
        benchmark_config = json.loads(config_path.read_text(encoding="utf-8"))
        database = benchmark_config["database"]
        plan = capture_plan(
            port, database, Path(benchmark_config["target_sql"]), output / "plan.json",
            parameter_int=314 if workload == "tpch" else None,
        )
        correctness = run_correctness(workload, benchmark_config, output)
        total_duration = 60 + measurement_seconds
        if workload.startswith("hybench"):
            command, cwd = _hybench_command(output, benchmark_config, workload, total_duration)
        elif workload == "job":
            command, cwd = _job_command(output, benchmark_config, total_duration)
        else:
            command, cwd = _benchbase_command(output, benchmark_config, total_duration)
        sampling = run_with_sampling(
            command, cwd, adapter, port, output,
            warmup_seconds=60, measurement_seconds=measurement_seconds,
        )
        if workload.startswith("hybench"):
            metrics = _hybench_metrics(output, sampling, workload)
        elif workload == "job":
            metrics = _job_metrics(output, sampling)
        else:
            metrics = _tpch_metrics(output, sampling)
        resource_samples = list(sampling.pop("resource_samples"))
        database_samples = list(sampling.pop("database_samples"))
        decision_resource_samples = _decision_window_samples(resource_samples)
        decision_database_samples = _decision_window_samples(database_samples)
        resource_means_full_measurement_audit = _mean_resources(resource_samples)
        resource_means = _mean_resources(decision_resource_samples)
        pressure = _pressure_evidence(
            decision_resource_samples, decision_database_samples, scenario
        )
        stability = _measurement_stability(workload, resource_samples, metrics)
        rollback_delta = _server_counter_delta(database_samples, "Com_rollback")
        aborted_delta = _server_counter_delta(database_samples, "Aborted_clients")
        metric_failures = int(metrics.get("failures") or 0)
        completed = float(metrics.get("query_count") or 0) + float(metrics.get("transaction_count") or 0)
        # HyBench AT1/AT2/AT6 intentionally call connection.rollback() for
        # business-rule rejections (for example insufficient balance or no
        # delinquency record).  Com_rollback therefore is workload activity for
        # XP, not a database/system error.  Keep recording it as provenance,
        # but exclude it from the hard error-rate gate.
        rollback_counted_as_error = workload != "hybench_xp"
        error_rollback_delta = rollback_delta if rollback_counted_as_error else 0
        error_total = error_rollback_delta + aborted_delta + metric_failures
        error_rate = error_total / max(completed + error_total, 1.0)
        state = sampling["container_state_after"]
        oom = bool(state.get("OOMKilled"))
        restarted = int(state["Pid"]) != adapter.pid or int(sampling["container_restart_count_after"]) > 0
        correctness_passed = True
        xp_correctness = None
        if workload == "hybench_xp":
            commits = {key: int(value) for key, value in metrics["at_commit_counts"].items()}
            expected_types = {f"AT{index}" for index in range(1, 7)}
            correctness_passed = (
                set(commits) == expected_types and all(value > 0 for value in commits.values())
                and aborted_delta == 0 and metric_failures == 0
            )
            per_type = {
                name: {
                    "commits": commits.get(name, 0),
                    "rollbacks": None,
                    "timeouts": 0 if correctness_passed else None,
                    "errors": 0 if correctness_passed else None,
                    "null_reason": "server-wide Com_rollback cannot be attributed to an individual AT type",
                }
                for name in sorted(expected_types)
            }
            xp_correctness = {
                "at1_to_at6": per_type, "global_rollback_delta": rollback_delta,
                "expected_business_rollback_delta": rollback_delta,
                "rollback_counted_as_error": False,
                "rollback_semantics": (
                    "HyBench AT1/AT2/AT6 explicitly roll back rejected business operations; "
                    "these are not database/system failures"
                ),
                "global_aborted_client_delta": aborted_delta, "metric_failures": metric_failures,
                "passed": correctness_passed,
            }
        baseline_gate = _baseline_gate(
            scenario, resource_means, pressure, stable=stability["stable"],
            correctness=correctness_passed, error_rate=error_rate, oom=oom, restarted=restarted,
        ) if point_id == "P0" else None
        fixed_after = validate_fixed_settings(port, fixed, scenario)
        final_cost = _cost_readback(port)
        summary = {
            "schema_version": 1, "freeze_id": FREEZE_ID, "global_campaign_id": GLOBAL_CAMPAIGN_ID,
            "engine": ENGINE, "status": "complete", "case_id": case_id, "workload": workload,
            "scenario": scenario, "point_id": point_id, "repeat_id": repeat_id,
            "attempt_id": attempt_number, "completed_at_utc": utc_now(),
            "template_validation": restored, "template_manifest_path": str(manifest_path),
            "template_manifest_sha256": sha256_file(manifest_path),
            "benchmark_config_path": str(config_path), "benchmark_config_sha256": sha256_file(config_path),
            "resource_readback": adapter.readback, "fixed_settings_before": fixed_before,
            "host_admission": dict(host_admission_evidence or {}),
            "host_machine_readback": dict(host_machine_readback or {}),
            "cost_apply": cost_apply, "fixed_settings_after_cost": fixed_after_cost,
            "plan_fingerprint_sha256": plan["structural_fingerprint_sha256"],
            "correctness": correctness, "xp_correctness": xp_correctness,
            "sampling": sampling, "metrics": metrics, "resource_means": resource_means,
            "resource_means_window_seconds": 180,
            "resource_means_full_measurement_audit": resource_means_full_measurement_audit,
            "pressure_evidence": pressure, "stability": stability,
            "error_rate": error_rate, "oom": oom, "database_restarted": restarted,
            "baseline_resource_gate": baseline_gate,
            "fixed_settings_after_measurement": fixed_after, "final_cost_readback": final_cost,
            "measurement_valid": bool(stability["stable"] and correctness_passed and error_rate <= 0.01 and not oom and not restarted),
        }
        private = output / "private"
        if private.exists():
            shutil.rmtree(private)
        redacted = _redact_benchbase_configs(output) if workload == "tpch" else []
        summary["redacted_generated_benchbase_configs"] = redacted
        atomic_json(output / "attempt_summary.json", summary)
        _attempt_checksums(output)
        return summary
    except Exception as exc:
        atomic_json(output / "failure.json", {
            "schema_version": 1, "freeze_id": FREEZE_ID, "global_campaign_id": GLOBAL_CAMPAIGN_ID,
            "engine": ENGINE, "status": "failed", "case_id": case_id, "point_id": point_id,
            "repeat_id": repeat_id, "attempt_id": attempt_number, "failed_at_utc": utc_now(),
            "error_type": type(exc).__name__, "error": str(exc),
        })
        private = output / "private"
        if private.exists():
            shutil.rmtree(private)
        _attempt_checksums(output)
        raise
    finally:
        if container:
            _remove_container(container)
        _cleanup_working(working, working_root)


def _running_formal_containers() -> list[str]:
    proc = run(["docker", "ps", "--format", "{{.Names}}"], check=True)
    return sorted(name for name in proc.stdout.splitlines() if name.startswith("ra_formal_"))


def _locked_gate_attempt(
    case_id: str, point_id: str, repeat_id: int,
    machine: Mapping[str, Any], fixed: Mapping[str, Any], benchmark: Mapping[str, Any],
    points: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    with host_measurement_lock():
        engine = machine["engines"]["mysql"]
        for item in engine["containers"].values():
            _remove_container(item["name"])
        running = _running_formal_containers()
        if running:
            raise GateError(f"other formal database containers are running under exclusive host lock: {running}")
        machine_readback = apply_and_read_machine(machine)
        nonce = f"{case_id}_{point_id}_r{repeat_id}_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{secrets.token_hex(4)}"
        admission = host_admission(machine, PREFLIGHT_ROOT / "host_admission" / f"{nonce}.json")
        summary = run_gate_attempt(
            case_id, point_id, repeat_id, machine, fixed, benchmark, points,
            host_admission_evidence=admission, host_machine_readback=machine_readback,
        )
        if not bool(summary["stability"]["stable"]):
            raise GateError(
                "gate measurement was unstable at 180 seconds, but run_gate_attempt "
                "cannot extend the same live workload in 30-second steps. Refusing "
                "the former invalid behavior of starting a new 600-second replay; "
                "use ContinuousMeasurementSession and _run_continuous_measurement"
            )
        return summary


def _gate_execution_spec() -> list[dict[str, Any]]:
    points = ["P0", *[f"G{index}" for index in range(11)]]
    repeats = {point: (3 if point in {"P0", "G0", "G10"} else 1) for point in points}
    result = []
    for case_id, (_, scenario, _) in CASE_DEFINITIONS.items():
        selected = points if scenario != "none" else ["P0"]
        for point in selected:
            for repeat in range(1, repeats[point] + 1):
                result.append({"case_id": case_id, "point_id": point, "repeat_id": repeat})
    return result


def _primary_throughput(summary: Mapping[str, Any]) -> float:
    metrics = summary["metrics"]
    if summary["workload"] == "hybench_xp":
        xap = float(metrics["xap_qps"])
        xtp = float(metrics["xtp_tps"])
        return math.sqrt(max(xap, 0.0) * max(xtp, 0.0))
    return float(metrics["qps"])


def _target_unit_resource(summary: Mapping[str, Any], target: str) -> float | None:
    throughput = _primary_throughput(summary)
    if throughput <= 0:
        return None
    return float(summary["resource_means"][target]) / throughput


def _mean_defined(values: Sequence[float | None]) -> float | None:
    defined = [float(value) for value in values if value is not None]
    return statistics.fmean(defined) if defined and len(defined) == len(values) else None


def _resource_effect(
    attempts: Sequence[Mapping[str, Any]], target: str,
) -> dict[str, Any]:
    by_point: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        by_point[str(attempt["point_id"])].append(attempt)
    p0 = sorted(by_point["P0"], key=lambda item: int(item["repeat_id"]))
    if len(p0) != 3:
        raise GateError("resource-effect aggregation requires exactly three P0 repeats")
    evidence = []
    for endpoint in ("G0", "G10"):
        values = sorted(by_point[endpoint], key=lambda item: int(item["repeat_id"]))
        if len(values) != 3:
            raise GateError(f"resource-effect aggregation requires exactly three {endpoint} repeats")
        deltas = [
            float(values[index]["resource_means"][target]) - float(p0[index]["resource_means"][target])
            for index in range(3)
        ]
        positive = sum(value > 0 for value in deltas)
        negative = sum(value < 0 for value in deltas)
        direction_passed = max(positive, negative) >= 2
        p0_mean = statistics.fmean(float(item["resource_means"][target]) for item in p0)
        endpoint_mean = statistics.fmean(float(item["resource_means"][target]) for item in values)
        absolute_change = abs(endpoint_mean - p0_mean)
        p0_unit = _mean_defined([_target_unit_resource(item, target) for item in p0])
        endpoint_unit = _mean_defined([_target_unit_resource(item, target) for item in values])
        unit_change = (
            abs(endpoint_unit / p0_unit - 1.0)
            if p0_unit is not None and endpoint_unit is not None and p0_unit > 0 else None
        )
        magnitude_passed = absolute_change >= 0.03 or (unit_change is not None and unit_change >= 0.05)
        evidence.append({
            "endpoint": endpoint, "paired_target_utilization_deltas": deltas,
            "positive_direction_repeats": positive, "negative_direction_repeats": negative,
            "repeat_direction_passed": direction_passed,
            "p0_target_utilization_mean": p0_mean,
            "endpoint_target_utilization_mean": endpoint_mean,
            "absolute_target_utilization_change_ratio": absolute_change,
            "p0_normalized_target_capacity_seconds_per_operation": p0_unit,
            "endpoint_normalized_target_capacity_seconds_per_operation": endpoint_unit,
            "unit_query_target_resource_change_ratio": unit_change,
            "magnitude_passed": magnitude_passed,
            "passed": direction_passed and magnitude_passed,
        })
    return {"passed": any(item["passed"] for item in evidence), "endpoint_evidence": evidence}


BOTTLENECK_CASE_BY_WORKLOAD = {
    "hybench_ap": "mysql_hybench_ap_memory",
    "job": "mysql_job_memory",
    "tpch": "mysql_tpch_memory",
    "hybench_xp": "mysql_hybench_xp_io",
}


def _aggregate_gate_case(
    case_id: str, attempts_by_case: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    workload, scenario, _ = CASE_DEFINITIONS[case_id]
    own = list(attempts_by_case[case_id])
    sensitivity_case = BOTTLENECK_CASE_BY_WORKLOAD[workload]
    sensitivity = list(attempts_by_case[sensitivity_case])
    expected_own = 3 if scenario == "none" else 18
    if len(own) != expected_own or len(sensitivity) != 18:
        raise GateError(
            f"gate attempt cardinality mismatch for {case_id}: own={len(own)} sensitivity={len(sensitivity)}"
        )
    fingerprints = sorted({str(item["plan_fingerprint_sha256"]) for item in sensitivity})
    combined = [*own, *sensitivity]
    correctness_signatures = {
        (int(item["correctness"]["row_count"]), str(item["correctness"]["sha256"]))
        for item in combined
    }
    completion_max = max(float(item["correctness"]["completion_seconds"]) for item in combined)
    correctness_passed = len(correctness_signatures) == 1
    stability_passed = all(bool(item["stability"]["stable"]) for item in combined)
    measurements_valid = all(bool(item["measurement_valid"]) for item in combined)
    baseline_attempts = [item for item in own if item["point_id"] == "P0"]
    baseline_passed = (
        len(baseline_attempts) == 3
        and all(item["baseline_resource_gate"] and item["baseline_resource_gate"]["passed"] for item in baseline_attempts)
    )
    target = CASE_DEFINITIONS[sensitivity_case][1]
    effect = _resource_effect(sensitivity, target)
    failures = []
    if len(fingerprints) < 2:
        failures.append("fewer_than_two_structural_plan_fingerprints")
    if not correctness_passed:
        failures.append("correctness_signature_changed_across_points")
    if completion_max > 600:
        failures.append("single_query_completion_exceeded_600_seconds")
    if not stability_passed:
        failures.append("one_or_more_prescribed_replays_unstable")
    if not measurements_valid:
        failures.append("one_or_more_prescribed_replays_invalid")
    if not baseline_passed:
        failures.append("three_repeat_baseline_resource_gate_failed")
    if not effect["passed"]:
        failures.append("resource_effect_or_repeat_direction_gate_failed")
    return {
        "case_id": case_id, "workload": workload, "scenario": scenario,
        "status": "passed" if not failures else "failed", "failures": failures,
        "prescribed_attempt_count": len(own),
        "plan_sensitivity_source_case_id": sensitivity_case,
        "plan_sensitivity_attempt_count": len(sensitivity),
        "distinct_structural_plan_fingerprints": len(fingerprints),
        "structural_plan_fingerprints": fingerprints,
        "correctness_passed": correctness_passed,
        "correctness_signatures": [
            {"row_count": item[0], "sha256": item[1]} for item in sorted(correctness_signatures)
        ],
        "single_query_completion_seconds_max": completion_max,
        "stable_required_passed": stability_passed,
        "measurement_validity_passed": measurements_valid,
        "baseline_resource_gate_passed": baseline_passed,
        "resource_effect": effect,
        "resource_effect_passed": effect["passed"],
    }


def _environment_readback(
    machine: Mapping[str, Any], fixed: Mapping[str, Any], benchmark: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    engine = machine["engines"]["mysql"]
    template_items = {}
    for name in ("hybench", "job", "tpch"):
        path = Path(engine["template_root"]) / name / "database_manifest.json"
        manifest = load_json(path)
        if (
            manifest.get("status") != "complete"
            or manifest.get("all_source_loaded_final_counts_equal") is not True
            or manifest.get("all_load_warning_counts_zero") is not True
        ):
            raise GateError(f"template load/count audit is not passed: {path}")
        if manifest.get("freeze_id") != FREEZE_ID:
            raise GateError(f"template freeze_id mismatch: {path}")
        template_items[name] = {
            "path": str(path),
            "sha256": sha256_file(path),
            "manifest": manifest,
        }
    profile_evidence = {}
    fixed_evidence = {}
    for profile in ("memory", "io", "none"):
        selected = next((item for item in attempts if item["scenario"] == profile and item["point_id"] == "P0"), None)
        if selected is None:
            raise GateError(f"no P0 gate attempt readback for profile {profile}")
        profile_evidence[profile] = selected["resource_readback"]
        fixed_evidence[profile] = selected["fixed_settings_before"]
    latest_machine = attempts[-1]["host_machine_readback"]
    host_snapshots = {
        "lscpu": run(["lscpu"]).stdout,
        "numactl_hardware": run(["numactl", "--hardware"]).stdout,
        "free_bytes": run(["free", "-b"]).stdout,
        "lsblk": run(["lsblk", "--json", "--bytes", "--output-all"]).stdout,
        "findmnt": run(["findmnt", "--json"]).stdout,
        "docker_version": run(["docker", "version", "--format", "{{json .}}"]).stdout.strip(),
    }
    sections = {
        "resource_profiles": {"status": "passed", "profiles": profile_evidence},
        "fixed_database_settings": {"status": "passed", "profiles": fixed_evidence},
        "machine": {
            "status": "passed", "expected": machine["host"], "readback": latest_machine,
            "mysql_mount_device": _resolve_mount_device(Path(engine["mount_root"])),
            "host_snapshots": host_snapshots,
        },
        "images": {"status": "passed", "mysql": _docker_image_readback(machine)},
        "artifacts": {"status": "passed", "readback": _verify_artifacts(benchmark)},
        "templates": {"status": "passed", "templates": template_items},
        "cost_apply_protocol": {
            "status": "passed", "attempt_count": len(attempts),
            "all_connections_drained_committed_flushed_fresh_readback": all(
                item["cost_apply"]["status"] == "passed"
                and item["cost_apply"]["connections_drained_before_apply"]
                and item["cost_apply"]["transaction_committed"]
                and item["cost_apply"]["flush_optimizer_costs"]
                and item["cost_apply"]["fresh_connection_readback"]
                for item in attempts
            ),
        },
    }
    if sections["cost_apply_protocol"]["all_connections_drained_committed_flushed_fresh_readback"] is not True:
        raise GateError("one or more gate attempts violated the frozen cost-apply protocol")
    return {
        "schema_version": 1, "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
        "status": "passed", "generated_at_utc": utc_now(), **sections,
    }


def run_engine_gate() -> dict[str, Any]:
    ready_path = GLOBAL_ROOT / "shared/MYSQL_ENGINE_READY.json"
    if ready_path.exists():
        return validate_engine_ready("mysql")
    machine, fixed, benchmark, preflight, measurement, search, _, _ = _configs()
    prepare_benchmark_configs()
    points = gate_points(search)
    execution = _gate_execution_spec()
    planned = {
        "schema_version": 1, "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
        "status": "registered_before_performance_results",
        "point_set": list(points), "additional_points_allowed": False,
        "repeat_policy": preflight["repeat_policy"],
        "none_case_policy": "P0_three_repeats_only; plan sensitivity and resource effect linked to same workload bottleneck case",
        "attempts": execution,
    }
    _write_immutable(PREFLIGHT_ROOT / "gate_execution_plan.json", _json_bytes(planned))
    attempts = []
    for index, item in enumerate(execution, 1):
        log("gate_attempt_dispatch", sequence=index, total=len(execution), **item)
        attempts.append(_locked_gate_attempt(
            item["case_id"], item["point_id"], item["repeat_id"],
            machine, fixed, benchmark, points,
        ))
    attempts_by_case: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in attempts:
        attempts_by_case[str(item["case_id"])].append(item)
    case_ids = list(CASE_DEFINITIONS)
    case_results = [_aggregate_gate_case(case_id, attempts_by_case) for case_id in case_ids]
    failed = [item for item in case_results if item["status"] != "passed"]
    if failed:
        atomic_json(PREFLIGHT_ROOT / "plan_gate_failure.json", {
            "schema_version": 1, "freeze_id": FREEZE_ID,
            "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
            "status": "blocked", "point_set": list(points), "case_results": case_results,
            "failed_at_utc": utc_now(),
        })
        raise GateError(f"MySQL engine gate failed for cases: {[item['case_id'] for item in failed]}")
    plan_gate = {
        "schema_version": 1, "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
        "status": "passed", "completed_at_utc": utc_now(),
        "point_set": list(points), "case_ids": case_ids,
        "case_results": case_results, "prescribed_gate_attempt_count": len(attempts),
        "measurement_protocol_sha256": sha256_file(SHARED_FROZEN_ROOT / "measurement_protocol.json"),
        "preflight_gates_sha256": sha256_file(SHARED_FROZEN_ROOT / "preflight_gates.json"),
    }
    environment = _environment_readback(machine, fixed, benchmark, attempts)
    plan_path = PREFLIGHT_ROOT / "plan_gate.json"
    environment_path = PREFLIGHT_ROOT / "environment_readback.json"
    _write_immutable(plan_path, _json_bytes(plan_gate))
    _write_immutable(environment_path, _json_bytes(environment))
    return publish_mysql_engine_ready(plan_path, environment_path)


RUN_DEFINITIONS = {
    "A5": ("hybench_ap", "memory"),
    "A6": ("job", "memory"),
    "A7": ("tpch", "memory"),
    "A8": ("hybench_xp", "io"),
    "B13": ("hybench_ap", "memory"),
    "B14": ("job", "memory"),
    "B15": ("hybench_xp", "io"),
    "E5": ("hybench_ap", "none"),
    "E6": ("job", "none"),
    "E7": ("tpch", "none"),
    "E8": ("hybench_xp", "none"),
}


def _parameter_values_from_normalized(
    normalized: Sequence[float], search_space: Mapping[str, Any],
) -> dict[str, float]:
    result = {}
    for name, position in zip(COST_NAMES, normalized, strict=True):
        spec = search_space["parameters"][name]
        lower = float(spec["min"])
        upper = float(spec["max"])
        if spec["transform"] == "log":
            value = math.exp(math.log(lower) + float(position) * (math.log(upper) - math.log(lower)))
        elif spec["transform"] == "linear":
            value = lower + float(position) * (upper - lower)
        else:
            raise GateError(f"unsupported brute-force transform for {name}")
        result[name] = value
    return result


def brute_force_candidates(
    search_spaces: Mapping[str, Any], methods: Mapping[str, Any],
) -> list[dict[str, Any]]:
    protocol = methods["brute_force"]
    if (
        protocol["candidate_selection"] != "farthest_point_maximin_in_normalized_space"
        or protocol["initial_selected_anchors"] != ["all_lower", "all_upper"]
        or protocol["candidate_count"] != 10
        or protocol["early_stop"] != "none"
    ):
        raise GateError("frozen brute-force protocol does not match implemented exact algorithm")
    grid = tuple(float(value) for value in protocol["per_dimension_normalized_grid"])
    if grid != (0.0, 0.25, 0.5, 0.75, 1.0):
        raise GateError(f"unexpected frozen brute-force grid: {grid}")
    space = search_spaces["mysql"]
    default = {name: float(space["default"][name]) for name in COST_NAMES}
    default_normalized = []
    for name in COST_NAMES:
        spec = space["parameters"][name]
        lower = float(spec["min"])
        upper = float(spec["max"])
        value = default[name]
        if spec["transform"] == "log":
            position = (math.log(value) - math.log(lower)) / (math.log(upper) - math.log(lower))
        else:
            position = (value - lower) / (upper - lower)
        default_normalized.append(position)
    default_grid_tuple = (
        tuple(grid.index(next(value for value in grid if math.isclose(value, position, rel_tol=0, abs_tol=1e-14))) for position in default_normalized)
        if all(any(math.isclose(value, position, rel_tol=0, abs_tol=1e-14) for value in grid) for position in default_normalized)
        else None
    )
    eligible = []
    for grid_index, grid_tuple in enumerate(itertools.product(range(len(grid)), repeat=len(COST_NAMES))):
        if protocol["exclude_exact_default"] and default_grid_tuple == grid_tuple:
            continue
        eligible.append((grid_index, tuple(grid[index] for index in grid_tuple), grid_tuple))
    anchors = [tuple(0.0 for _ in COST_NAMES), tuple(1.0 for _ in COST_NAMES)]
    selected: list[tuple[float, ...]] = []
    selected_meta: list[tuple[int, tuple[int, ...]]] = []
    lookup = {normalized: (grid_index, grid_tuple) for grid_index, normalized, grid_tuple in eligible}
    for anchor in anchors:
        if anchor not in lookup:
            raise GateError(f"frozen brute-force anchor is ineligible: {anchor}")
        selected.append(anchor)
        selected_meta.append(lookup[anchor])
    while len(selected) < int(protocol["candidate_count"]):
        best: tuple[float, ...] | None = None
        best_distance = -1.0
        best_meta: tuple[int, tuple[int, ...]] | None = None
        for grid_index, normalized, grid_tuple in eligible:
            if normalized in selected:
                continue
            minimum_distance = min(
                sum((normalized[index] - existing[index]) ** 2 for index in range(len(COST_NAMES)))
                for existing in selected
            )
            # eligible is in ascending parameter-lexicographic/grid-index order;
            # strict greater preserves the first item for every exact distance tie.
            if minimum_distance > best_distance:
                best = normalized
                best_distance = minimum_distance
                best_meta = (grid_index, grid_tuple)
        if best is None or best_meta is None:
            raise GateError("brute-force maximin selection exhausted before candidate_count")
        selected.append(best)
        selected_meta.append(best_meta)
    result = []
    for index, (normalized, meta) in enumerate(zip(selected, selected_meta, strict=True), 1):
        result.append({
            "candidate_id": f"C{index:02d}",
            "selection_order": index,
            "normalized": {name: normalized[offset] for offset, name in enumerate(COST_NAMES)},
            "params": _parameter_values_from_normalized(normalized, space),
            "grid_tuple": list(meta[1]),
            "grid_index": meta[0],
            "anchor": "all_lower" if index == 1 else ("all_upper" if index == 2 else None),
            "min_squared_distance_at_selection": (
                None if index <= 2 else min(
                    sum((normalized[offset] - earlier[offset]) ** 2 for offset in range(len(COST_NAMES)))
                    for earlier in selected[: index - 1]
                )
            ),
        })
    return result


def _point_attempt_number(logical_root: Path, measurement_seconds: int) -> tuple[int, dict[str, Any] | None]:
    attempt = 1
    while True:
        root = logical_root / f"attempt_{attempt:02d}"
        round_summary = root / "round_summary.json"
        if round_summary.is_file():
            payload = load_json(round_summary)
            if (
                payload.get("status") == "complete"
                and int(payload.get("sampling", {}).get("measurement_seconds", 0)) == measurement_seconds
            ):
                return attempt, payload
            if payload.get("status") == "failed":
                return attempt, payload
        measurement = root / "measurement_summary.json"
        if measurement.is_file():
            payload = load_json(measurement)
            if int(payload.get("sampling", {}).get("measurement_seconds", 0)) == measurement_seconds:
                return attempt, payload
        if not root.exists():
            return attempt, None
        attempt += 1


def _write_params(path: Path, values: Mapping[str, Any]) -> None:
    atomic_json(path, {"schema_version": 1, "parameters": dict(values)})


def _formal_measurement_attempt(
    run_id: str, repeat_id: int, logical_name: str, logical_root: Path,
    workload: str, scenario: str, params_before: Mapping[str, float],
    params_candidate: Mapping[str, float], machine: Mapping[str, Any],
    fixed: Mapping[str, Any], measurement_seconds: int = 180,
) -> dict[str, Any]:
    attempt_number, existing = _point_attempt_number(logical_root, measurement_seconds)
    if existing is not None:
        return existing
    output = logical_root / f"attempt_{attempt_number:02d}"
    output.mkdir(parents=True)
    append_jsonl(output / "events.jsonl", {
        "schema_version": 1, "event": "round_start", "at_utc": utc_now(),
        "run_id": run_id, "repeat_id": repeat_id, "logical_name": logical_name,
        "attempt": attempt_number,
    })
    atomic_json(output / "round_manifest.json", {
        "schema_version": 1, "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
        "method": "brute_force", "run_id": run_id, "repeat_id": repeat_id,
        "logical_name": logical_name, "attempt": attempt_number,
        "workload": workload, "scenario": scenario,
        "inner_loop_applicable": False, "measurement_seconds_requested": measurement_seconds,
        "started_at_utc": utc_now(),
    })
    (output / "inner_iterations.jsonl").touch(exist_ok=False)
    _write_params(output / "params_before.json", params_before)
    _write_params(output / "params_candidate.json", params_candidate)
    engine = machine["engines"]["mysql"]
    template_key = "hybench" if workload.startswith("hybench") else workload
    template_root = Path(engine["template_root"]) / template_key
    manifest_path = template_root / "database_manifest.json"
    if not manifest_path.is_file():
        raise GateError(f"formal template manifest is absent: {manifest_path}")
    template_manifest = load_json(manifest_path)
    working_root = Path(engine["working_root"])
    working = (
        working_root / "formal_runs" / f"{run_id}_r{repeat_id}" / logical_name
        / f"attempt_{attempt_number:02d}"
    )
    datadir = working / "datadir"
    container = ""
    try:
        machine_readback = apply_and_read_machine(machine)
        admission = host_admission(machine, output / "host_admission.json")
        _copy_template(template_root / "datadir", datadir, working_root)
        container, port, adapter = _start_container(machine, fixed, workload, scenario, datadir)
        restored = validate_restored_template(port, template_manifest)
        fixed_before = validate_fixed_settings(port, fixed, scenario)
        cost_apply = apply_costs(port, params_candidate)
        fixed_after_cost = validate_fixed_settings(port, fixed, scenario)
        applied = cost_apply["values"]
        _write_params(output / "params_applied.json", applied)
        config_path = BENCHMARK_CONFIG_ROOT / workload / "benchmark_config.json"
        benchmark_config = load_json(config_path)
        plan = capture_plan(
            port, benchmark_config["database"], Path(benchmark_config["target_sql"]),
            output / "plan.json", parameter_int=314 if workload == "tpch" else None,
        )
        atomic_json(output / "plan_fingerprint.json", {
            "schema_version": 1,
            "structural_fingerprint_sha256": plan["structural_fingerprint_sha256"],
            "structural_projection": plan["structural_projection"],
        })
        correctness = run_correctness(workload, benchmark_config, output)
        total_duration = 60 + measurement_seconds
        if workload.startswith("hybench"):
            command, cwd = _hybench_command(output, benchmark_config, workload, total_duration)
        elif workload == "job":
            command, cwd = _job_command(output, benchmark_config, total_duration)
        else:
            command, cwd = _benchbase_command(output, benchmark_config, total_duration)
        sampling = run_with_sampling(
            command, cwd, adapter, port, output,
            warmup_seconds=60, measurement_seconds=measurement_seconds,
        )
        if workload.startswith("hybench"):
            metrics = _hybench_metrics(output, sampling, workload)
        elif workload == "job":
            metrics = _job_metrics(output, sampling)
        else:
            metrics = _tpch_metrics(output, sampling)
        resource_samples = list(sampling.pop("resource_samples"))
        database_samples = list(sampling.pop("database_samples"))
        decision_resource_samples = _decision_window_samples(resource_samples)
        decision_database_samples = _decision_window_samples(database_samples)
        resource_means_full_measurement_audit = _mean_resources(resource_samples)
        resource_means = _mean_resources(decision_resource_samples)
        pressure = _pressure_evidence(
            decision_resource_samples, decision_database_samples, scenario
        )
        stability = _measurement_stability(workload, resource_samples, metrics)
        rollback_delta = _server_counter_delta(database_samples, "Com_rollback")
        aborted_delta = _server_counter_delta(database_samples, "Aborted_clients")
        metric_failures = int(metrics.get("failures") or 0)
        completed = float(metrics.get("query_count") or 0) + float(metrics.get("transaction_count") or 0)
        # HyBench AT1/AT2/AT6 intentionally call connection.rollback() for
        # business-rule rejections.  Preserve the counter in xp_correctness,
        # but do not classify these expected business aborts as system errors.
        rollback_counted_as_error = workload != "hybench_xp"
        error_rollback_delta = rollback_delta if rollback_counted_as_error else 0
        error_total = error_rollback_delta + aborted_delta + metric_failures
        error_rate = error_total / max(completed + error_total, 1.0)
        state = sampling["container_state_after"]
        oom = bool(state["OOMKilled"])
        restarted = (
            int(state["Pid"]) != adapter.pid
            or int(sampling["container_restart_count_after"]) > 0
        )
        xp_correctness = None
        workload_correctness_passed = True
        if workload == "hybench_xp":
            commits = {key: int(value) for key, value in metrics["at_commit_counts"].items()}
            expected_types = {f"AT{index}" for index in range(1, 7)}
            workload_correctness_passed = (
                set(commits) == expected_types and all(value > 0 for value in commits.values())
                and aborted_delta == 0 and metric_failures == 0
            )
            xp_correctness = {
                "at1_to_at6": {
                    name: {
                        "commits": commits.get(name, 0), "rollbacks": None,
                        "timeouts": 0 if workload_correctness_passed else None,
                        "errors": 0 if workload_correctness_passed else None,
                        "null_reason": "server-wide Com_rollback cannot be attributed to an individual AT type",
                    }
                    for name in sorted(expected_types)
                },
                "global_rollback_delta": rollback_delta,
                "expected_business_rollback_delta": rollback_delta,
                "rollback_counted_as_error": False,
                "rollback_semantics": (
                    "HyBench AT1/AT2/AT6 explicitly roll back rejected business operations; "
                    "these are not database/system failures"
                ),
                "global_aborted_client_delta": aborted_delta,
                "metric_failures": metric_failures,
                "passed": workload_correctness_passed,
            }
        baseline_gate = _baseline_gate(
            scenario, resource_means, pressure, stable=stability["stable"],
            correctness=workload_correctness_passed, error_rate=error_rate,
            oom=oom, restarted=restarted,
        )
        fixed_after = validate_fixed_settings(port, fixed, scenario)
        final_cost = _cost_readback(port)
        for name in COST_NAMES:
            if not math.isclose(final_cost[name], float(params_candidate[name]), rel_tol=1e-12, abs_tol=1e-12):
                raise GateError(f"final formal cost readback drifted for {name}")
        measurement = {
            "schema_version": 1, "freeze_id": FREEZE_ID,
            "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
            "method": "brute_force", "run_id": run_id, "repeat_id": repeat_id,
            "status": "measurement_complete", "logical_name": logical_name,
            "attempt": attempt_number, "workload": workload, "scenario": scenario,
            "completed_at_utc": utc_now(), "params_before": dict(params_before),
            "params_candidate": dict(params_candidate), "params_applied": applied,
            "template_validation": restored, "template_manifest_path": str(manifest_path),
            "template_manifest_sha256": sha256_file(manifest_path),
            "benchmark_config_path": str(config_path), "benchmark_config_sha256": sha256_file(config_path),
            "host_machine_readback": machine_readback, "host_admission": admission,
            "resource_readback": adapter.readback, "fixed_settings_before": fixed_before,
            "cost_apply": cost_apply, "fixed_settings_after_cost": fixed_after_cost,
            "plan_fingerprint_sha256": plan["structural_fingerprint_sha256"],
            "correctness": correctness, "xp_correctness": xp_correctness,
            "workload_correctness_passed": workload_correctness_passed,
            "sampling": sampling, "metrics": metrics, "resource_means": resource_means,
            "resource_means_window_seconds": 180,
            "resource_means_full_measurement_audit": resource_means_full_measurement_audit,
            "pressure_evidence": pressure, "stability": stability,
            "error_rate": error_rate, "oom": oom, "database_restarted": restarted,
            "baseline_resource_gate": baseline_gate,
            "fixed_settings_after_measurement": fixed_after, "final_cost_readback": final_cost,
            "measurement_valid": bool(
                workload_correctness_passed and error_rate <= 0.01
                and not oom and not restarted and float(sampling["sample_coverage"]) >= 0.9
            ),
            "attempt_path": str(output),
        }
        private = output / "private"
        if private.exists():
            shutil.rmtree(private)
        measurement["redacted_generated_benchbase_configs"] = (
            _redact_benchbase_configs(output) if workload == "tpch" else []
        )
        atomic_json(output / "benchmark_result.json", metrics)
        atomic_json(output / "stability.json", stability)
        windows = (
            stability.get("resource", {}).get("resource_windows", [])
            if workload == "hybench_xp" else stability.get("resource_and_throughput", {}).get("resource_windows", [])
        )
        for window in windows:
            append_jsonl(output / "throughput_windows.jsonl", window)
        if not (output / "throughput_windows.jsonl").exists():
            (output / "throughput_windows.jsonl").touch()
        atomic_json(output / "measurement_summary.json", measurement)
        append_jsonl(output / "events.jsonl", {
            "schema_version": 1, "event": "measurement_end", "at_utc": utc_now(),
            "measurement_valid": measurement["measurement_valid"],
        })
        return measurement
    except Exception as exc:
        # Preserve database-side evidence before the finally block removes the
        # point container.  This is essential on remote hosts where an abrupt
        # server exit otherwise surfaces only as ERROR 2013 to the client.
        if container:
            try:
                docker_logs = run(["docker", "logs", "--timestamps", container], check=False)
                (output / "container_failure.log").write_text(
                    docker_logs.stdout or "", encoding="utf-8"
                )
                docker_inspect = run(["docker", "inspect", container], check=False)
                (output / "container_failure_inspect.json").write_text(
                    docker_inspect.stdout or "[]\n", encoding="utf-8"
                )
            except Exception as diagnostic_exc:
                (output / "container_failure_capture_error.txt").write_text(
                    f"{type(diagnostic_exc).__name__}: {diagnostic_exc}\n",
                    encoding="utf-8",
                )
        failure = {
            "schema_version": 1, "freeze_id": FREEZE_ID,
            "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
            "method": "brute_force", "run_id": run_id, "repeat_id": repeat_id,
            "status": "failed", "logical_name": logical_name, "attempt": attempt_number,
            "failed_at_utc": utc_now(), "error_type": type(exc).__name__, "error": str(exc),
            "candidate_budget_consumed": logical_name.startswith("round_"),
            "attempt_path": str(output),
        }
        atomic_json(output / "failure.json", failure)
        atomic_json(output / "decision.json", {
            "schema_version": 1, "accepted": False,
            "accept_reject_reason": f"measurement_failed:{type(exc).__name__}",
            "params_before": dict(params_before), "params_candidate": dict(params_candidate),
            "params_after": dict(params_before),
        })
        atomic_json(output / "round_summary.json", failure)
        append_jsonl(output / "events.jsonl", {
            "schema_version": 1, "event": "round_end", "at_utc": utc_now(),
            "status": "failed", "error_type": type(exc).__name__,
        })
        private = output / "private"
        if private.exists():
            shutil.rmtree(private)
        _attempt_checksums(output)
        return failure
    finally:
        if container:
            _remove_container(container)
        _cleanup_working(working, working_root)


def _finalize_formal_measurement(
    measurement: Mapping[str, Any], decision: Mapping[str, Any],
) -> dict[str, Any]:
    if measurement.get("status") == "complete":
        return dict(measurement)
    if measurement.get("status") == "failed":
        return dict(measurement)
    output = Path(str(measurement["attempt_path"]))
    final = {**measurement, "status": "complete", "decision": dict(decision)}
    atomic_json(output / "decision.json", decision)
    atomic_json(output / "round_summary.json", final)
    append_jsonl(output / "events.jsonl", {
        "schema_version": 1, "event": "round_end", "at_utc": utc_now(),
        "status": "complete", "accepted": decision.get("accepted"),
    })
    _attempt_checksums(output)
    return final


def _correctness_signature(point: Mapping[str, Any]) -> tuple[int, str] | None:
    value = point.get("correctness")
    if not isinstance(value, Mapping):
        return None
    try:
        return int(value["row_count"]), str(value["sha256"])
    except (KeyError, TypeError, ValueError):
        return None


def _throughput_ratio(candidate: Mapping[str, Any], reference: Mapping[str, Any]) -> tuple[float, dict[str, float | None]]:
    if candidate["workload"] == "hybench_xp":
        cx = float(candidate["metrics"]["xap_qps"])
        ct = float(candidate["metrics"]["xtp_tps"])
        rx = float(reference["metrics"]["xap_qps"])
        rt = float(reference["metrics"]["xtp_tps"])
        if min(cx, ct, rx, rt) <= 0:
            return 0.0, {"xap_ratio": None, "xtp_ratio": None}
        xratio = cx / rx
        tratio = ct / rt
        return math.sqrt(xratio * tratio), {"xap_ratio": xratio, "xtp_ratio": tratio}
    base = _primary_throughput(reference)
    ratio = _primary_throughput(candidate) / base if base > 0 else 0.0
    return ratio, {"xap_ratio": None, "xtp_ratio": None}


def _relative_utility(
    candidate: Mapping[str, Any], reference: Mapping[str, Any], scenario: str,
) -> tuple[float, dict[str, Any]]:
    throughput_ratio, streams = _throughput_ratio(candidate, reference)
    p95_reference = float(reference["metrics"]["latency_p95_ms"])
    p99_reference = float(reference["metrics"]["latency_p99_ms"])
    p95_ratio = (
        float(candidate["metrics"]["latency_p95_ms"]) / p95_reference
        if p95_reference > 0 else (1.0 if float(candidate["metrics"]["latency_p95_ms"]) == 0 else math.inf)
    )
    p99_ratio = (
        float(candidate["metrics"]["latency_p99_ms"]) / p99_reference
        if p99_reference > 0 else (1.0 if float(candidate["metrics"]["latency_p99_ms"]) == 0 else math.inf)
    )
    latency_penalty = 2 * max(0.0, p95_ratio - 1.0) + 3 * max(0.0, p99_ratio - 1.0)
    if throughput_ratio <= 0 or not math.isfinite(p95_ratio) or not math.isfinite(p99_ratio):
        return -math.inf, {
            "throughput_ratio": throughput_ratio, **streams,
            "p95_ratio": p95_ratio if math.isfinite(p95_ratio) else None,
            "p99_ratio": p99_ratio if math.isfinite(p99_ratio) else None,
        }
    if scenario == "none":
        utility = math.log(throughput_ratio) - latency_penalty
        details = {"target_pressure_drop_ratio": None, "non_target_penalty": 0.0}
    else:
        target_drop = (
            float(reference["resource_means"][scenario])
            - float(candidate["resource_means"][scenario])
        )
        non_target_penalty = 4 * sum(
            max(0.0, float(value) - 0.8)
            for name, value in candidate["resource_means"].items() if name != scenario
        )
        utility = math.log(throughput_ratio) + target_drop - latency_penalty - non_target_penalty
        details = {
            "target_pressure_drop_ratio": target_drop,
            "non_target_penalty": non_target_penalty,
        }
    return utility, {
        "throughput_ratio": throughput_ratio, **streams,
        "p95_ratio": p95_ratio, "p99_ratio": p99_ratio,
        "latency_penalty": latency_penalty, **details,
    }


def outer_decision(
    candidate: Mapping[str, Any], incumbent: Mapping[str, Any],
    baseline: Mapping[str, Any], scenario: str, policy: Mapping[str, Any],
) -> dict[str, Any]:
    utility, ratios = _relative_utility(candidate, incumbent, scenario)
    hard = policy["common_hard_gates"]
    failures = []
    if _correctness_signature(candidate) != _correctness_signature(baseline):
        failures.append("correctness_signature_mismatch")
    if candidate.get("workload_correctness_passed") is not True:
        failures.append("workload_correctness_failed")
    if candidate.get("measurement_valid") is not True:
        failures.append("measurement_validity_failed")
    if float(candidate["sampling"]["sample_coverage"]) < float(hard["sample_coverage_min"]):
        failures.append("sample_coverage_below_minimum")
    if float(candidate["error_rate"]) > float(hard["error_rate_max"]):
        failures.append("error_rate_above_maximum")
    if candidate["oom"] or candidate["database_restarted"]:
        failures.append("oom_or_database_restart")
    if ratios.get("p95_ratio") is None or float(ratios["p95_ratio"]) > 1 + float(hard["p95_regression_max_ratio"]):
        failures.append("p95_regression_above_maximum")
    if ratios.get("p99_ratio") is None or float(ratios["p99_ratio"]) > 1 + float(hard["p99_regression_max_ratio"]):
        failures.append("p99_regression_above_maximum")
    throughput_ratio = float(ratios["throughput_ratio"])
    branch = None
    if scenario == "none":
        selected = policy["none_candidate"]
        if any(float(value) >= float(selected["all_resource_utilization_max"]) for value in candidate["resource_means"].values()):
            failures.append("none_resource_utilization_above_maximum")
        if throughput_ratio < 1 + float(selected["throughput_improvement_min_ratio"]):
            failures.append("none_throughput_improvement_below_minimum")
        if utility < float(selected["utility_min_improvement"]):
            failures.append("utility_improvement_below_minimum")
        branch = "none_throughput"
    else:
        selected = policy["bottleneck_candidate"]
        if any(
            float(value) >= float(selected["non_target_resource_utilization_max"])
            for name, value in candidate["resource_means"].items() if name != scenario
        ):
            failures.append("non_target_resource_utilization_above_maximum")
        if candidate["workload"] == "hybench_xp":
            limit = 1 - float(policy["throughput"]["xp_each_stream_regression_max_ratio"])
            if ratios["xap_ratio"] is None or ratios["xtp_ratio"] is None or min(float(ratios["xap_ratio"]), float(ratios["xtp_ratio"])) < limit:
                failures.append("xp_stream_regression_above_maximum")
        target_change = (
            float(candidate["resource_means"][scenario])
            - float(incumbent["resource_means"][scenario])
        )
        throughput_branch = (
            throughput_ratio >= 1 + float(selected["branch_throughput"]["throughput_improvement_min_ratio"])
            and target_change <= float(selected["branch_throughput"]["target_pressure_increase_max_ratio"])
        )
        resource_branch = (
            -target_change >= float(selected["branch_resource"]["target_pressure_drop_min_ratio"])
            and throughput_ratio >= 1 - float(selected["branch_resource"]["throughput_regression_max_ratio"])
        )
        if not (throughput_branch or resource_branch):
            failures.append("neither_bottleneck_acceptance_branch_passed")
        elif throughput_branch:
            branch = "throughput"
        else:
            branch = "resource"
        if utility < float(selected["utility_min_improvement"]):
            failures.append("utility_improvement_below_minimum")
    accepted = not failures
    return {
        "schema_version": 1, "round": candidate["logical_name"],
        "accepted": accepted,
        "accept_reject_reason": "accepted_" + str(branch) if accepted else ";".join(failures),
        "acceptance_branch": branch if accepted else None,
        "hard_gate_failures": failures,
        "params_before": dict(incumbent["params_applied"]),
        "params_candidate": dict(candidate["params_applied"]),
        "params_after": dict(candidate["params_applied"] if accepted else incumbent["params_applied"]),
        "plan_before_sha256": incumbent["plan_fingerprint_sha256"],
        "plan_candidate_sha256": candidate["plan_fingerprint_sha256"],
        "plan_changed": incumbent["plan_fingerprint_sha256"] != candidate["plan_fingerprint_sha256"],
        "qps_before": _primary_throughput(incumbent),
        "qps_candidate": _primary_throughput(candidate),
        "target_util_before_ratio": None if scenario == "none" else incumbent["resource_means"][scenario],
        "target_util_candidate_ratio": None if scenario == "none" else candidate["resource_means"][scenario],
        "stable": candidate["stability"]["stable"],
        "measurement_seconds": candidate["sampling"]["measurement_seconds"],
        "sample_coverage": candidate["sampling"]["sample_coverage"],
        "utility_improvement": utility if math.isfinite(utility) else None,
        "ratios": ratios,
    }


def _validation_decision(
    point: Mapping[str, Any], baseline: Mapping[str, Any], label: str,
) -> dict[str, Any]:
    failures = []
    if point.get("measurement_valid") is not True:
        failures.append("measurement_invalid")
    if _correctness_signature(point) != _correctness_signature(baseline):
        failures.append("correctness_signature_mismatch")
    return {
        "schema_version": 1, "accepted": not failures,
        "accept_reject_reason": label if not failures else ";".join(failures),
        "validation_label": label, "failures": failures,
        "params_before": dict(point["params_before"]),
        "params_candidate": dict(point["params_candidate"]),
        "params_after": dict(point["params_candidate"]),
    }


def _statistics(values: Sequence[float], seed: int) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "stddev": None, "bootstrap_95_ci": [None, None]}
    rng = random.Random(seed)
    means = []
    for _ in range(10000):
        means.append(statistics.fmean(values[rng.randrange(len(values))] for _ in values))
    means.sort()
    return {
        "count": len(values), "values": list(values),
        "mean": statistics.fmean(values),
        "stddev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "bootstrap_95_ci": [means[249], means[9749]],
        "bootstrap_samples": 10000, "bootstrap_seed": seed,
    }


ROLLING_MEASUREMENT_DECISION_SECONDS = tuple(range(180, 601, 30))


class ContinuousMeasurementSession(Protocol):
    """Required runner interface for the frozen rolling measurement protocol.

    The workload and database must stay alive between calls.  In particular,
    ``wait_until_measurement(210)`` extends the measurement already observed at
    180 seconds; it must not start another workload replay.  ``observation``
    must contain workload-native, per-second throughput evidence.  A MySQL
    server activity counter such as ``Questions`` is deliberately not a valid
    substitute for workload throughput.
    """

    def start(self, *, warmup_seconds: int, maximum_measurement_seconds: int) -> None: ...

    def wait_until_measurement(self, measurement_seconds: int) -> None: ...

    def observation(self, measurement_seconds: int) -> Mapping[str, Any]: ...

    def stop(self, measurement_seconds: int) -> Mapping[str, Any]: ...


def _validate_live_measurement_observation(
    observation: Mapping[str, Any], expected_seconds: int,
) -> None:
    if int(observation.get("measurement_seconds", -1)) != expected_seconds:
        raise GateError(
            "continuous measurement observation has the wrong elapsed time: "
            f"expected={expected_seconds} actual={observation.get('measurement_seconds')!r}"
        )
    evidence = observation.get("throughput_evidence")
    if not isinstance(evidence, Mapping) or evidence.get("reliable") is not True:
        raise GateError(
            "rolling stability requires reliable workload-native real-time throughput evidence"
        )
    source = str(evidence.get("source", "")).strip().lower()
    if not source or source in {
        "questions", "mysql_questions", "performance_schema.questions",
        "performance_schema_global_status_questions",
    }:
        raise GateError(
            "MySQL Questions is an activity counter, not workload throughput; "
            "the runner must expose cumulative workload completions sampled at one second"
        )
    stability = observation.get("stability")
    if not isinstance(stability, Mapping) or not isinstance(stability.get("stable"), bool):
        raise GateError("continuous measurement observation is missing a stability decision")
    window_seconds = stability.get("decision_window_seconds")
    if window_seconds is not None and int(window_seconds) != 180:
        raise GateError("stability decision did not use the latest 180-second window")


def _run_continuous_measurement(
    session: ContinuousMeasurementSession,
) -> Mapping[str, Any]:
    """Drive one 60+180..600 run, extending only in 30-second increments."""
    session.start(warmup_seconds=60, maximum_measurement_seconds=600)
    last_observation: Mapping[str, Any] | None = None
    for measurement_seconds in ROLLING_MEASUREMENT_DECISION_SECONDS:
        session.wait_until_measurement(measurement_seconds)
        observation = session.observation(measurement_seconds)
        _validate_live_measurement_observation(observation, measurement_seconds)
        last_observation = observation
        if bool(observation["stability"]["stable"]):
            result = dict(session.stop(measurement_seconds))
            result["rolling_measurement_control"] = {
                "warmup_seconds": 60,
                "selected_measurement_seconds": measurement_seconds,
                "maximum_measurement_seconds": 600,
                "decision_step_seconds": 30,
                "decision_window_seconds": 180,
                "stop_reason": "latest_six_30_second_windows_stable",
                "selected_observation": dict(observation),
            }
            return result
    if last_observation is None:  # pragma: no cover - schedule is a constant
        raise GateError("rolling measurement produced no decision observation")
    result = dict(session.stop(600))
    result["rolling_measurement_control"] = {
        "warmup_seconds": 60,
        "selected_measurement_seconds": 600,
        "maximum_measurement_seconds": 600,
        "decision_step_seconds": 30,
        "decision_window_seconds": 180,
        "stop_reason": "maximum_measurement_seconds_reached_unstable",
        "selected_observation": dict(last_observation),
    }
    return result


def _measurement_with_extension(
    run_id: str, repeat_id: int, logical_name: str, logical_root: Path,
    workload: str, scenario: str, params_before: Mapping[str, float],
    params_candidate: Mapping[str, float], machine: Mapping[str, Any], fixed: Mapping[str, Any],
) -> dict[str, Any]:
    raise GateError(
        "formal measurement is fail-closed: the current terminal-only runner cannot "
        "provide a ContinuousMeasurementSession with workload-native one-second "
        "throughput while keeping one workload process alive for the 180/210/.../600 "
        "rolling decisions. Implement that interface and invoke "
        "_run_continuous_measurement; do not use Questions as db_qps and do not "
        "restart a 600-second replay after an unstable 180-second replay"
    )


def _validation_summary(points: Sequence[Mapping[str, Any]], bootstrap_seed: int) -> dict[str, Any]:
    metrics = {
        "qps": [float(item["metrics"]["qps"]) for item in points if item["metrics"].get("qps") is not None],
        "xap_qps": [float(item["metrics"]["xap_qps"]) for item in points if item["metrics"].get("xap_qps") is not None],
        "xtp_tps": [float(item["metrics"]["xtp_tps"]) for item in points if item["metrics"].get("xtp_tps") is not None],
        "latency_p95_ms": [float(item["metrics"]["latency_p95_ms"]) for item in points],
        "latency_p99_ms": [float(item["metrics"]["latency_p99_ms"]) for item in points],
        "cpu_utilization_ratio": [float(item["resource_means"]["cpu"]) for item in points],
        "memory_utilization_ratio": [float(item["resource_means"]["memory"]) for item in points],
        "io_utilization_ratio": [float(item["resource_means"]["io"]) for item in points],
    }
    return {
        "replay_count": len(points),
        "all_measurement_valid": all(bool(item["measurement_valid"]) for item in points),
        "all_correctness_signatures": [
            {"row_count": signature[0], "sha256": signature[1]}
            for signature in sorted({_correctness_signature(item) for item in points if _correctness_signature(item) is not None})
        ],
        "metrics": {name: _statistics(values, bootstrap_seed) for name, values in metrics.items()},
        "attempt_paths": [item["attempt_path"] for item in points],
    }


def _run_checksums(root: Path) -> Path:
    files = [
        path for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
        and path.name != "checksums.sha256" and "private" not in path.parts
    ]
    lines = [f"{sha256_file(path)}  {path.relative_to(root).as_posix()}" for path in sorted(files)]
    target = root / "checksums.sha256"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _next_run_attempt_id(run_root: Path) -> int:
    attempts = run_root / "run_attempts"
    attempts.mkdir(parents=True, exist_ok=True)
    existing = []
    for path in attempts.glob("attempt_*.json"):
        try:
            existing.append(int(path.stem.split("_")[-1]))
        except ValueError:
            continue
    return max(existing, default=0) + 1


def execute_complete_run(
    schedule_entry: Mapping[str, Any], whole_run_attempt_id: int,
) -> tuple[dict[str, Any], Path]:
    run_id = str(schedule_entry["run_id"])
    repeat_id = int(schedule_entry["repeat_id"])
    if run_id not in RUN_DEFINITIONS:
        raise GateError(f"scheduler assigned non-target MySQL brute-force run: {run_id}")
    workload, scenario = RUN_DEFINITIONS[run_id]
    run_root = GLOBAL_ROOT / "mysql/brute_force" / f"{run_id}_r{repeat_id}"
    run_root.mkdir(parents=True, exist_ok=True)
    run_attempt_path = run_root / "run_attempts" / f"attempt_{whole_run_attempt_id:02d}.json"
    if not run_attempt_path.exists():
        atomic_json(run_attempt_path, {
            "schema_version": 1, "status": "running", "started_at_utc": utc_now(),
            "started_epoch_ms": time.time_ns() // 1_000_000,
            "whole_run_attempt_id": whole_run_attempt_id, "ordinal": schedule_entry["ordinal"],
            "run_id": run_id, "repeat_id": repeat_id,
        })
    result_path = run_root / "run_summary.json"
    if result_path.is_file():
        existing = load_json(result_path)
        if existing.get("status") == "complete":
            completion_marker = run_root / "run_attempts" / f"completion_{whole_run_attempt_id:02d}.json"
            if not completion_marker.exists():
                atomic_json(completion_marker, {
                    "schema_version": 1, "status": "complete_result_reused_for_scheduler_resume",
                    "completed_at_utc": utc_now(), "run_summary_sha256": sha256_file(result_path),
                })
            return existing, result_path
        raise GateError(f"existing run_summary is not complete: {result_path}")
    machine, fixed, benchmark, _, measurement, search, methods, policy = _configs()
    expected = {
        "engine": ENGINE, "method": "brute_force", "run_id": run_id, "repeat_id": repeat_id,
    }
    for key, value in expected.items():
        if schedule_entry.get(key) != value:
            raise GateError(f"schedule entry mismatch for complete run {key}: {schedule_entry.get(key)} != {value}")
    candidates = brute_force_candidates(search, methods)
    candidate_path = run_root / "candidate_order.json"
    _write_immutable(candidate_path, _json_bytes({
        "schema_version": 1, "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
        "method": "brute_force", "run_id": run_id, "repeat_id": repeat_id,
        "status": "selected_before_performance_results", "candidate_count": len(candidates),
        "search_space_sha256": sha256_file(SHARED_FROZEN_ROOT / "search_spaces.json"),
        "method_protocols_sha256": sha256_file(SHARED_FROZEN_ROOT / "method_protocols.json"),
        "candidates": candidates,
    }))
    template_key = "hybench" if workload.startswith("hybench") else workload
    template_manifest_path = Path(machine["engines"]["mysql"]["template_root"]) / template_key / "database_manifest.json"
    template_manifest = load_json(template_manifest_path)
    benchmark_config_path = BENCHMARK_CONFIG_ROOT / workload / "benchmark_config.json"
    method_manifest_path = run_root / "method_manifest.json"
    _write_immutable(method_manifest_path, _json_bytes({
        "schema_version": 1, "freeze_id": FREEZE_ID, "method": "brute_force",
        "frozen_protocol": methods["brute_force"],
        "implementation_source": str(Path(__file__).resolve()),
        "implementation_source_sha256": sha256_file(Path(__file__).resolve()),
    }))
    run_manifest_path = run_root / "run_manifest.json"
    _write_immutable(run_manifest_path, _json_bytes({
        "schema_version": 1, "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
        "method": "brute_force", "run_id": run_id, "repeat_id": repeat_id,
        "ordinal": schedule_entry["ordinal"], "phase": schedule_entry["phase"],
        "workload": workload, "scenario": scenario,
        "workload_seed": measurement["seeds"]["workload_seed"],
        "method_seed": measurement["seeds"]["method_seed"],
        "point_order_seed": measurement["seeds"]["point_order_seed"],
        "benchmark_config_path": str(benchmark_config_path),
        "benchmark_config_sha256": sha256_file(benchmark_config_path),
        "database_template_path": str(template_manifest_path),
        "database_template_sha256": sha256_file(template_manifest_path),
        "fixed_db_settings_sha256": sha256_file(SHARED_FROZEN_ROOT / "fixed_db_settings.json"),
        "search_space_sha256": sha256_file(SHARED_FROZEN_ROOT / "search_spaces.json"),
        "method_manifest_sha256": sha256_file(method_manifest_path),
        "acceptance_policy_sha256": sha256_file(SHARED_FROZEN_ROOT / "acceptance_policy.json"),
        "resource_awareness_config_path": str(RESOURCE_CONTRACT),
        "resource_awareness_sha256": RESOURCE_AWARENESS_SHA256,
        "resource_metrics_source": "docker_cgroup_runtime",
        "candidate_order_path": str(candidate_path),
        "candidate_order_sha256": sha256_file(candidate_path),
        "status": "registered_before_run_results",
    }))
    started_epoch_ms = min(
        int(load_json(path)["started_epoch_ms"])
        for path in sorted((run_root / "run_attempts").glob("attempt_*.json"))
    )
    default_params = {name: float(search["mysql"]["default"][name]) for name in COST_NAMES}
    baseline_raw = _measurement_with_extension(
        run_id, repeat_id, "round_000_baseline", run_root / "round_000_baseline",
        workload, scenario, default_params, default_params, machine, fixed,
    )
    if baseline_raw.get("status") == "failed":
        raise GateError(f"formal baseline failed for {run_id} repeat {repeat_id}: {baseline_raw.get('error')}")
    baseline = _finalize_formal_measurement(
        baseline_raw, _validation_decision(baseline_raw, baseline_raw, "search_baseline"),
    )
    if not baseline["measurement_valid"] or not baseline["baseline_resource_gate"]["passed"]:
        raise GateError(
            f"formal default baseline gate failed for {run_id} repeat {repeat_id}: "
            f"{baseline['baseline_resource_gate']['failures']}"
        )
    incumbent = baseline
    accepted_points: list[tuple[int, Mapping[str, Any]]] = []
    outer_loop = []
    for index, candidate in enumerate(candidates, 1):
        logical_name = f"round_{index:03d}"
        measured = _measurement_with_extension(
            run_id, repeat_id, logical_name, run_root / logical_name,
            workload, scenario, incumbent["params_applied"], candidate["params"], machine, fixed,
        )
        if measured.get("status") == "failed":
            outer_loop.append({
                "iter": index, "candidate_id": candidate["candidate_id"], "attempt_status": "failed",
                "accepted": False, "accept_reject_reason": measured.get("error"),
                "params_before": dict(incumbent["params_applied"]),
                "params_candidate": dict(candidate["params"]),
                "round_summary_path": str(Path(measured["attempt_path"]) / "round_summary.json"),
            })
            continue
        decision = outer_decision(measured, incumbent, baseline, scenario, policy)
        point = _finalize_formal_measurement(measured, decision)
        outer_loop.append({
            "iter": index, "candidate_id": candidate["candidate_id"], "attempt_status": "complete",
            "accepted": decision["accepted"], "accept_reject_reason": decision["accept_reject_reason"],
            "params_before": decision["params_before"], "params_candidate": decision["params_candidate"],
            "params_applied": point["params_applied"], "qps": point["metrics"].get("qps"),
            "xap_qps": point["metrics"].get("xap_qps"), "xtp_tps": point["metrics"].get("xtp_tps"),
            "latency_p95_ms": point["metrics"]["latency_p95_ms"],
            "latency_p99_ms": point["metrics"]["latency_p99_ms"],
            "resource": point["resource_means"], "stability": point["stability"],
            "utility_improvement": decision["utility_improvement"],
            "plan_fingerprint_sha256": point["plan_fingerprint_sha256"],
            "plan_changed_from_incumbent": decision["plan_changed"],
            "round_summary_path": str(Path(point["attempt_path"]) / "round_summary.json"),
        })
        if decision["accepted"]:
            accepted_points.append((index, point))
            incumbent = point
    if len(outer_loop) != 10:
        raise GateError("brute-force run did not consume exactly ten frozen candidates")
    eligible: list[tuple[int, Mapping[str, Any], float]] = [(0, baseline, 0.0)]
    for index, point in accepted_points:
        utility, _ = _relative_utility(point, baseline, scenario)
        eligible.append((index, point, utility))
    def final_key(item: tuple[int, Mapping[str, Any], float]) -> tuple[float, float, float, int]:
        index, point, utility = item
        target_pressure = 0.0 if scenario == "none" else float(point["resource_means"][scenario])
        return utility, _primary_throughput(point), -target_pressure, -index
    final_index, selected_final, final_utility = max(eligible, key=final_key)
    final_params = dict(selected_final["params_applied"])
    default_replays = []
    final_replays = []
    for replay in range(1, 4):
        logical = f"validation_default/replay_{replay:02d}"
        measured = _measurement_with_extension(
            run_id, repeat_id, logical, run_root / "validation_default" / f"replay_{replay:02d}",
            workload, scenario, default_params, default_params, machine, fixed,
        )
        if measured.get("status") == "failed":
            raise GateError(f"default validation replay {replay} failed: {measured.get('error')}")
        point = _finalize_formal_measurement(measured, _validation_decision(measured, baseline, "default_validation"))
        if not point["measurement_valid"] or not point["baseline_resource_gate"]["passed"]:
            raise GateError(f"default validation replay {replay} failed frozen gates")
        default_replays.append(point)
    for replay in range(1, 4):
        logical = f"validation_final/replay_{replay:02d}"
        measured = _measurement_with_extension(
            run_id, repeat_id, logical, run_root / "validation_final" / f"replay_{replay:02d}",
            workload, scenario, final_params, final_params, machine, fixed,
        )
        if measured.get("status") == "failed":
            raise GateError(f"final validation replay {replay} failed: {measured.get('error')}")
        point = _finalize_formal_measurement(measured, _validation_decision(measured, baseline, "final_validation"))
        if not point["measurement_valid"] or _correctness_signature(point) != _correctness_signature(baseline):
            raise GateError(f"final validation replay {replay} failed frozen gates")
        final_replays.append(point)
    bootstrap_seed = int(measurement["seeds"]["bootstrap_seed"])
    default_summary = _validation_summary(default_replays, bootstrap_seed)
    final_summary = _validation_summary(final_replays, bootstrap_seed)
    default_primary = (
        default_summary["metrics"]["qps"]["mean"]
        if workload != "hybench_xp" else math.sqrt(
            default_summary["metrics"]["xap_qps"]["mean"] * default_summary["metrics"]["xtp_tps"]["mean"]
        )
    )
    final_primary = (
        final_summary["metrics"]["qps"]["mean"]
        if workload != "hybench_xp" else math.sqrt(
            final_summary["metrics"]["xap_qps"]["mean"] * final_summary["metrics"]["xtp_tps"]["mean"]
        )
    )
    target_before = None if scenario == "none" else default_summary["metrics"][f"{scenario}_utilization_ratio"]["mean"]
    target_after = None if scenario == "none" else final_summary["metrics"][f"{scenario}_utilization_ratio"]["mean"]
    actual_attempts = [
        path for path in run_root.rglob("attempt_*")
        if path.is_dir() and ((path / "measurement_summary.json").exists() or (path / "failure.json").exists())
    ]
    failed_or_invalid = sum(
        1 for item in outer_loop
        if item["attempt_status"] == "failed" or "stability_or_measurement_validity_failed" in str(item["accept_reject_reason"])
    )
    plan_changes = sum(
        1 for item in outer_loop
        if item["attempt_status"] == "complete"
        and item.get("plan_fingerprint_sha256") != baseline["plan_fingerprint_sha256"]
    )
    completed_epoch_ms = time.time_ns() // 1_000_000
    summary = {
        "schema_version": 1, "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
        "method": "brute_force", "run_id": run_id, "repeat_id": repeat_id,
        "status": "complete",
        "meta": {
            "run_id": run_id, "exp_id": run_id, "benchmark": workload,
            "workload_variant": benchmark["workloads"][workload]["formal_name"],
            "db": "mysql80", "db_version": template_manifest["database_version"],
            "engine": ENGINE, "method": "brute_force", "bottleneck": scenario,
            "bottleneck_target": None if scenario == "none" else 0.85,
            "repeat_id": repeat_id,
            "workload_seed": measurement["seeds"]["workload_seed"],
            "method_seed": measurement["seeds"]["method_seed"],
            "point_order_seed": measurement["seeds"]["point_order_seed"],
            "benchmark_config_sha256": sha256_file(benchmark_config_path),
            "database_template_sha256": sha256_file(template_manifest_path),
            "fixed_db_settings_sha256": sha256_file(SHARED_FROZEN_ROOT / "fixed_db_settings.json"),
            "search_space_sha256": sha256_file(SHARED_FROZEN_ROOT / "search_spaces.json"),
            "method_manifest_sha256": sha256_file(method_manifest_path),
            "acceptance_policy_sha256": sha256_file(SHARED_FROZEN_ROOT / "acceptance_policy.json"),
            "resource_awareness_config_path": str(RESOURCE_CONTRACT),
            "resource_awareness_sha256": RESOURCE_AWARENESS_SHA256,
            "resource_metrics_source": "docker_cgroup_runtime", "cgroup_version": "v1",
            "machine_spec": machine["resource_profiles"][scenario],
            "parallelism": {
                "client_concurrency": (
                    benchmark["workloads"][workload].get("engine_terminals", {}).get("mysql")
                    or benchmark["workloads"][workload].get("clients")
                    or benchmark["workloads"][workload].get("terminals")
                ),
                "mysql_query_execution_dop": 1,
            },
            "ordinal": schedule_entry["ordinal"], "whole_run_attempt_id": whole_run_attempt_id,
        },
        "baseline": {
            "params": default_params,
            "plan_fingerprints": sorted({item["plan_fingerprint_sha256"] for item in default_replays}),
            "validation": default_summary,
            "qps": default_summary["metrics"]["qps"]["mean"],
            "xap_qps": default_summary["metrics"]["xap_qps"]["mean"],
            "xtp_tps": default_summary["metrics"]["xtp_tps"]["mean"],
            "latency_p95_ms": default_summary["metrics"]["latency_p95_ms"]["mean"],
            "latency_p99_ms": default_summary["metrics"]["latency_p99_ms"]["mean"],
            "resource": {
                "cpu": default_summary["metrics"]["cpu_utilization_ratio"]["mean"],
                "mem": default_summary["metrics"]["memory_utilization_ratio"]["mean"],
                "io": default_summary["metrics"]["io_utilization_ratio"]["mean"],
            },
            "utility": 0.0,
            "stable": default_summary["all_measurement_valid"],
            "measurement_seconds": [item["sampling"]["measurement_seconds"] for item in default_replays],
        },
        "outer_loop": outer_loop,
        "inner_loop_summary": {
            "total_iterations": 0, "accepted_iterations": 0, "rejected_iterations": 0,
            "wall_time_ms": 0, "best_estimated_utility": None, "best_params": None,
            "iteration_log_glob": "round_*/attempt_*/inner_iterations.jsonl",
        },
        "final_validation": final_summary,
        "result": {
            "status": "complete", "final_params": final_params,
            "final_selected_candidate_index": final_index,
            "final_selected_candidate_id": "P0" if final_index == 0 else f"C{final_index:02d}",
            "final_plan_fingerprints": sorted({item["plan_fingerprint_sha256"] for item in final_replays}),
            "final_utility_relative_default": final_utility,
            "delta_u_pct": 100 * (final_primary / default_primary - 1) if default_primary > 0 else None,
            "qps_before": default_summary["metrics"]["qps"]["mean"],
            "qps_after": final_summary["metrics"]["qps"]["mean"],
            "xap_qps_before": default_summary["metrics"]["xap_qps"]["mean"],
            "xap_qps_after": final_summary["metrics"]["xap_qps"]["mean"],
            "xtp_tps_before": default_summary["metrics"]["xtp_tps"]["mean"],
            "xtp_tps_after": final_summary["metrics"]["xtp_tps"]["mean"],
            "p95_before_ms": default_summary["metrics"]["latency_p95_ms"]["mean"],
            "p95_after_ms": final_summary["metrics"]["latency_p95_ms"]["mean"],
            "p99_before_ms": default_summary["metrics"]["latency_p99_ms"]["mean"],
            "p99_after_ms": final_summary["metrics"]["latency_p99_ms"]["mean"],
            "target_resource_before": target_before, "target_resource_after": target_after,
            "bad_config": failed_or_invalid > 0,
            "bad_config_rate": failed_or_invalid / 10,
            "outer_accept_count": len(accepted_points),
            "outer_reject_count": 10 - len(accepted_points),
            "plan_change_rate": plan_changes / 10,
            "converge_outer_iters": 10,
            "total_real_replays": len(actual_attempts),
            "total_wall_time_s": (completed_epoch_ms - started_epoch_ms) / 1000.0,
            "default_validation_statistics": default_summary,
            "final_validation_statistics": final_summary,
        },
        "completed_at_utc": utc_now(),
    }
    _write_immutable(result_path, _json_bytes(summary))
    _run_checksums(run_root)
    atomic_json(run_root / "run_completion.json", {
        "schema_version": 1, "status": "complete", "completed_at_utc": utc_now(),
        "run_summary_sha256": sha256_file(result_path),
    })
    atomic_json(run_root / "run_attempts" / f"completion_{whole_run_attempt_id:02d}.json", {
        "schema_version": 1, "status": "complete", "completed_at_utc": utc_now(),
        "run_summary_sha256": sha256_file(result_path),
    })
    # Recompute once so run_completion itself is covered without checksum self-reference.
    _run_checksums(run_root)
    return summary, result_path


def _target_schedule_entries() -> list[dict[str, Any]]:
    _, by_ordinal, _ = load_schedule()
    entries = [
        entry for _, entry in sorted(by_ordinal.items())
        if entry["session_id"] == SESSION_ID
    ]
    expected = {
        ("A5", 1), ("A6", 1), ("A7", 1), ("A8", 1),
        ("B13", 2), ("B13", 3), ("B14", 2), ("B14", 3),
        ("B15", 2), ("B15", 3),
        ("E5", 1), ("E6", 1), ("E7", 1), ("E8", 1),
    }
    actual = {(entry["run_id"], int(entry["repeat_id"])) for entry in entries}
    if len(entries) != 14 or actual != expected:
        raise GateError(f"MySQL brute-force target schedule mismatch: {entries}")
    return entries


def _campaign_manifest(entries: Sequence[Mapping[str, Any]]) -> Path:
    path = GLOBAL_ROOT / "mysql/campaign_manifest.json"
    if path.is_file():
        existing = load_json(path)
        if (
            existing.get("freeze_id") != FREEZE_ID
            or existing.get("session_id") != SESSION_ID
            or existing.get("method") != "brute_force"
            or existing.get("target_run_count") != 14
            or existing.get("target_entries") != [dict(item) for item in entries]
        ):
            raise GateError(f"existing campaign manifest differs from frozen session registration: {path}")
        return path
    payload = {
        "schema_version": 1, "freeze_id": FREEZE_ID,
        "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
        "session_id": SESSION_ID, "method": "brute_force",
        "status": "registered", "registered_at_utc": utc_now(),
        "target_run_count": 14,
        "target_entries": [dict(item) for item in entries],
        "checksums_sha256": sha256_file(SHARED_FROZEN_ROOT / "checksums.sha256"),
        "point_schedule_sha256": sha256_file(SHARED_FROZEN_ROOT / "point_schedule.json"),
        "resource_awareness_path": str(RESOURCE_CONTRACT),
        "resource_awareness_sha256": RESOURCE_AWARENESS_SHA256,
        "runner_source": str(Path(__file__).resolve()),
        "runner_source_sha256": sha256_file(Path(__file__).resolve()),
    }
    _write_immutable(path, _json_bytes(payload))
    return path


def wait_for_engine_ready(engine: str, poll_seconds: int = 15) -> dict[str, Any]:
    path = GLOBAL_ROOT / "shared" / ("PG_ENGINE_READY.json" if engine == "postgresql" else "MYSQL_ENGINE_READY.json")
    while not path.exists():
        log("waiting_for_engine_ready", engine=engine, path=str(path))
        time.sleep(poll_seconds)
    return validate_engine_ready(engine)


def execute_session() -> dict[str, Any]:
    validate_shared_bundle()
    run_engine_gate()
    wait_for_engine_ready("postgresql")
    validate_all_ready()
    entries = _target_schedule_entries()
    _campaign_manifest(entries)
    target_ordinals = {int(item["ordinal"]) for item in entries}
    while True:
        with host_measurement_lock():
            state = validate_scheduler_state()
            completed_targets = [
                item for item in state["completed_entries"] if int(item["ordinal"]) in target_ordinals
            ]
            if len(completed_targets) == 14:
                result = {
                    "schema_version": 1, "freeze_id": FREEZE_ID,
                    "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
                    "session_id": SESSION_ID, "method": "brute_force",
                    "status": "complete", "completed_run_count": 14,
                    "completed_ordinals": sorted(int(item["ordinal"]) for item in completed_targets),
                    "completed_at_utc": utc_now(),
                }
                atomic_json(GLOBAL_ROOT / "mysql/brute_force/session_summary.json", result)
                return result
            next_ordinal = int(state["next_ordinal"])
            active = state["active"]
            _, by_ordinal, _ = load_schedule()
            if next_ordinal == 85:
                raise GateError(
                    f"global scheduler completed without all MySQL brute-force targets: {len(completed_targets)}/14"
                )
            expected = by_ordinal[next_ordinal]
            active_session = active["session_id"] if isinstance(active, Mapping) else None
            if expected["session_id"] != SESSION_ID or (active_session is not None and active_session != SESSION_ID):
                log(
                    "scheduler_not_this_session", next_ordinal=next_ordinal,
                    expected_session_id=expected["session_id"], active_session_id=active_session,
                    completed_targets=len(completed_targets),
                )
            else:
                run_id = str(expected["run_id"])
                repeat_id = int(expected["repeat_id"])
                run_root = GLOBAL_ROOT / "mysql/brute_force" / f"{run_id}_r{repeat_id}"
                run_root.mkdir(parents=True, exist_ok=True)
                whole_attempt = _next_run_attempt_id(run_root)
                claim = claim_scheduler_entry(SESSION_ID, whole_attempt)
                if claim["status"] not in {"claimed", "resumed"}:
                    raise GateError(f"unexpected scheduler claim result for own entry: {claim}")
                log(
                    "complete_run_started", ordinal=next_ordinal, run_id=run_id,
                    repeat_id=repeat_id, whole_run_attempt_id=whole_attempt,
                )
                summary, result_path = execute_complete_run(expected, whole_attempt)
                if summary.get("status") != "complete":
                    raise GateError(f"whole optimization run is not complete: {result_path}")
                relative = result_path.relative_to(GLOBAL_ROOT).as_posix()
                completion = complete_scheduler_entry(SESSION_ID, relative)
                log(
                    "complete_run_scheduler_advanced", ordinal=next_ordinal,
                    run_id=run_id, repeat_id=repeat_id,
                    next_ordinal=completion["next_ordinal"],
                )
        time.sleep(15)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=(
            "prepare-benchmark-configs", "run-engine-gate", "execute-session",
            "print-brute-candidates", "validate-all-ready",
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "prepare-benchmark-configs":
            result = prepare_benchmark_configs()
        elif args.command == "run-engine-gate":
            result = run_engine_gate()
        elif args.command == "execute-session":
            result = execute_session()
        elif args.command == "print-brute-candidates":
            _, _, _, _, _, search, methods, _ = _configs()
            result = {"status": "complete", "candidates": brute_force_candidates(search, methods)}
        elif args.command == "validate-all-ready":
            result = validate_all_ready()
        else:  # pragma: no cover
            raise GateError(f"unknown command: {args.command}")
        print("FORMAL_MYSQL_CAMPAIGN=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (GateError, ResourceError, ProtocolError, OSError, ValueError) as exc:
        blocker_root = PREFLIGHT_ROOT / "blockers"
        blocker_root.mkdir(parents=True, exist_ok=True)
        target = blocker_root / f"blocker_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}_{os.getpid()}.json"
        atomic_json(target, {
            "schema_version": 1, "freeze_id": FREEZE_ID,
            "global_campaign_id": GLOBAL_CAMPAIGN_ID, "engine": ENGINE,
            "session_id": SESSION_ID, "status": "blocked_attempt",
            "failed_at_utc": utc_now(), "error_type": type(exc).__name__, "error": str(exc),
        })
        print(f"FORMAL_MYSQL_BLOCKER={type(exc).__name__}:{exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
