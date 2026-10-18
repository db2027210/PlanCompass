#!/usr/bin/env python3
"""Extract exact AP executable SQL/bindings from a production MySQL general log."""

from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib
from typing import Any


START = re.compile(r"^(\S+Z)\s+(\d+)\s+(\w+)\t(.*)$")
LITERAL = r"(-?\d+(?:\.\d+)?|'(?:''|[^'])*'|NULL)"
TYPE_CONTRACT = {
    "AP-1": ["INT"] * 4, "AP-2": ["INT"], "AP-2-RAND": ["INT"],
    "AP-3": ["INT"] * 2, "AP-4": ["INT"] * 2, "AP-5": ["INT"],
    "AP-6": [], "AP-7": ["INT"] * 4, "AP-8": [], "AP-9": [],
    "AP-10": [], "AP-11": [], "AP-12": ["STRING"], "AP-13": ["INT"],
}


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).strip()


def template_pattern(sql: str) -> re.Pattern[str]:
    escaped = re.escape(normalize(sql)).replace(r"\?", LITERAL).replace(r"\ ", r"\s+")
    return re.compile(r"^" + escaped + r"$", re.IGNORECASE)


def parse_literal(value: str, jdbc_type: str) -> Any:
    if value.upper() == "NULL":
        return None
    if jdbc_type == "INT":
        return int(value)
    if not (value.startswith("'") and value.endswith("'")):
        raise ValueError(f"STRING literal is not quoted: {value}")
    return value[1:-1].replace("''", "'")


