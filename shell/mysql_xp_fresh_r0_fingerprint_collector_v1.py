#!/usr/bin/env python3
"""Collect a sealed, same-live XP fingerprint concurrently with fresh R0."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time


DOCKER = ["docker", "--host", "unix:///run/ra-docker-mysql-xp-v2.sock"]
MYSQL = "/usr/bin/mysql"
DEFAULTS = "/run/mysql-xp-private/mysql-client.cnf"
PORT = 65104
COUNTER_NAMES = (
    "Created_tmp_disk_tables", "Created_tmp_tables", "Handler_read_first",
    "Handler_read_key", "Handler_read_last", "Handler_read_next",
    "Handler_read_prev", "Handler_read_rnd", "Handler_read_rnd_next",
    "Handler_write", "Innodb_buffer_pool_read_requests",
    "Innodb_buffer_pool_reads", "Innodb_pages_written",
)
COUNTER_SQL = (
    "SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema.global_status "
    "WHERE VARIABLE_NAME IN ("
    + ",".join("'%s'" % value for value in COUNTER_NAMES)
    + ") UNION ALL SELECT 'PFS_FILE_IO_WAIT_PS',"
      "CAST(COALESCE(SUM(SUM_TIMER_WAIT),0) AS CHAR) "
      "FROM performance_schema.file_summary_by_instance ORDER BY 1"
)
PARAMETER_SQL = (
    "SELECT CONCAT(COST_NAME,'=',COST_VALUE) FROM mysql.server_cost "
    "WHERE COST_NAME IN ('disk_temptable_create_cost','disk_temptable_row_cost',"
    "'key_compare_cost','memory_temptable_create_cost','memory_temptable_row_cost',"
    "'row_evaluate_cost') UNION ALL SELECT CONCAT(COST_NAME,'=',COST_VALUE) "
    "FROM mysql.engine_cost WHERE COST_NAME IN "
    "('io_block_read_cost','memory_block_read_cost') ORDER BY 1"
)


class CollectorError(RuntimeError):
    pass


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha(value) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sealed(path: Path, label: str) -> dict:
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.is_symlink() or not path.is_file() or sidecar.is_symlink() or not sidecar.is_file():
        raise CollectorError(label + " is not sealed")
    if sidecar.read_text(encoding="ascii") != "%s  %s\n" % (sha(path), path.name):
        raise CollectorError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CollectorError(label + " is not an object")
    return value


def atomic(path: Path, value: dict) -> None:
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise CollectorError("fresh output required")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    temporary = path.with_name("." + path.name + ".%d" % os.getpid())
    with temporary.open("xb") as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    Path(str(path) + ".sha256").write_text(
        "%s  %s\n" % (sha(path), path.name), encoding="ascii"
    )


def mysql_argv(client: str, sql: str) -> list[str]:
    if not isinstance(client, str) or len(client) != 64 or any(
        char not in "0123456789abcdef" for char in client
    ):
        raise CollectorError("full client container ID is required")
    return DOCKER + [
        "exec", "--user", "1008:1008", client, MYSQL,
        "--defaults-extra-file=" + DEFAULTS,
        "--host=127.0.0.1", "--port=%d" % PORT, "--protocol=TCP",
        "--batch", "--raw", "--skip-column-names", "--execute", sql,
    ]


def query(client: str, sql: str, timeout: int = 15) -> str:
    completed = subprocess.run(
        mysql_argv(client, sql), text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=timeout, check=False,
    )
    if completed.returncode or not completed.stdout.strip():
        raise CollectorError("sealed client SQL collector failed")
    return completed.stdout


def counters(client: str) -> dict[str, int]:
    rows = {}
    for line in query(client, COUNTER_SQL).splitlines():
        fields = line.split("\t", 1)
        if len(fields) != 2 or fields[0] in rows:
            raise CollectorError("counter output differs")
        try:
            rows[fields[0]] = int(fields[1])
        except ValueError as exc:
            raise CollectorError("counter is not integral") from exc
    required = set(COUNTER_NAMES) | {"PFS_FILE_IO_WAIT_PS"}
    if set(rows) != required or any(value < 0 for value in rows.values()):
        raise CollectorError("counter set differs")
    return rows


def trace_sql(registry: dict) -> list[str]:
    trace = Path(str(registry.get("trace_path") or ""))
    if trace.is_symlink() or not trace.is_file() or sha(trace) != registry.get("trace_sha256"):
        raise CollectorError("registry trace differs")
    statements = set()
    for line in trace.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        sql = row.get("canonical_executable_sql") if isinstance(row, dict) else None
        if row.get("event_type") == "statement" and row.get("outcome") == "success" and isinstance(sql, str):
            statements.add(" ".join(sql.split()))
    if not statements:
        raise CollectorError("real trace has no executable SQL")
    return sorted(statements)


def walk_plan(value, l1, l2) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"nested_loop", "table", "grouping_operation", "ordering_operation",
                       "duplicates_removal", "attached_subqueries", "union_result"}:
                l1["operator_types"].add(key)
            if key == "table" and isinstance(item, dict):
                name = item.get("table_name")
                access = item.get("access_type")
                if isinstance(name, str): l1["join_order"].append(name)
                if isinstance(access, str): l1["scan_types"].add(access)
                for metric in ("rows_examined_per_scan", "rows_produced_per_join"):
                    raw = item.get(metric)
                    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= 0:
                        l2["intermediate_result_rows"] += float(raw)
            if key == "using_temporary_table" and item is True:
                l2["temporary_table_count"] += 1
            if key == "using_filesort" and item is True:
                l2["sort_or_spill_count"] += 1
            if key == "using_join_buffer" and "hash" in str(item).lower():
                l2["hash_batch_count"] += 1
            walk_plan(item, l1, l2)
    elif isinstance(value, list):
        for item in value: walk_plan(item, l1, l2)


def plans(client: str, statements: list[str]) -> tuple[dict, dict]:
    l1 = {"operator_types": set(), "join_order": [], "scan_types": set(),
          "parallel_structure": {"workers": 0, "mysql_parallel_operator_seen": False}}
    l2 = {"workers": 0, "hash_batch_count": 0, "sort_or_spill_count": 0,
          "bitmap_lossy_pages": 0, "intermediate_result_rows": 0.0,
          "temporary_table_count": 0, "explain_statement_count": 0}
    for sql in statements:
        raw = query(client, "EXPLAIN FORMAT=JSON " + sql)
        try:
            plan = json.loads(raw.strip())
        except json.JSONDecodeError as exc:
            raise CollectorError("EXPLAIN JSON differs") from exc
        walk_plan(plan, l1, l2); l2["explain_statement_count"] += 1
    if not l1["operator_types"] or not l1["scan_types"]:
        raise CollectorError("L1 plan evidence is incomplete")
    l1["operator_types"] = sorted(l1["operator_types"])
    l1["scan_types"] = sorted(l1["scan_types"])
    return l1, l2


def delta(before: dict, after: dict, names) -> int:
    value = sum(after[name] - before[name] for name in names)
    if value < 0: raise CollectorError("counter regressed")
    return value


def collect(args) -> dict:
    bundle = Path(args.bundle).resolve(strict=True)
    manifest = sealed(bundle / "bundle_manifest.json", "bundle manifest")
    adoption = sealed(args.adopted_service, "adopted service")
    registry = sealed(args.trace_registry, "trace registry")
    self_relative = "launch/mysql_xp_fresh_r0_fingerprint_collector_v1.py"
    self_path = bundle / self_relative
    identity = adoption.get("same_live_identity") or {}
    client = identity.get("client_container_id")
    if (
        manifest.get("bundle_id") != bundle.name
        or manifest.get("files", {}).get(self_relative) != sha(self_path)
        or adoption.get("bundle_id") != bundle.name
        or registry.get("bundle_id") != bundle.name
        or registry.get("same_live_identity") != identity
        or registry.get("status") != "SEALED_REAL_TRACE"
    ):
        raise CollectorError("bundle/adoption/registry binding differs")
    statements = trace_sql(registry)
    before = counters(client)
    parameters = sorted(line for line in query(client, PARAMETER_SQL).splitlines() if line)
    if len(parameters) < 7:
        raise CollectorError("parameter signature evidence is incomplete")
    l1, l2 = plans(client, statements)
    atomic(Path(args.ready), {
        "schema_version": "mysql_xp_fresh_r0_fingerprint_ready_v1",
        "status": "READY", "bundle_id": bundle.name,
        "producer_sha256": sha(self_path), "client_container_id": client,
        "counter_sql_sha256": hashlib.sha256(COUNTER_SQL.encode()).hexdigest(),
        "qps_resource_input_present": False,
    })
    stop = Path(args.stop)
    deadline = time.monotonic() + 600
    while not stop.is_file():
        if stop.is_symlink() or time.monotonic() >= deadline:
            raise CollectorError("sealed concurrent collector stop timed out")
        time.sleep(0.1)
    after = counters(client)
    handler_reads = tuple(name for name in COUNTER_NAMES if name.startswith("Handler_read_"))
    l3 = {
        "shared_read_blocks": delta(before, after, ("Innodb_buffer_pool_read_requests",)),
        "shared_write_blocks": delta(before, after, ("Innodb_pages_written",)),
        "local_read_blocks": delta(before, after, handler_reads),
        "local_write_blocks": delta(before, after, ("Handler_write",)),
        "temp_read_blocks": delta(before, after, ("Created_tmp_disk_tables",)),
        "temp_write_blocks": delta(before, after, ("Created_tmp_tables",)),
        "io_wait_ms": delta(before, after, ("PFS_FILE_IO_WAIT_PS",)) / 1_000_000_000.0,
    }
    return {
        "schema_version": "mysql_xp_fresh_r0_raw_fingerprint_v1",
        "status": "PASS_CONCURRENT_RAW_OBSERVATION", "bundle_id": bundle.name,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "producer_relative_path": self_relative, "producer_sha256": sha(self_path),
        "adopted_service_sha256": sha(Path(args.adopted_service)),
        "trace_registry_sha256": sha(Path(args.trace_registry)),
        "same_live_identity": identity, "client_container_id": client,
        "counter_sql": COUNTER_SQL, "counter_sql_sha256": hashlib.sha256(COUNTER_SQL.encode()).hexdigest(),
        "counter_transport": "exact_adopted_client_docker_exec_stdout_v2",
        "defaults_file_in_client": DEFAULTS, "mysql_port": PORT,
        "qps_resource_input_present": False,
        "raw_before_sha256": canonical_sha(before), "raw_after_sha256": canonical_sha(after),
        "fingerprint": {"parameter_signature": canonical_sha(parameters),
                        "l1": l1, "l2": l2, "l3": l3},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True); parser.add_argument("--adopted-service", required=True)
    parser.add_argument("--trace-registry", required=True); parser.add_argument("--ready", required=True)
    parser.add_argument("--stop", required=True); parser.add_argument("--output", required=True)
    args = parser.parse_args()
    atomic(Path(args.output), collect(args)); return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (CollectorError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit("FAIL_CLOSED: " + str(exc))
