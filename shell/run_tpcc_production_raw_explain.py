#!/usr/bin/env python3
"""Run read-only EXPLAIN FORMAT=JSON for the observed TPC-C corpus."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from mysql_cost_adapter import MySQLCLIClient
from shell.manual_mysql_sgd_r0_campaign import Campaign, LANES, MYSQL, ROOT


OUTPUT = ROOT / "history/mysql_sgd_algorithm_c/tpcc_cpu/production_trace_registry"
CORPUS = OUTPUT / "tpcc.representative_sql_corpus.json"
REGISTRY = OUTPUT / "tpcc.strict_registry.json"
RUN_ID = "raw_explain_tpcc_node01_v3_20260726T000000Z"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_bytes(value))
    temporary.replace(path)


def statement_class(sql: str) -> str:
    return sql.lstrip().split(None, 1)[0].upper() if sql.strip() else "EMPTY"


def explain_sql(sql: str) -> str:
    return "EXPLAIN FORMAT=JSON " + sql.strip().rstrip(";")


def main() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if corpus["complete_workload"] is not False:
        raise ValueError("corpus must explicitly remain representative-only")
    if registry["execution_authorized"] is not False:
        raise ValueError("raw explain cannot upgrade execution authorization")
    entries = corpus["entries"]
    campaign = Campaign(
        LANES["tpcc"], OUTPUT / "raw_explain_campaign_v3", RUN_ID,
        measurement_seconds=600, minimum_measurement_seconds=180,
    )
    client = None
    results: list[dict[str, Any]] = []
    try:
        campaign.output.mkdir(parents=True, exist_ok=False)
        campaign.prepare_remote()
        campaign.start_tunnel()
        client = MySQLCLIClient({
            "mysql_binary": str(MYSQL),
            "defaults_extra_file": str(campaign.output / "private/mysql.cnf"),
            "defaults_file_only": True,
            "host": "127.0.0.1",
            "port": campaign.lane.remote_port,
            "user": "root",
            "database": campaign.lane.database,
            "query_timeout_sec": 120,
        })
        for entry in entries:
            sql = str(entry["sql"])
            row = {
                "corpus_id": entry["corpus_id"],
                "query_id_candidates": entry["query_id_candidates"],
                "statement_id_candidates": entry["statement_id_candidates"],
                "statement_class": statement_class(sql),
                "sql": sql,
                "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
                "explain_form": "EXPLAIN FORMAT=JSON <observed SQL>",
                "dml_executed": False,
            }
            try:
                raw = client.execute(explain_sql(sql))
                row.update({"status": "EXPLAINED", "raw_explain": raw})
            except Exception as error:
                row.update({
                    "status": "UNSUPPORTED_OR_ERROR",
                    "raw_explain": None,
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "unsupported_statement_action": "retain_statement_and_fail_open",
                })
            results.append(row)
        inventory = {
            "schema_version": "tpcc_raw_explain_inventory_v1",
            "benchmark": "tpcc",
            "run_id": RUN_ID,
            "host": campaign.lane.host,
            "hostname": campaign.lane.hostname,
            "execution_authorized": False,
            "transaction_replay_used": False,
            "dml_executed": False,
            "entries": results,
            "explained_count": sum(row["status"] == "EXPLAINED" for row in results),
            "unsupported_or_error_count": sum(row["status"] != "EXPLAINED" for row in results),
            "source_corpus": {"path": str(CORPUS), "sha256": sha256_file(CORPUS)},
            "source_registry": {"path": str(REGISTRY), "sha256": sha256_file(REGISTRY)},
            "safety": {
                "only_form": "EXPLAIN FORMAT=JSON <one observed actual SQL statement>",
                "analyze_allowed": False,
                "execute_dml_allowed": False,
                "unsupported_statements_retained": True,
            },
        }
        inventory_path = OUTPUT / "tpcc.raw_explain_inventory.json"
        atomic_json(inventory_path, inventory)
        (OUTPUT / "tpcc.raw_explain_inventory.json.sha256").write_text(
            f"{sha256_file(inventory_path)}  {inventory_path.name}\n", encoding="ascii"
        )
        feature_source = {
            "schema_version": "tpcc_estimator_feature_source_v1",
            "benchmark": "tpcc",
            "workload_freeze_id": registry["freeze_id"],
            "coverage_scope": "read_only_representative_subset_only",
            "complete_workload": False,
            "execution_authorized": False,
            "raw_explain_required_before_estimator_use": False,
            "queries": [
                {
                    "query_id": row["corpus_id"],
                    "sql": row["sql"],
                    "weight": 1.0,
                    "dynamic": False,
                    "representative_bindings": [],
                    "required_plan_modes": [],
                    "raw_explain_status": row["status"],
                }
                for row in results
                if row["statement_class"] in {"SELECT", "WITH"} and row["status"] == "EXPLAINED"
            ],
            "excluded_statement_classes": sorted({row["statement_class"] for row in results if row["statement_class"] not in {"SELECT", "WITH"}}),
            "raw_explain_inventory": {"path": str(inventory_path), "sha256": sha256_file(inventory_path)},
            "source_registry_sha256": sha256_file(REGISTRY),
        }
        feature_path = OUTPUT / "tpcc.feature_source.json"
        atomic_json(feature_path, feature_source)
        print(json.dumps({"status": "RAW_EXPLAIN_COMPLETE_FAIL_CLOSED", "explained_count": inventory["explained_count"], "unsupported_or_error_count": inventory["unsupported_or_error_count"], "inventory": str(inventory_path), "feature_source": str(feature_path)}, sort_keys=True))
        return 0
    finally:
        campaign.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
