#!/usr/bin/env python3
"""Run the bounded offline CPU C04 Unique Phase A on PostgreSQL worker w1."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hybenchxp_memory_v86_newop_batch_probe import plan_meta, strip_sql


REPO = Path("/home/anonymous/program/resource_adaptive")
CONFIG_PATH = REPO / "config/binary_plan_tuning_hybenchxp_cpu_c04_unique_phase_a_20260715.json"
RUN_DIR = Path(
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp/runs/"
    "hyxp_cpu_c04_unique_phase_a_20260715"
)
RESULT_PATH = RUN_DIR / "phase_a_result.json"
DECISION_PATH = REPO / "report/bonect/phase_a_eligibility_decision_cpu_c04_unique.json"
REPORT_JSON_PATH = REPO / "report/bonect/hybenchxp_cpu_c04_unique_phase_a_20260715.json"
REPORT_MD_PATH = REPO / "report/bonect/hybenchxp_cpu_c04_unique_phase_a_20260715.md"
STATUS_PATH = REPO / ".agents/codex-hybenchxp-cpu-c04-status.md"
PARSER_PATH = REPO / "shell/hybenchxp_memory_v86_newop_batch_probe.py"
SESSION_NAME = "hyxp_cpu_c04_unique_phase_a_20260715"
APP_NAME = "hyxp_cpu_c04_unique_phase_a_20260715"
TZ = timezone(timedelta(hours=8))

SEVEN = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
SEED_NAMES = ("default", "rpc16", "coc025", "coc05", "ctc1", "ctc1_ptc3")
EXPECTED_SEEDS = {
    "default": [1, 4, 0.01, 0.005, 0.0025, 1000, 0.1],
    "rpc16": [1, 16, 0.01, 0.005, 0.0025, 1000, 0.1],
    "coc025": [1, 4, 0.01, 0.005, 0.025, 1000, 0.1],
    "coc05": [1, 4, 0.01, 0.005, 0.05, 1000, 0.1],
    "ctc1": [1, 4, 1, 0.005, 0.0025, 1000, 0.1],
    "ctc1_ptc3": [1, 4, 1, 0.005, 0.0025, 1000, 3],
}


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_event(event: str, **fields: Any) -> None:
    payload = {"timestamp": now_iso(), "event": event, **fields}
    with (RUN_DIR / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"[{payload['timestamp']}] {event} {fields}", flush=True)


def update_status(stage: str, detail: str, *, eligible: Optional[bool] = None) -> None:
    eligible_text = "pending" if eligible is None else str(eligible).lower()
    STATUS_PATH.write_text(
        "\n".join(
            [
                "# HyBenchXP CPU C04 Unique Phase A status",
                "",
                f"- Updated: {now_iso()}",
                f"- State: `{stage}`",
                "- Run id: `hyxp_cpu_c04_unique_phase_a_20260715`",
                f"- Run dir: `{RUN_DIR}`",
                f"- Session: `{SESSION_NAME}`",
                f"- PID: `{os.getpid()}` (primary runner when state is not `finalized`)",
                "- Worker: `binary_plan_tuning_hybenchxp_cpu_mem_w1`, `127.0.0.1:56433`, "
                "`hybench_sf10x`",
                f"- Current phase: {detail}",
                "- Scope: six EXPLAIN-only map cells; answer gate; one equal warmup pair; "
                "three interleaved measured pairs",
                "- Forbidden: HyBench, core tuner, runtime baseline/candidate/full8, Docker changes, "
                "foreign process control, closed-family ANALYZE, Grok",
                "- Stop policy: fail closed on conflict, timeout/error, semantic/collision mismatch, "
                "answer mismatch, speed failure, co-role failure, or cleanup/restoration failure",
                f"- Final eligible: `{eligible_text}`",
                "",
            ]
        ),
        encoding="utf-8",
    )


def load_and_validate_config() -> Tuple[Dict[str, Any], Dict[str, Dict[str, float]]]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config.get("run_id") != RUN_DIR.name:
        raise RuntimeError("run_id/run directory mismatch")
    if config.get("host") != "127.0.0.1" or int(config.get("port", 0)) != 56433:
        raise RuntimeError("worker endpoint must be exactly 127.0.0.1:56433")
    if config.get("container") != "binary_plan_tuning_hybenchxp_cpu_mem_w1":
        raise RuntimeError("container must be task-owned CPU worker w1")
    if config.get("database") != "hybench_sf10x":
        raise RuntimeError("database must be hybench_sf10x")
    if tuple(config.get("candidate_keys") or ()) != SEVEN:
        raise RuntimeError("candidate keys or key order differ from exact seven")
    seeds = config.get("seeds") or {}
    if tuple(seeds) != SEED_NAMES or seeds != EXPECTED_SEEDS:
        raise RuntimeError("seed names, order, count, or exact values differ from the bounded six")
    fixed = config.get("fixed_settings") or {}
    overlap = set(fixed).intersection(SEVEN)
    if overlap:
        raise RuntimeError(f"fixed settings overlap seven-cost keys: {sorted(overlap)}")
    if config.get("runtime_allowed") is not False or config.get("hybench_allowed") is not False:
        raise RuntimeError("runtime and HyBench must remain disabled")
    expected_order = [["default", "candidate"], ["candidate", "default"], ["default", "candidate"]]
    if (config.get("sampling") or {}).get("measured_order") != expected_order:
        raise RuntimeError("paired order must be the fixed three-group interleaving")
    vectors = {
        name: {key: float(value) for key, value in zip(SEVEN, seeds[name])}
        for name in SEED_NAMES
    }
    return config, vectors


def process_output(args: Sequence[str], *, timeout: int = 30) -> str:
    result = subprocess.run(
        list(args), capture_output=True, text=True, check=False, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed rc={result.returncode}: {' '.join(args)}: "
            f"{result.stderr.strip()[:1200]}"
        )
    return result.stdout.strip()


def psql(config: Dict[str, Any], body: str, *, timeout: int) -> str:
    env = {
        **os.environ,
        "PGPASSWORD": os.environ.get("PGPASSWORD", "postgres"),
        "PGAPPNAME": APP_NAME,
    }
    result = subprocess.run(
        [
            "psql",
            "-h",
            str(config["host"]),
            "-p",
            str(config["port"]),
            "-U",
            str(config["user"]),
            "-d",
            str(config["database"]),
            "-XqAt",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=body,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"psql failed rc={result.returncode}: {result.stderr.strip()[:1600]}")
    return result.stdout.strip()


def psql_json(config: Dict[str, Any], query: str, *, timeout: int = 30) -> Any:
    raw = psql(config, query.rstrip().rstrip(";") + ";\n", timeout=timeout)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"expected JSON from psql, got: {raw[:1000]}") from exc


def owned_python_processes() -> List[Dict[str, Any]]:
    raw = process_output(["ps", "-eo", "pid=,ppid=,pgid=,comm=,args="])
    records: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        if "hybenchxp_cpu_c04_unique_phase_a.py" not in line:
            continue
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(.*)", line)
        if not match or not match.group(4).startswith("python"):
            continue
        records.append(
            {
                "pid": int(match.group(1)),
                "ppid": int(match.group(2)),
                "pgid": int(match.group(3)),
                "command": match.group(5),
            }
        )
    return records


def database_activity(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    query = """
SELECT COALESCE(jsonb_agg(to_jsonb(a) ORDER BY a.pid), '[]'::jsonb)
FROM (
    SELECT pid, usename, application_name, state, wait_event_type, wait_event,
           query_start, left(regexp_replace(query, E'[\\n\\r\\t]+', ' ', 'g'), 240) AS query
    FROM pg_stat_activity
    WHERE datname = current_database() AND pid <> pg_backend_pid()
) AS a
"""
    result = psql_json(config, query)
    if not isinstance(result, list):
        raise RuntimeError("pg_stat_activity audit did not return a list")
    return result


def settings_snapshot(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    query = """
SELECT COALESCE(jsonb_agg(to_jsonb(s) ORDER BY s.name), '[]'::jsonb)
FROM (
    SELECT name, setting, unit, source, reset_val, pending_restart
    FROM pg_settings
    ORDER BY name
) AS s
"""
    result = psql_json(config, query)
    if not isinstance(result, list):
        raise RuntimeError("pg_settings snapshot did not return a list")
    return result


def preflight(config: Dict[str, Any]) -> Dict[str, Any]:
    processes = owned_python_processes()
    conflicts = [item for item in processes if item["pid"] != os.getpid()]
    if conflicts:
        raise RuntimeError(f"conflicting task-owned C04 process exists: {conflicts}")
    inspect_raw = process_output(["docker", "inspect", str(config["container"])])
    inspect = json.loads(inspect_raw)[0]
    if not inspect.get("State", {}).get("Running"):
        raise RuntimeError("task-owned PostgreSQL worker w1 is not running")
    ports = inspect.get("NetworkSettings", {}).get("Ports", {}).get("5432/tcp") or []
    if not any(
        item.get("HostIp") == "127.0.0.1" and item.get("HostPort") == "56433"
        for item in ports
    ):
        raise RuntimeError(f"worker w1 does not own 127.0.0.1:56433: {ports}")
    identity = psql_json(
        config,
        """
