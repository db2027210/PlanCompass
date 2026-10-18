#!/usr/bin/env python3
"""Build a fail-closed TPC-C trace registry from one production general log."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries/tpcc.production_binding_capture_contract_v1.json"
DEFAULT_CAPTURE = ROOT / "history/mysql_sgd_algorithm_c/tpcc_cpu/production_binding_capture/capture_result.json"
DEFAULT_OUTPUT = ROOT / "history/mysql_sgd_algorithm_c/tpcc_cpu/production_trace_registry"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value: Any) -> str:
    payload = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    digest = sha256_bytes(payload)
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def strip_leading_comments(sql: str) -> str:
    value = sql.strip()
    while value.startswith("/*") and "*/" in value:
        value = value[value.index("*/") + 2 :].lstrip()
    return value


def normalize_sql(sql: str) -> str:
    """Normalize literal values without changing SQL structure."""
    value = strip_leading_comments(sql).rstrip().rstrip(";")
    out: list[str] = []
    i = 0
    while i < len(value):
        char = value[i]
        if char in "'\"`":
            quote = char
            if quote == "`":
                out.append(char)
                i += 1
                while i < len(value):
                    out.append(value[i])
                    if value[i] == quote:
                        i += 1
                        break
                    i += 1
                continue
            out.append("?")
            i += 1
            while i < len(value):
                if value[i] == "\\":
                    i += 2
                    continue
                if value[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if char == "?":
            out.append("?")
            i += 1
            continue
        signed_number = char in "+-" and i + 1 < len(value) and value[i + 1].isdigit()
        if char.isdigit() or signed_number or (char == "." and i + 1 < len(value) and value[i + 1].isdigit()):
            if i == 0 or not (value[i - 1].isalnum() or value[i - 1] in "_$"):
                out.append("?")
                i += 2 if signed_number else 1
                while i < len(value) and (value[i].isalnum() or value[i] in ".+-_"):  # numeric/date token
                    i += 1
                continue
        out.append(char)
        i += 1
    return re.sub(r"\s+", " ", "".join(out)).strip().upper()


def literal_tokens(sql: str) -> list[str]:
    """Return observed literal spellings; types remain explicitly unproven."""
    value = strip_leading_comments(sql)
    tokens: list[str] = []
    i = 0
    while i < len(value):
        char = value[i]
        if char in "'\"":
            start = i
            quote = char
            i += 1
            while i < len(value):
                if value[i] == "\\":
                    i += 2
                    continue
                i += 1
                if value[i - 1] == quote:
                    break
            tokens.append(value[start:i])
            continue
        signed_number = char in "+-" and i + 1 < len(value) and value[i + 1].isdigit()
        if char.isdigit() or signed_number or (char == "." and i + 1 < len(value) and value[i + 1].isdigit()):
            if i == 0 or not (value[i - 1].isalnum() or value[i - 1] in "_$"):
                start = i
                i += 2 if signed_number else 1
                while i < len(value) and (value[i].isalnum() or value[i] in ".+-_"):
                    i += 1
                tokens.append(value[start:i])
                continue
        i += 1
    return tokens


def parse_general_log(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pattern = re.compile(r"^(\S+)\s+(\d+)\s+(\w+)\s?(.*)$")
    current: dict[str, Any] | None = None
    for line_number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        match = pattern.match(raw)
        if not match:
            if current is not None and current["command"].lower() == "query":
                current["argument"] += "\n" + raw
            continue
        if current is not None:
            rows.append(current)
        timestamp, connection_id, command, argument = match.groups()
        current = {
            "line_number": line_number,
            "timestamp": timestamp,
            "connection_id": int(connection_id),
            "command": command,
            "argument": argument,
            "line_sha256": sha256_bytes(raw.encode()),
        }
    if current is not None:
        rows.append(current)
    return rows


def statement_inventory(contract: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for procedure in contract["complete_sql_inventory"]["procedures"]:
        for statement in procedure["statements"]:
            rows.append({
                "transaction": procedure["transaction"],
                "weight_percent": procedure["weight_percent"],
                "query_id": f"{procedure['transaction']}.{statement['statement_id']}",
                **statement,
                "template_shape": normalize_sql(statement["production_sql_template"]),
            })
    sensitive = contract["complete_sql_inventory"]["sensitive_statement"]
    rows.append({
        "transaction": "SensitiveStockLevel",
        "weight_percent": 0,
        "query_id": "SensitiveStockLevel." + sensitive["logical_name"],
        "statement_id": sensitive["logical_name"],
        "statement_kind": sensitive["statement_kind"],
        "production_sql_template": sensitive["production_sql_template"],
        "production_sql_template_sha256": sensitive["production_sql_template_sha256"],
        "dynamic": sensitive["dynamic"],
        "template_shape": normalize_sql(sensitive["production_sql_template"]),
    })
    return rows


def build_trace(contract: dict[str, Any], capture: dict[str, Any], log_path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    inventory = statement_inventory(contract)
    by_shape: dict[str, list[dict[str, Any]]] = {}
    for item in inventory:
        by_shape.setdefault(item["template_shape"], []).append(item)
    events = parse_general_log(log_path)
    query_events = [row for row in events if row["command"].lower() == "query"]
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for row in query_events:
        shape = normalize_sql(row["argument"])
        candidates = by_shape.get(shape, [])
        if not candidates and row["argument"].lstrip().upper().startswith("INSERT INTO ORDER_LINE"):
            candidates = [
                item for item in inventory
                if item["statement_id"] == "stmtInsertOrderLineSQL"
                and normalize_sql(item["production_sql_template"]).split(" VALUES ", 1)[0]
                in shape.split(" VALUES ", 1)[0]
            ]
        if not candidates:
            unmatched.append(row)
            continue
        matched.append({
            "query_id_candidates": [item["query_id"] for item in candidates],
            "statement_id_candidates": [item["statement_id"] for item in candidates],
            "transaction_candidates": sorted({item["transaction"] for item in candidates}),
            "query_id": candidates[0]["query_id"] if len(candidates) == 1 else None,
            "statement_id": candidates[0]["statement_id"] if len(candidates) == 1 else None,
            "transaction": candidates[0]["transaction"] if len(candidates) == 1 else None,
            "statement_kind": sorted({item["statement_kind"] for item in candidates}),
            "sql": row["argument"].strip(),
            "normalized_sql": shape,
            "observed_literal_tokens": literal_tokens(row["argument"]),
            "binding_status": "UNPROVEN_GENERAL_LOG_LITERAL_ONLY",
            "jdbc_types": None,
            "connection_id": row["connection_id"],
            "timestamp": row["timestamp"],
            "line_number": row["line_number"],
            "line_sha256": row["line_sha256"],
        })

    representatives: dict[str, dict[str, Any]] = {}
    for row in matched:
        key = json.dumps([row["normalized_sql"], row["statement_id_candidates"]], sort_keys=True)
        representatives.setdefault(key, {
            "corpus_id": f"observed_{len(representatives) + 1:04d}_{sha256_bytes(key.encode())[:12]}",
            **row,
            "raw_explain_status": "PENDING",
            "raw_explain": None,
        })

    observed_by_id: dict[str, list[dict[str, Any]]] = {item["query_id"]: [] for item in inventory}
    for row in matched:
        for query_id in row["query_id_candidates"]:
            observed_by_id[query_id].append(row)
    missing_ids = sorted(query_id for query_id, rows in observed_by_id.items() if not rows)
    ambiguous_count = sum(1 for row in matched if row["statement_id"] is None)
    prepare_count = sum(1 for row in events if row["command"].lower() == "prepare")
    execute_count = sum(1 for row in events if row["command"].lower() == "execute")
    transaction_events = {
        name: sum(1 for row in query_events if row["argument"].strip().upper().startswith(name))
        for name in ("START TRANSACTION", "BEGIN", "COMMIT", "ROLLBACK")
    }
    jar_match = capture.get("production_jar_sha256") == contract["production_benchbase_jar"]["sha256"]
    execution_authorized = bool(
        jar_match and not missing_ids and not ambiguous_count and prepare_count > 0 and execute_count > 0
        and all(transaction_events.values()) and all(row["binding_status"] == "TYPED_PRODUCTION_BINDING" for row in matched)
    )
    registry = {
        "schema_version": "tpcc_production_trace_strict_registry_v1",
        "benchmark": "tpcc",
        "freeze_id": contract["freeze_id"],
        "immutable": True,
        "execution_authorized": execution_authorized,
        "complete_workload": True,
        "coverage_scope": "all_observed_query_events_from_one_production_mixed_run",
        "native_statement_count": contract["complete_sql_inventory"]["native_statement_count"],
        "statement_template_count": contract["complete_sql_inventory"]["total_statement_templates"],
        "query_ids_verified": not missing_ids and ambiguous_count == 0,
        "statements": [
            {
                "query_id": item["query_id"],
                "statement_id": item["statement_id"],
                "transaction": item["transaction"],
                "statement_kind": item["statement_kind"],
                "template_sql_sha256": item["production_sql_template_sha256"],
                "observed_event_count": len(observed_by_id[item["query_id"]]),
                "observed_variants": representatives_for(observed_by_id[item["query_id"]]),
            }
            for item in inventory
        ],
        "binding_authority": {
            "source": "MySQL_8.0_general_log_Query_arguments",
            "jdbc_types_proven": False,
            "prepare_event_count": prepare_count,
            "execute_event_count": execute_count,
            "server_log_limit": contract["capture_authority"]["server_log_limit"],
        },
        "failure_reasons": [
            *([] if jar_match else ["capture_result_production_jar_sha256_does_not_match_frozen_contract"]),
            *(["missing_statement_ids:" + ",".join(missing_ids)] if missing_ids else []),
            *([f"ambiguous_statement_shape_events:{ambiguous_count}"] if ambiguous_count else []),
            *(["general_log_has_no_Prepare_or_Execute_events"] if not (prepare_count and execute_count) else []),
            "JDBC_setter_types_are_not_proven_by_general_log",
        ],
        "source": {
            "capture_result_path": str(DEFAULT_CAPTURE),
            "capture_result_sha256": sha256_file(DEFAULT_CAPTURE),
            "general_log_path": str(log_path),
            "general_log_sha256": sha256_file(log_path),
            "contract_path": str(DEFAULT_CONTRACT),
            "contract_sha256": sha256_file(DEFAULT_CONTRACT),
        },
    }
    corpus = {
        "schema_version": "tpcc_representative_sql_corpus_v1",
        "benchmark": "tpcc",
        "complete_workload": False,
        "coverage_scope": "representative_observed_variants_only_not_a_complete_workload",
        "statement_template_count": 34,
        "entries": list(representatives.values()),
        "excluded_unmatched_query_event_count": len(unmatched),
        "unmatched_query_event_examples": unmatched[:20],
        "provenance": registry["source"],
    }
    feature_source = {
        "schema_version": "tpcc_estimator_feature_source_v1",
        "benchmark": "tpcc",
        "workload_freeze_id": contract["freeze_id"],
        "coverage_scope": "read_only_representative_subset_only",
        "complete_workload": False,
        "execution_authorized": False,
        "require_strict_plan_registry": False,
        "raw_explain_required_before_estimator_use": True,
        "queries": [
            {
                "query_id": row["corpus_id"],
                "sql": row["sql"],
                "weight": 1.0,
                "dynamic": False,
                "representative_bindings": [],
                "required_plan_modes": [],
                "statement_id": row["statement_id"],
                "query_id_candidates": row["query_id_candidates"],
            }
            for row in representatives.values()
            if row["statement_kind"] == ["read"] and row["raw_explain_status"] == "PENDING"
        ],
        "excluded_statement_classes": sorted({kind for row in representatives.values() for kind in row["statement_kind"] if kind != "read"}),
        "source_registry_sha256": sha256_bytes(canonical_bytes(registry)),
    }
    return registry, corpus, feature_source


def representatives_for(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "query_id_candidates": row["query_id_candidates"],
            "statement_id_candidates": row["statement_id_candidates"],
            "sql_sha256": sha256_bytes(row["sql"].encode()),
            "sql": row["sql"],
            "connection_id": row["connection_id"],
            "timestamp": row["timestamp"],
            "line_number": row["line_number"],
            "binding_status": row["binding_status"],
            "observed_literal_tokens": row["observed_literal_tokens"],
        }
        for row in rows[:20]
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--capture-result", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    capture = json.loads(args.capture_result.read_text(encoding="utf-8"))
    if capture.get("status") != "RAW_GENERAL_LOG_CAPTURED":
        raise ValueError(f"capture_result status is not RAW_GENERAL_LOG_CAPTURED: {capture.get('status')}")
    log_path = Path(capture["raw_general_log"])
    if not log_path.is_file():
        raise FileNotFoundError(log_path)
    if sha256_file(log_path) != capture["raw_general_log_sha256"]:
        raise ValueError("capture_result raw_general_log_sha256 mismatch")
    registry, corpus, feature_source = build_trace(contract, capture, log_path)
    registry_path = args.output_dir / "tpcc.strict_registry.json"
    corpus_path = args.output_dir / "tpcc.representative_sql_corpus.json"
    feature_path = args.output_dir / "tpcc.feature_source.json"
    registry_sha = write_json(registry_path, registry)
    corpus_sha = write_json(corpus_path, corpus)
    feature_sha = write_json(feature_path, feature_source)
    provenance = {
        "schema_version": "tpcc_production_trace_registry_provenance_v1",
        "registry_path": str(registry_path),
        "registry_sha256": registry_sha,
        "corpus_path": str(corpus_path),
        "corpus_sha256": corpus_sha,
        "feature_source_path": str(feature_path),
        "feature_source_sha256": feature_sha,
        "execution_authorized": registry["execution_authorized"],
        "complete_workload": registry["complete_workload"],
        "raw_explain_status": "NOT_STARTED",
        "source": registry["source"],
    }
    provenance_sha = write_json(args.output_dir / "tpcc.trace_provenance.json", provenance)
    print(json.dumps({"status": "PASS_FAIL_CLOSED_TRACE_PARSED", "execution_authorized": registry["execution_authorized"], "registry_sha256": registry_sha, "corpus_sha256": corpus_sha, "feature_source_sha256": feature_sha, "provenance_sha256": provenance_sha}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
