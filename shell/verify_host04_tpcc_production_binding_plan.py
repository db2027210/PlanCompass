#!/usr/bin/env python3
"""Verify the TPC-C production binding dry-run contract without DB access."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_DIR = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries"
DEFAULT_OUTPUT = ROOT / "history/mysql_sgd_env_agents_20260723/tpcc/host04_tpcc_production_binding_plan_dry_run"
EXPECTED_COUNTS = {"NewOrder": 10, "Payment": 10, "OrderStatus": 4, "Delivery": 7, "StockLevel": 2}
EXPECTED_WEIGHTS = {"NewOrder": 45, "Payment": 43, "OrderStatus": 4, "Delivery": 4, "StockLevel": 4}


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def verify_file(path: Path) -> tuple[dict[str, Any], str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if path.read_bytes() != canonical_bytes(value):
        raise ValueError(f"noncanonical JSON: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.with_name(path.name + ".sha256").read_text(encoding="ascii") != f"{digest}  {path.name}\n":
        raise ValueError(f"sidecar mismatch: {path}")
    return value, digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    contract, contract_sha = verify_file(REGISTRY_DIR / "tpcc.production_binding_capture_contract_v1.json")
    gate, gate_sha = verify_file(args.output_root.resolve() / "dry_run_gate.json")
    if contract["schema_version"] != "tpcc_production_binding_capture_contract_v1":
        raise ValueError("schema mismatch")
    if contract["scale_factor"] != 10 or contract["warehouses"] != 10 or contract["terminals"] != 32:
        raise ValueError("SF/warehouses/terminals mismatch")
    production_jar = Path(contract["production_benchbase_jar"]["path"])
    if hashlib.sha256(production_jar.read_bytes()).hexdigest() != contract["production_benchbase_jar"]["sha256"] \
            or contract["production_benchbase_jar"]["sha256"] != "0acf80b463321f52323dc75f10643f17994046d0d20fd3a5faa1f3c94d38a032":
        raise ValueError("production JAR authority mismatch")
    constants_source = Path(contract["source_constants"]["path"])
    if hashlib.sha256(constants_source.read_bytes()).hexdigest() != contract["source_constants"]["sha256"]:
        raise ValueError("table constants source mismatch")
    if contract["native_mix_percent"] != EXPECTED_WEIGHTS or contract["sensitive_mix"] != {"global_interval_seconds": 15, "may_run_unthrottled": False}:
        raise ValueError("native+sensitive mix mismatch")
    inventory = contract["complete_sql_inventory"]
    procedures = {row["transaction"]: row for row in inventory["procedures"]}
    if set(procedures) != set(EXPECTED_COUNTS):
        raise ValueError("five-transaction coverage mismatch")
    if any(len(procedures[name]["statements"]) != count for name, count in EXPECTED_COUNTS.items()):
        raise ValueError("complete SQLStmt inventory mismatch")
    for procedure in procedures.values():
        source = Path(procedure["source"]["path"])
        if hashlib.sha256(source.read_bytes()).hexdigest() != procedure["source"]["sha256"]:
            raise ValueError(f"procedure source mismatch: {procedure['transaction']}")
    statements = [statement for procedure in procedures.values() for statement in procedure["statements"]]
    if len(statements) != 33 or inventory["native_statement_count"] != 33 or inventory["total_statement_templates"] != 34:
        raise ValueError("statement count mismatch")
    if any(not statement["production_sql_template"].strip().endswith(";") for statement in statements):
        raise ValueError("incomplete SQL text")
    if any("%s" in statement["production_sql_template"] for statement in statements):
        raise ValueError("unresolved table placeholder")
    if any(hashlib.sha256(statement["production_sql_template"].encode()).hexdigest() != statement["production_sql_template_sha256"] for statement in statements):
        raise ValueError("SQL template SHA mismatch")
    if any(statement["actual_bindings"] for statement in statements):
        raise ValueError("unobserved bindings were invented")
    if contract["mysql_generic_custom_semantics_available"] is not False or contract["mysql_dynamic_plan_cluster_deletion_authorized"] is not False:
        raise ValueError("dynamic plan deletion must fail open")
    sensitive = inventory["sensitive_statement"]
    sensitive_source = Path(sensitive["source"]["path"])
    if hashlib.sha256(sensitive_source.read_bytes()).hexdigest() != sensitive["source"]["sha256"] \
            or hashlib.sha256(sensitive["production_sql_template"].encode()).hexdigest() != sensitive["production_sql_template_sha256"]:
        raise ValueError("sensitive SQL authority mismatch")
    safety = contract["explain_safety_contract"]
    if safety["plain_explain_only"] is not True or safety["analyze_allowed"] is not False or safety["execute_dml_allowed"] is not False or safety["transaction_replay_for_plan_capture_allowed"] is not False:
        raise ValueError("unsafe write EXPLAIN contract")
    if safety["only_form"] != "EXPLAIN FORMAT=JSON <one observed actual SQL statement>":
        raise ValueError("EXPLAIN form mismatch")
    if contract["execution_protocol_after_gate"]["missing_binding_or_statement_action"] != "FAIL_OPEN_RETAIN_REAL_REPLAY":
        raise ValueError("missing binding does not fail open")
    if gate["contract_sha256"] != contract_sha or gate["controller_mode"] != "DRY_RUN_ONLY" or gate["r0_or_sgd"] is not False:
        raise ValueError("gate contract mismatch")
    if gate["planned_mutations"] is not False or gate["workload_started"] is not False or gate["explain_started"] is not False or gate["database_connection_attempted"] is not False:
        raise ValueError("forbidden activity recorded")
    if gate["pg_driver_processes"] and gate["status"] != "BLOCKED_PG_DRIVER_ACTIVE":
        raise ValueError("active PG driver did not block")
    if not gate["pg_driver_processes"] and gate["status"] != "READY_REQUEST_MAIN_CONTROLLER":
        raise ValueError("gate may only request execution")
    print(json.dumps({"contract_sha256": contract_sha, "gate_sha256": gate_sha, "native_statements": 33, "status": "PASS_DRY_RUN", "transaction_types": 5}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error), "status": "FAIL"}, sort_keys=True))
        raise SystemExit(1)