SELECT jsonb_build_object(
    'database', current_database(),
    'server_version', current_setting('server_version'),
    'server_version_num', current_setting('server_version_num'),
    'server_addr', inet_server_addr()::text,
    'server_port', inet_server_port(),
    'transaction_read_only', current_setting('transaction_read_only')
)
""",
    )
    activity = database_activity(config)
    task_conflicts = [item for item in activity if item.get("application_name") == APP_NAME]
    if task_conflicts:
        raise RuntimeError(f"conflicting task-owned database session exists: {task_conflicts}")
    listeners = process_output(["ss", "-ltnp", "sport = :56433"])
    if "127.0.0.1:56433" not in listeners:
        raise RuntimeError("127.0.0.1:56433 listener is absent")
    return {
        "timestamp": now_iso(),
        "runner_pid": os.getpid(),
        "runner_pgid": os.getpgid(0),
        "session": SESSION_NAME,
        "worker_container_id": inspect.get("Id"),
        "worker_container_name": inspect.get("Name", "").lstrip("/"),
        "worker_port_binding": ports,
        "identity": identity,
        "listener": listeners,
        "task_owned_processes": processes,
        "task_owned_process_conflicts": conflicts,
        "database_activity_observed": activity,
        "task_owned_database_conflicts": task_conflicts,
        "external_activity_is_not_a_blocker": True,
        "external_activity_was_not_modified": True,
    }


def render_value(value: Any) -> str:
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def local_settings_sql(
    config: Dict[str, Any], params: Dict[str, float], *, statement_timeout: str
) -> str:
    statements = []
    for key in SEVEN:
        statements.append(f"SET LOCAL {key} = {render_value(params[key])};")
    for key, value in config["fixed_settings"].items():
        if not re.fullmatch(r"[a-z_]+", key):
            raise RuntimeError(f"unsafe GUC name: {key}")
        statements.append(f"SET LOCAL {key} = {render_value(value)};")
    statements.append(f"SET LOCAL statement_timeout = {render_value(statement_timeout)};")
    return "\n".join(statements)


def transaction_sql(
    config: Dict[str, Any], params: Dict[str, float], operation: str, *, statement_timeout: str
) -> str:
    return (
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
        + local_settings_sql(config, params, statement_timeout=statement_timeout)
        + "\n"
        + operation.rstrip().rstrip(";")
        + ";\nROLLBACK;\n"
    )


def parse_explain(raw: str) -> Dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        match = re.search(r"(\[.*\])", raw, re.S)
        if not match:
            raise RuntimeError(f"no EXPLAIN JSON in output: {raw[:1000]}") from exc
        payload = json.loads(match.group(1))
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise RuntimeError("EXPLAIN JSON must contain exactly one top-level object")
    return payload[0]


def explain(
    config: Dict[str, Any],
    sql_body: str,
    params: Dict[str, float],
    *,
    analyze: bool,
) -> Dict[str, Any]:
    timeout_key = "query" if analyze else "map"
    options = (
        "ANALYZE, BUFFERS, SUMMARY, VERBOSE, FORMAT JSON"
        if analyze
        else "VERBOSE, FORMAT JSON"
    )
    operation = f"EXPLAIN ({options})\n{sql_body}"
    raw = psql(
        config,
        transaction_sql(
            config,
            params,
            operation,
            statement_timeout=config["timeouts"][f"{timeout_key}_statement"],
        ),
        timeout=int(config["timeouts"][f"{timeout_key}_process_seconds"]),
    )
    return parse_explain(raw)


def closed_family_collisions(meta: Dict[str, Any]) -> List[str]:
    counts = meta.get("counts") or {}
    strategies = meta.get("strategies") or {}
    semantics = meta.get("execution_semantics") or {}
    join_types: List[str] = []
    sort_methods: List[str] = []

    def walk(node: Dict[str, Any]) -> None:
        if node.get("Join Type"):
            join_types.append(str(node["Join Type"]))
        if node.get("Sort Method"):
            sort_methods.append(str(node["Sort Method"]))
        for child in node.get("Plans") or []:
            walk(child)

    walk(semantics)
    collisions: List[str] = []
    if counts.get("Nested Loop", 0) and (
        counts.get("Bitmap Heap Scan", 0) or counts.get("Bitmap Index Scan", 0)
    ):
        collisions.append("lowseq_NL_Bitmap")
    if counts.get("Merge Join", 0) and not (
        counts.get("Gather", 0) or counts.get("Gather Merge", 0)
    ):
        collisions.append("serial_MergeJoin")
    if any(value in {"Semi", "Anti", "Right Anti", "Right Semi"} for value in join_types):
        collisions.append("Semi_Anti")
    if counts.get("Incremental Sort", 0) or any("top-N" in value for value in sort_methods):
        collisions.append("C06_IncrementalSort_topN")
    if counts.get("Index Only Scan", 0):
        collisions.append("IndexOnly_cliff")
    if counts.get("SetOp", 0) or counts.get("HashSetOp", 0):
        collisions.append("SetOp")
    if int(strategies.get("Aggregate:Hashed", 0)) >= 2:
        collisions.append("nested_HashAgg_rename")
    return collisions


def analyze_only_collisions(explain_obj: Dict[str, Any]) -> List[str]:
    collisions: List[str] = []

    def walk(node: Dict[str, Any]) -> None:
        if "top-N" in str(node.get("Sort Method") or ""):
            collisions.append("C06_IncrementalSort_topN")
        for child in node.get("Plans") or []:
            walk(child)

    walk(explain_obj["Plan"])
    return sorted(set(collisions))


def main_pair_semantics(
    config: Dict[str, Any], map_by_seed: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    default = map_by_seed["default"]["meta"]
    candidate = map_by_seed["ctc1"]["meta"]
    dc = default.get("counts") or {}
    ds = default.get("strategies") or {}
    cc = candidate.get("counts") or {}
    cs = candidate.get("strategies") or {}
    gates = config["gates"]
    default_semantic = (
        int(ds.get("Aggregate:Hashed", 0)) >= 1
        and int(dc.get("Unique", 0)) == 0
        and int(dc.get("Aggregate", 0)) >= 2
    )
    candidate_semantic = (
        int(cs.get("Aggregate:Hashed", 0)) == 0
        and int(cc.get("Unique", 0)) >= int(gates["require_unique_count"])
        and int(cc.get("Gather Merge", 0)) >= int(gates["require_gather_merge_count"])
        and int(cc.get("Sort", 0)) >= int(gates["require_sort_count"])
    )
    default_fp = default["execution_fingerprint_hash"]
    candidate_fp = candidate["execution_fingerprint_hash"]
    pair = config["primary_pair"]
    collisions = {
        "default": closed_family_collisions(default),
        "candidate": closed_family_collisions(candidate),
    }
    return {
        "default_semantic_ha_distinct": default_semantic,
        "candidate_semantic_unique_gather_merge_unique_sort": candidate_semantic,
        "default_fingerprint": default_fp,
        "candidate_fingerprint": candidate_fp,
        "expected_default_fingerprint": pair["expected_default_fingerprint"],
        "expected_candidate_fingerprint": pair["expected_candidate_fingerprint"],
        "default_fingerprint_remapped": default_semantic
        and default_fp != pair["expected_default_fingerprint"],
        "candidate_fingerprint_remapped": candidate_semantic
        and candidate_fp != pair["expected_candidate_fingerprint"],
        "closed_family_collisions": collisions,
        "closed_family_clear": not collisions["default"] and not collisions["candidate"],
        "passed": default_semantic and candidate_semantic and not any(collisions.values()),
    }


def answer_digest(
    config: Dict[str, Any], sql_body: str, params: Dict[str, float]
) -> Dict[str, Any]:
    operation = (
        "COPY (\n"
        + sql_body
        + "\n) TO STDOUT WITH (FORMAT csv, NULL '__BONECT_SQL_NULL__', HEADER false)"
    )
    raw = psql(
        config,
        transaction_sql(
            config,
            params,
            operation,
            statement_timeout=config["timeouts"]["query_statement"],
        ),
        timeout=int(config["timeouts"]["query_process_seconds"]),
    )
    rows = list(csv.reader(io.StringIO(raw))) if raw else []
    if len(rows) != 1:
        raise RuntimeError(f"answer must contain exactly one complete result row, got {len(rows)}")
    if any(len(row) != 2 for row in rows):
        raise RuntimeError(f"answer must contain count(*) and sum(sourceid::numeric): {rows[:3]}")
    normalized_rows: List[List[Dict[str, Any]]] = []
    for row in rows:
        normalized_row = []
        for value in row:
            is_null = value == "__BONECT_SQL_NULL__"
            normalized_row.append({"is_null": is_null, "text": None if is_null else value})
        normalized_rows.append(normalized_row)
    canonical = {
        "columns": ["count_star", "sum_sourceid_numeric"],
        "row_count": len(normalized_rows),
        "rows": normalized_rows,
    }
    canonical_bytes = json.dumps(
        canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "complete_result_row_count": len(normalized_rows),
        "column_count": 2,
        "columns": canonical["columns"],
        "count_star": normalized_rows[0][0],
        "sum_sourceid_numeric": normalized_rows[0][1],
        "null_encoding": {
            "wire": "__BONECT_SQL_NULL__",
            "normalized": {"is_null": True, "text": None},
        },
        "normalized_sha256": sha256_bytes(canonical_bytes),
        "normalized_bytes": len(canonical_bytes),
        "canonical": canonical,
    }


def run_map(
    config: Dict[str, Any], vectors: Dict[str, Dict[str, float]], sql_body: str
) -> Dict[str, Any]:
    cells: List[Dict[str, Any]] = []
    map_dir = RUN_DIR / "map"
    map_dir.mkdir(parents=True, exist_ok=True)
    for index, seed in enumerate(SEED_NAMES, start=1):
        update_status("mapping", f"EXPLAIN-only cell {index}/6: {seed}")
        explain_obj = explain(config, sql_body, vectors[seed], analyze=False)
        raw_path = map_dir / f"{index:02d}_{seed}.json"
        write_json(raw_path, explain_obj)
        meta = plan_meta(explain_obj)
        cell = {
            "index": index,
            "seed": seed,
            "vector": [vectors[seed][key] for key in SEVEN],
            "params": vectors[seed],
            "execution_fingerprint_hash": meta["execution_fingerprint_hash"],
            "signature": meta["signature"],
            "counts": meta["counts"],
            "strategies": meta["strategies"],
            "closed_family_collisions": closed_family_collisions(meta),
            "meta": meta,
            "raw_plan_path": str(raw_path),
            "analyzed": False,
            "error": None,
        }
        cells.append(cell)
        write_json(RUN_DIR / "raw_map.json", {"cells": cells, "complete": False})
        append_event(
            "map_cell_complete",
            index=index,
            seed=seed,
            fingerprint=meta["execution_fingerprint_hash"],
        )
    groups: Dict[str, List[str]] = {}
    for cell in cells:
        groups.setdefault(cell["execution_fingerprint_hash"], []).append(cell["seed"])
    result = {
        "timestamp": now_iso(),
        "sql_path": config["sql_path"],
        "key_order": list(SEVEN),
        "raw_cell_count": len(cells),
        "cells": cells,
        "fingerprint_groups": groups,
        "execution_fingerprint_count": len(groups),
        "deduplicated_by_execution_fingerprint": True,
        "complete": len(cells) == 6,
    }
    write_json(RUN_DIR / "raw_map.json", result)
    return result


def analyze_once(
    config: Dict[str, Any],
    vectors: Dict[str, Dict[str, float]],
    sql_body: str,
    *,
    label: str,
    role: str,
) -> Dict[str, Any]:
    seed = "default" if role == "default" else "ctc1"
    explain_obj = explain(config, sql_body, vectors[seed], analyze=True)
    raw_path = RUN_DIR / "analyze" / f"{label}_{role}.json"
    write_json(raw_path, explain_obj)
    meta = plan_meta(explain_obj)
    collisions = sorted(
        set(closed_family_collisions(meta) + analyze_only_collisions(explain_obj))
    )
    return {
        "role": role,
        "seed": seed,
        "actual_ms": meta.get("actual_ms"),
        "effective_peak_mib": meta.get("effective_peak_mib"),
        "effective_peak_basis": meta.get("effective_peak_basis"),
        "execution_fingerprint_hash": meta.get("execution_fingerprint_hash"),
        "signature": meta.get("signature"),
        "counts": meta.get("counts"),
        "strategies": meta.get("strategies"),
        "memory_nodes": meta.get("memory_nodes"),
        "shared_hit_blocks": meta.get("shared_hit_blocks"),
        "shared_read_blocks": meta.get("shared_read_blocks"),
        "temp_read_blocks": meta.get("temp_read_blocks"),
        "temp_written_blocks": meta.get("temp_written_blocks"),
        "closed_family_collisions": collisions,
        "raw_plan_path": str(raw_path),
        "meta": meta,
    }


def run_paired_analyze(
    config: Dict[str, Any],
    vectors: Dict[str, Dict[str, float]],
    sql_body: str,
    map_semantics: Dict[str, Any],
) -> Dict[str, Any]:
    (RUN_DIR / "analyze").mkdir(parents=True, exist_ok=True)
    warmup: List[Dict[str, Any]] = []
    for role in ("default", "candidate"):
        update_status("warmup", f"equal warmup pair: {role}")
        sample = analyze_once(
            config, vectors, sql_body, label="warmup_01", role=role
        )
        warmup.append(sample)
        append_event("warmup_analyze_complete", role=role, actual_ms=sample["actual_ms"])
    groups: List[Dict[str, Any]] = []
    for group_index, order in enumerate(config["sampling"]["measured_order"], start=1):
        samples: Dict[str, Dict[str, Any]] = {}
        for role in order:
            update_status(
                "paired_analyze",
                f"measured group {group_index}/3, order {'/'.join(order)}, running {role}",
            )
            sample = analyze_once(
                config,
                vectors,
                sql_body,
                label=f"group_{group_index:02d}",
                role=role,
            )
            samples[role] = sample
            append_event(
                "measured_analyze_complete",
                group=group_index,
                role=role,
                actual_ms=sample["actual_ms"],
                effective_peak_mib=sample["effective_peak_mib"],
            )
        default_ms = float(samples["default"]["actual_ms"])
        candidate_ms = float(samples["candidate"]["actual_ms"])
        groups.append(
            {
                "group": group_index,
                "execution_order": order,
                "default": samples["default"],
                "candidate": samples["candidate"],
                "default_ms": default_ms,
                "candidate_ms": candidate_ms,
                "candidate_default_ratio": candidate_ms / default_ms,
            }
        )
        write_json(
            RUN_DIR / "paired_analyze.json",
            {"warmup": warmup, "groups": groups, "complete": False},
        )
    ratios = [float(item["candidate_default_ratio"]) for item in groups]
    default_ms_samples = [float(item["default_ms"]) for item in groups]
    candidate_ms_samples = [float(item["candidate_ms"]) for item in groups]
    default_peaks = [float(item["default"]["effective_peak_mib"]) for item in groups]
    candidate_peaks = [float(item["candidate"]["effective_peak_mib"]) for item in groups]
    candidate_fp = map_semantics["candidate_fingerprint"]
    default_fp = map_semantics["default_fingerprint"]
    fingerprints_stable = all(
        item["default"]["execution_fingerprint_hash"] == default_fp
        and item["candidate"]["execution_fingerprint_hash"] == candidate_fp
        for item in groups
    ) and all(
        item["execution_fingerprint_hash"]
        == (default_fp if item["role"] == "default" else candidate_fp)
        for item in warmup
    )
    collision_free = all(
        not item[role]["closed_family_collisions"]
        for item in groups
        for role in ("default", "candidate")
    ) and all(not item["closed_family_collisions"] for item in warmup)
    candidate_peak_attribution = all(
        item["candidate"]["effective_peak_basis"].startswith("node_instance:")
        and "Unique" in item["candidate"]["effective_peak_basis"]
        and "Gather Merge" in item["candidate"]["effective_peak_basis"]
        and "Sort" in item["candidate"]["effective_peak_basis"]
        and int((item["candidate"]["counts"] or {}).get("Unique", 0)) >= 2
        and int((item["candidate"]["counts"] or {}).get("Gather Merge", 0)) >= 1
        and int((item["candidate"]["counts"] or {}).get("Sort", 0)) >= 1
        for item in groups
    )
    median_default_peak = statistics.median(default_peaks)
    median_candidate_peak = statistics.median(candidate_peaks)
    result = {
        "timestamp": now_iso(),
        "warmup_pair_count": 1,
        "warmup": warmup,
        "measured_pair_count": len(groups),
        "groups": groups,
        "paired_ratios": ratios,
        "median_candidate_default_ratio": statistics.median(ratios),
        "ratio_of_median_times": statistics.median(candidate_ms_samples)
        / statistics.median(default_ms_samples),
        "default_ms_median": statistics.median(default_ms_samples),
        "candidate_ms_median": statistics.median(candidate_ms_samples),
        "default_effective_peak_mib_samples": default_peaks,
        "candidate_effective_peak_mib_samples": candidate_peaks,
        "default_effective_peak_mib_median": median_default_peak,
        "candidate_effective_peak_mib_median": median_candidate_peak,
        "candidate_minus_default_effective_peak_mib": median_candidate_peak
        - median_default_peak,
        "fingerprints_stable": fingerprints_stable,
        "closed_family_clear": collision_free,
        "candidate_peak_attributed_to_unique_sort_gather_merge": candidate_peak_attribution,
        "shared_reads_are_not_treated_as_physical_io_sweet": True,
        "complete": len(groups) == 3,
    }
    write_json(RUN_DIR / "paired_analyze.json", result)
    return result


def gate(passed: Optional[bool], evidence: str) -> Dict[str, Any]:
    return {"passed": passed, "evidence": evidence}


def build_decision(state: Dict[str, Any], *, finalized: bool) -> Dict[str, Any]:
    map_result = state.get("raw_map") or {}
    semantics = state.get("pair_semantics") or {}
    answer = state.get("answer_digest") or {}
    paired = state.get("paired_analyze") or {}
    cleanup = state.get("cleanup") or {}
    answer_equal = bool(
        answer.get("checked")
        and answer.get("equal")
        and answer.get("complete_result_row_count_equal")
    )
    speed_pass = bool(
        paired.get("complete")
        and paired.get("fingerprints_stable")
        and paired.get("closed_family_clear")
        and float(paired.get("median_candidate_default_ratio", float("inf"))) <= 0.85
    )
    co_role_pass = bool(
        paired.get("complete")
        and paired.get("candidate_peak_attributed_to_unique_sort_gather_merge")
        and float(paired.get("candidate_minus_default_effective_peak_mib", float("-inf")))
        >= 512.0
    )
    cleanup_pass = bool(
        finalized
        and cleanup.get("settings_restored")
        and cleanup.get("task_owned_database_sessions_remaining") == []
        and cleanup.get("primary_controller_process_alive") is False
        and cleanup.get("task_owned_python_processes_remaining") == []
        and cleanup.get("tmux_session_exists") is False
        and cleanup.get("controller_exit_code") == 0
    )
    gates = {
        "worker_w1_identity": gate(bool((state.get("preflight") or {}).get("identity")), "w1/56433 identity recorded"),
        "no_task_owned_conflict": gate(
            (state.get("preflight") or {}).get("task_owned_process_conflicts") == []
            and (state.get("preflight") or {}).get("task_owned_database_conflicts") == [],
            "only exact same-task conflicts block; external tasks untouched",
        ),
        "exact_six_exact_seven_scope": gate(
            map_result.get("raw_cell_count") == 6 and map_result.get("complete") is True,
            "six required vectors in fixed seven-key order",
        ),
        "map_deduplicated_by_execution_fingerprint": gate(
            bool(map_result.get("deduplicated_by_execution_fingerprint")),
            f"fingerprints={map_result.get('execution_fingerprint_count')}",
        ),
        "primary_pair_plan_semantics": gate(
            semantics.get("passed") is True,
            "default HA DISTINCT and candidate Unique/Gather Merge/Unique/Sort; hash drift only remapped semantically",
        ),
        "closed_family_collision": gate(
            semantics.get("closed_family_clear") is True and paired.get("closed_family_clear") is True,
            "main pair clear of all hard-rejected closed families in map and ANALYZE",
        ),
        "answer_completeness_and_digest": gate(
            answer_equal,
            "complete result row count and normalized SHA-256 equal; count(*) and sum(sourceid::numeric) included; NULL explicit",
        ),
        "equal_warmup_and_three_interleaved_pairs": gate(
            paired.get("warmup_pair_count") == 1 and paired.get("measured_pair_count") == 3,
            "one equal warmup pair and D/C, C/D, D/C measured order",
        ),
        "speed_median_ratio": gate(
            speed_pass,
            f"median candidate/default={paired.get('median_candidate_default_ratio')} <= 0.85",
        ),
        "same_pair_memory_co_role": gate(
            co_role_pass,
            f"candidate-default effective peak={paired.get('candidate_minus_default_effective_peak_mib')} MiB >= 512 MiB",
        ),
        "settings_and_process_cleanup": gate(
            cleanup_pass if finalized else None,
            "full pg_settings restored; primary PID/session/backend gone; finalized after controller exit",
        ),
        "report_only_stop": gate(True, "runtime/HyBench/tuner were disabled and never started"),
    }
    all_passed = all(item["passed"] is True for item in gates.values())
    fatal_error = state.get("fatal_error")
    eligible = bool(finalized and not fatal_error and all_passed)
    return {
        "timestamp": now_iso(),
        "run_id": RUN_DIR.name,
        "phase": "CPU C04 Unique bounded offline Phase A",
        "classification": "eligible" if eligible else "phase_a_empty",
        "eligible": eligible,
        "phase_a_empty": not eligible,
        "eligible_set": ["C04/ctc1"] if eligible else [],
        "primary_pair": "default -> ctc1",
        "gates": gates,
        "fatal_error": fatal_error,
        "raw_map": map_result,
        "pair_semantics": semantics,
        "answer_digest": answer,
        "paired_analyze": paired,
        "preflight": state.get("preflight"),
        "cleanup": cleanup,
        "fallback_db_qps": 183.02089028620412,
        "runtime_started": False,
        "hybench_started": False,
        "binary_plan_resource_tuner_started": False,
        "strict_runtime_success_claimed": False,
        "stop_after_phase_a": True,
        "await_main_agent": eligible,
        "finalized_after_controller_exit": finalized,
        "artifacts": {
            "config": str(CONFIG_PATH),
            "sql_reused": str(REPO / "shell/hybenchxp_memory_ap6_v86_m02_distinct_unique.sql"),
            "run_dir": str(RUN_DIR),
            "decision": str(DECISION_PATH),
            "report_json": str(REPORT_JSON_PATH),
            "report_markdown": str(REPORT_MD_PATH),
        },
    }


def markdown_report(decision: Dict[str, Any]) -> str:
    map_result = decision.get("raw_map") or {}
    answer = decision.get("answer_digest") or {}
    paired = decision.get("paired_analyze") or {}
    semantics = decision.get("pair_semantics") or {}
    cleanup = decision.get("cleanup") or {}
    lines = [
        "# HyBenchXP CPU C04 Unique bounded offline Phase A",
        "",
        f"- Final eligible: **`{str(decision['eligible']).lower()}`**",
        f"- Classification: **`{decision['classification']}`**",
        "- Primary pair: `default -> ctc1`",
        "- Fallback DB QPS retained: `183.02089028620412`",
        "- Runtime/HyBench/tuner: not started; this report does not claim strict runtime success",
        "",
        "## Raw map",
        "",
        "| seed | exact-seven vector | execution fingerprint | nodes | closed collision | map ANALYZE |",
        "|---|---|---|---|---|---|",
    ]
    for cell in map_result.get("cells") or []:
        counts = ", ".join(f"{key}={value}" for key, value in cell.get("counts", {}).items())
        collisions = ",".join(cell.get("closed_family_collisions") or []) or "none"
        lines.append(
            f"| `{cell['seed']}` | `{cell['vector']}` | `{cell['execution_fingerprint_hash']}` | "
            f"{counts} | {collisions} | `{str(cell.get('analyzed', False)).lower()}` |"
        )
    lines.extend(
        [
            "",
            f"Execution-fingerprint groups: `{json.dumps(map_result.get('fingerprint_groups'), sort_keys=True)}`.",
            "Only the intended `default -> ctc1` pair was subsequently analyzed.",
            "",
            "## Pair semantics",
            "",
            f"- Default fingerprint: `{semantics.get('default_fingerprint')}` "
            f"(remapped: `{semantics.get('default_fingerprint_remapped')}`)",
            f"- Candidate fingerprint: `{semantics.get('candidate_fingerprint')}` "
            f"(remapped: `{semantics.get('candidate_fingerprint_remapped')}`)",
            f"- Semantic gate: `{semantics.get('passed')}`; closed-family clear: "
            f"`{semantics.get('closed_family_clear')}`",
            "",
            "## Answer digest",
            "",
            f"- Checked/equal: `{answer.get('checked')}` / `{answer.get('equal')}`",
            f"- Complete result row counts: default `{answer.get('default', {}).get('complete_result_row_count')}`, "
            f"candidate `{answer.get('candidate', {}).get('complete_result_row_count')}`",
            f"- Default normalized SHA-256: `{answer.get('default', {}).get('normalized_sha256')}`",
            f"- Candidate normalized SHA-256: `{answer.get('candidate', {}).get('normalized_sha256')}`",
            f"- `count(*)`: `{answer.get('default', {}).get('count_star')}`",
            f"- `sum(sourceid::numeric)`: `{answer.get('default', {}).get('sum_sourceid_numeric')}`",
            "- NULL normalization: wire `__BONECT_SQL_NULL__`, canonical "
            "`{\"is_null\":true,\"text\":null}`",
            "",
            "## Paired ANALYZE",
            "",
            "One equal warmup pair preceded three measured interleaved groups.",
            "",
            "| group | order | default ms | candidate ms | candidate/default | default peak MiB | candidate peak MiB |",
            "|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    for group in paired.get("groups") or []:
        lines.append(
            f"| {group['group']} | `{'/'.join(group['execution_order'])}` | "
            f"{group['default_ms']:.3f} | {group['candidate_ms']:.3f} | "
            f"{group['candidate_default_ratio']:.6f} | "
            f"{group['default']['effective_peak_mib']:.3f} | "
            f"{group['candidate']['effective_peak_mib']:.3f} |"
        )
    lines.extend(
        [
            "",
            f"- Median paired ratio: `{paired.get('median_candidate_default_ratio')}` (gate `<=0.85`)",
            f"- Median effective peak delta: "
            f"`{paired.get('candidate_minus_default_effective_peak_mib')} MiB` (gate `>=512 MiB`)",
            f"- Peak attribution to same Unique/Sort/Gather Merge plan: "
            f"`{paired.get('candidate_peak_attributed_to_unique_sort_gather_merge')}`",
            "- Shared reads were recorded but were not treated as physical IO sweet evidence.",
            "",
            "## Gate decision",
            "",
            "| gate | passed | evidence |",
            "|---|---|---|",
        ]
    )
    for name, item in decision["gates"].items():
        lines.append(f"| `{name}` | `{item['passed']}` | {item['evidence']} |")
    lines.extend(
        [
            "",
            "## Process and restoration evidence",
            "",
            f"- Controller PID: `{cleanup.get('primary_controller_pid')}`; alive after exit audit: "
            f"`{cleanup.get('primary_controller_process_alive')}`",
            f"- Controller exit code: `{cleanup.get('controller_exit_code')}`",
            f"- Task-owned Python processes remaining: "
            f"`{cleanup.get('task_owned_python_processes_remaining')}`",
            f"- Task-owned PostgreSQL sessions remaining: "
            f"`{cleanup.get('task_owned_database_sessions_remaining')}`",
            f"- tmux session exists after cleanup: `{cleanup.get('tmux_session_exists')}`",
            f"- Full `pg_settings` restored exactly: `{cleanup.get('settings_restored')}`",
            "- Every experiment query used `REPEATABLE READ READ ONLY`, all settings used `SET LOCAL`, "
            "and each transaction ended with `ROLLBACK` or connection teardown on error.",
            "- No external process, container, schema, data, or runtime workload was modified.",
            "",
            "## Stop",
            "",
            "Phase A stopped after decision/report generation. Even an eligible result requires an independent "
            "main-agent runtime authorization; no HyBench or runtime stage was started.",
            "",
        ]
    )
    return "\n".join(lines)


def persist_decision(state: Dict[str, Any], *, finalized: bool) -> Dict[str, Any]:
    decision = build_decision(state, finalized=finalized)
    write_json(RESULT_PATH, state)
    write_json(DECISION_PATH, decision)
    write_json(REPORT_JSON_PATH, decision)
    REPORT_MD_PATH.write_text(markdown_report(decision), encoding="utf-8")
    return decision


def run_primary() -> int:
    existing = []
    if RUN_DIR.exists():
        existing = [path for path in RUN_DIR.iterdir() if path.name != "preflight_pycache"]
    if existing:
        raise RuntimeError(f"run directory is not fresh: {existing}")
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "controller.pid").write_text(f"{os.getpid()}\n", encoding="ascii")
    state: Dict[str, Any] = {
        "timestamp_start": now_iso(),
        "run_id": RUN_DIR.name,
        "controller_pid": os.getpid(),
        "controller_pgid": os.getpgid(0),
        "session": SESSION_NAME,
        "fatal_error": None,
        "runtime_started": False,
        "hybench_started": False,
        "binary_plan_resource_tuner_started": False,
    }
    exit_code = 0
    try:
        config, vectors = load_and_validate_config()
        state["provenance"] = {
            "config_path": str(CONFIG_PATH),
            "config_sha256": sha256_file(CONFIG_PATH),
            "sql_path": str(REPO / config["sql_path"]),
            "sql_sha256": sha256_file(REPO / config["sql_path"]),
            "v86_plan_parser_path": str(PARSER_PATH),
            "v86_plan_parser_sha256": sha256_file(PARSER_PATH),
        }
        sql_body = strip_sql((REPO / config["sql_path"]).read_text(encoding="utf-8"))
        if "count(*)" not in sql_body.lower() or "sum(sourceid::numeric)" not in sql_body.lower():
            raise RuntimeError("reused SQL does not expose both required answer fields")
        update_status("preflight", "live same-task conflict and worker identity audit")
        state["preflight"] = preflight(config)
        state["settings_before"] = settings_snapshot(config)
        state["settings_before_sha256"] = sha256_bytes(
            json.dumps(state["settings_before"], sort_keys=True, separators=(",", ":")).encode()
        )
        write_json(RUN_DIR / "preflight.json", state["preflight"])
        append_event("preflight_complete", pid=os.getpid(), worker_port=56433)

        state["raw_map"] = run_map(config, vectors, sql_body)
        map_by_seed = {item["seed"]: item for item in state["raw_map"]["cells"]}
        state["pair_semantics"] = main_pair_semantics(config, map_by_seed)
        write_json(RUN_DIR / "pair_semantics.json", state["pair_semantics"])
        if not state["pair_semantics"]["passed"]:
            raise RuntimeError(f"primary pair semantic/collision gate failed: {state['pair_semantics']}")
        append_event("map_and_semantic_gate_complete", semantics=state["pair_semantics"])

        update_status("answer_gate", "complete normalized answers for default and ctc1")
        default_digest = answer_digest(config, sql_body, vectors["default"])
        candidate_digest = answer_digest(config, sql_body, vectors["ctc1"])
        state["answer_digest"] = {
            "checked": True,
            "default": default_digest,
            "candidate": candidate_digest,
            "complete_result_row_count_equal": default_digest["complete_result_row_count"]
            == candidate_digest["complete_result_row_count"],
            "equal": default_digest["normalized_sha256"]
            == candidate_digest["normalized_sha256"],
        }
        write_json(RUN_DIR / "answer_digest.json", state["answer_digest"])
        if not (
            state["answer_digest"]["equal"]
            and state["answer_digest"]["complete_result_row_count_equal"]
        ):
            raise RuntimeError(f"answer completeness/digest gate failed: {state['answer_digest']}")
        append_event(
            "answer_gate_complete",
            sha256=default_digest["normalized_sha256"],
            row_count=default_digest["complete_result_row_count"],
        )

        state["paired_analyze"] = run_paired_analyze(
            config, vectors, sql_body, state["pair_semantics"]
        )
        append_event(
            "paired_analyze_complete",
            median_ratio=state["paired_analyze"]["median_candidate_default_ratio"],
            peak_delta_mib=state["paired_analyze"][
                "candidate_minus_default_effective_peak_mib"
            ],
        )
    except subprocess.TimeoutExpired as exc:
        state["fatal_error"] = {
            "type": "TimeoutExpired",
            "message": str(exc),
            "fail_closed": True,
        }
        exit_code = 2
        append_event("fail_closed", error=state["fatal_error"])
    except Exception as exc:  # noqa: BLE001
        state["fatal_error"] = {
            "type": type(exc).__name__,
            "message": str(exc)[:4000],
            "fail_closed": True,
        }
        exit_code = 2
        append_event("fail_closed", error=state["fatal_error"])
    finally:
        try:
            config, _ = load_and_validate_config()
            settings_after = settings_snapshot(config)
            settings_after_sha = sha256_bytes(
                json.dumps(settings_after, sort_keys=True, separators=(",", ":")).encode()
            )
            activity_after = database_activity(config)
            state["settings_after"] = settings_after
            state["settings_after_sha256"] = settings_after_sha
            state["cleanup"] = {
                "timestamp_primary_cleanup": now_iso(),
                "primary_controller_pid": os.getpid(),
                "primary_controller_process_alive": True,
                "controller_exit_code": None,
                "settings_before_sha256": state.get("settings_before_sha256"),
                "settings_after_sha256": settings_after_sha,
                "settings_restored": bool(state.get("settings_before") == settings_after),
                "database_activity_after": activity_after,
                "task_owned_database_sessions_remaining": [
                    item for item in activity_after if item.get("application_name") == APP_NAME
                ],
                "task_owned_python_processes_remaining": [
                    item for item in owned_python_processes() if item["pid"] != os.getpid()
                ],
                "tmux_session_exists": True,
                "set_local_only": True,
                "read_only_transactions": True,
                "explicit_rollback": True,
                "external_processes_modified": False,
                "docker_modified": False,
                "schema_or_data_modified": False,
                "process_exit_audit_pending": True,
            }
        except Exception as cleanup_exc:  # noqa: BLE001
            state["cleanup"] = {
                "timestamp_primary_cleanup": now_iso(),
                "primary_controller_pid": os.getpid(),
                "primary_controller_process_alive": True,
                "controller_exit_code": None,
                "settings_restored": False,
                "cleanup_error": f"{type(cleanup_exc).__name__}: {cleanup_exc}",
                "process_exit_audit_pending": True,
            }
            if state.get("fatal_error") is None:
                state["fatal_error"] = {
                    "type": "CleanupError",
                    "message": str(cleanup_exc),
                    "fail_closed": True,
                }
                exit_code = 2
        state["timestamp_primary_end"] = now_iso()
        state["primary_exit_intent"] = exit_code
        decision = persist_decision(state, finalized=False)
        update_status(
            "awaiting_exit_audit",
            "report written; primary process will exit and main agent must finalize PID/session cleanup",
            eligible=decision["eligible"],
        )
        append_event("primary_report_written", exit_intent=exit_code)
    return exit_code


def tmux_session_exists() -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", SESSION_NAME],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def finalize_cleanup(controller_exit_code: int) -> int:
    if not RESULT_PATH.exists() or not (RUN_DIR / "controller.pid").exists():
        raise RuntimeError("cannot finalize without primary result and controller.pid")
    state = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    config, _ = load_and_validate_config()
    primary_pid = int((RUN_DIR / "controller.pid").read_text(encoding="ascii").strip())
    primary_alive = Path(f"/proc/{primary_pid}").exists()
    processes = [item for item in owned_python_processes() if item["pid"] != os.getpid()]
    activity = database_activity(config)
    task_sessions = [item for item in activity if item.get("application_name") == APP_NAME]
    cleanup = dict(state.get("cleanup") or {})
    cleanup.update(
        {
            "timestamp_finalized": now_iso(),
            "primary_controller_pid": primary_pid,
            "primary_controller_process_alive": primary_alive,
            "controller_exit_code": controller_exit_code,
            "task_owned_python_processes_remaining": processes,
            "database_activity_after_exit": activity,
            "task_owned_database_sessions_remaining": task_sessions,
            "tmux_session_exists": tmux_session_exists(),
            "process_exit_audit_pending": False,
            "finalizer_pid": os.getpid(),
        }
    )
    state["cleanup"] = cleanup
    state["timestamp_finalized"] = now_iso()
    decision = persist_decision(state, finalized=True)
    write_json(RUN_DIR / "final_cleanup.json", cleanup)
    update_status(
        "finalized",
        "controller exited; PID/session/backend/settings cleanup audited; report-only stop complete",
        eligible=decision["eligible"],
    )
    append_event(
        "final_cleanup_complete",
        eligible=decision["eligible"],
        controller_exit_code=controller_exit_code,
        primary_alive=primary_alive,
    )
    return 0 if not primary_alive and not processes and not task_sessions else 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finalize-cleanup", action="store_true")
    parser.add_argument("--controller-exit-code", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.finalize_cleanup:
        if args.controller_exit_code is None:
            raise RuntimeError("--controller-exit-code is required for cleanup finalization")
        return finalize_cleanup(args.controller_exit_code)
    if args.controller_exit_code is not None:
        raise RuntimeError("--controller-exit-code is only valid with --finalize-cleanup")
    return run_primary()


if __name__ == "__main__":
    sys.exit(main())