def write_json(path: Path, value: Any) -> str:
    payload = canonical_bytes(value)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.run_root.resolve()
    log_path = root / "artifacts/production_general.log"
    props_path = root / "production.props"
    toml_path = root / "stmt_mysql.toml"
    props = dict(line.split("=", 1) for line in props_path.read_text().splitlines() if "=" in line)
    if props.get("random_seed") != "4508333" or props.get("apclient") != "4":
        raise ValueError("production seed/client contract mismatch")
    if props.get("ap_query_sequence") != ",".join(str(i) for i in range(1, 14)) \
            or props.get("ap_query_sequence_limit") != "13":
        raise ValueError("production AP sequence contract mismatch")

    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in log_path.read_text(encoding="utf-8").splitlines():
        match = START.match(line)
        if match:
            if current is not None:
                entries.append(current)
            current = {
                "timestamp_utc": match.group(1), "connection_id": int(match.group(2)),
                "command": match.group(3), "sql": match.group(4),
            }
        elif current is not None:
            current["sql"] += "\n" + line
    if current is not None:
        entries.append(current)

    templates = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    template_sql = {f"AP-{i}": templates[f"AP-{i}"]["sql"] for i in range(1, 14)}
    template_sql["AP-2-RAND"] = templates["AP-2"]["1"]["sql"]
    patterns = {query_id: template_pattern(sql) for query_id, sql in template_sql.items()}
    workload: list[dict[str, Any]] = []
    for entry in entries:
        if entry["command"] != "Query":
            continue
        sql = normalize(entry["sql"])
        matches = [(query_id, pattern.fullmatch(sql)) for query_id, pattern in patterns.items()]
        matches = [(query_id, match) for query_id, match in matches if match is not None]
        if not matches:
            continue
        if len(matches) != 1:
            raise ValueError(f"ambiguous AP SQL: {sql[:80]}")
        query_id, match = matches[0]
        types = TYPE_CONTRACT[query_id]
        if len(match.groups()) != len(types):
            raise ValueError(f"setter arity mismatch: {query_id}")
        bindings = [{
            "jdbc_type": jdbc_type,
            "position": position,
            "value": parse_literal(raw, jdbc_type),
        } for position, (raw, jdbc_type) in enumerate(zip(match.groups(), types), 1)]
        workload.append({
            "actual_bindings": bindings,
            "binding_type_authority": "frozen_production_source_JDBC_setter_contract",
            "connection_id": entry["connection_id"],
            "executable_sql": sql,
            "executable_sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            "global_seed_from_runtime_props": 4508333,
            "query_id": query_id,
            "schema_version": "mysql_server_observed_ap_binding_event_v1",
            "server_observation_authority": "MySQL_8.0_general_log_Query_argument",
            "template_sql_sha256": hashlib.sha256(normalize(template_sql[query_id]).encode()).hexdigest(),
            "timestamp_utc": entry["timestamp_utc"],
        })

    counts = Counter(row["query_id"] for row in workload)
    if set(counts) != set(TYPE_CONTRACT) or set(counts.values()) != {4} or len(workload) != 56:
        raise ValueError(f"AP event coverage mismatch: {dict(counts)}")
    by_connection: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in workload:
        by_connection[row["connection_id"]].append(row)
    if len(by_connection) != 4:
        raise ValueError(f"expected four AP DB connections, got {sorted(by_connection)}")
    for connection, rows in by_connection.items():
        main = [row["query_id"] for row in rows if row["query_id"] != "AP-2-RAND"]
        if len(main) != 13 or set(main) != {f"AP-{i}" for i in range(1, 14)}:
            raise ValueError(f"connection {connection} lacks AP1-AP13 exactly once")
        rand_index = next(i for i, row in enumerate(rows) if row["query_id"] == "AP-2-RAND")
        if rand_index + 1 >= len(rows) or rows[rand_index + 1]["query_id"] != "AP-2":
            raise ValueError(f"connection {connection} AP-2 selector/main are not adjacent")
        selected = rows[rand_index + 1]["actual_bindings"][0]["value"]
        rows[rand_index]["database_rand_result_observed_in_adjacent_ap2_binding"] = selected
        for sequence, row in enumerate(rows, 1):
            row["connection_sequence"] = sequence

    workload.sort(key=lambda row: (row["connection_id"], row["connection_sequence"]))
    trace_path = root / "artifacts/production_server_binding_trace.jsonl"
    trace_path.write_bytes(b"".join(canonical_bytes(row) for row in workload))
    trace_sha = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    trace_path.with_name(trace_path.name + ".sha256").write_text(
        f"{trace_sha}  {trace_path.name}\n", encoding="ascii"
    )
    sql_dir = root / "artifacts/explain_sql"
    sql_dir.mkdir(exist_ok=True)
    for index, row in enumerate(workload, 1):
        (sql_dir / f"{index:02d}_{row['connection_id']}_{row['connection_sequence']:02d}_{row['query_id']}.sql").write_text(
            row["executable_sql"] + ";\n", encoding="utf-8"
        )
    manifest = {
        "event_count": len(workload),
        "event_schema_version": "mysql_server_observed_ap_binding_event_v1",
        "events_sha256": trace_sha,
        "exact_frozen_production_jar_sha256": "6e615d1b704da0b095266fd5592fc72245a9c5034707a37ef91abfa65a7fc7f4",
        "generic_custom_plan_semantics_claimed": False,
        "global_seed": 4508333,
        "jdbc_types_observed_by_server": False,
        "jdbc_types_verified_from_frozen_source_contract": True,
        "main_query_count": 52,
        "mysql_dynamic_plan_cluster_deletion_authorized": False,
        "query_counts": dict(sorted(counts.items())),
        "raw_explain_status": "PENDING",
        "schema_version": "mysql_server_observed_ap_binding_manifest_v1",
        "server_connection_ids": sorted(by_connection),
        "status": "BINDING_VALUES_COMPLETE_STRICT_PLAN_FAIL_OPEN_PENDING_RAW_EXPLAIN",
    }
    manifest_sha = write_json(root / "artifacts/production_server_binding_manifest.json", manifest)
    print(json.dumps({"manifest_sha256": manifest_sha, "status": "PASS", "trace_sha256": trace_sha}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
