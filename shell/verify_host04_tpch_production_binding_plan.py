#!/usr/bin/env python3
"""Independently verify the host04 TPC-H dry-run contract and safety gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_DIR = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries"
DEFAULT_OUTPUT = ROOT / "history/mysql_sgd_env_agents_20260723/tpch/host04_tpch_production_binding_plan_dry_run"


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
    contract, contract_sha = verify_file(REGISTRY_DIR / "tpch.production_binding_capture_contract_v1.json")
    gate, gate_sha = verify_file(args.output_root.resolve() / "dry_run_gate.json")
    expected_ids = [f"Q{i}" for i in range(1, 23)]
    if contract["schema_version"] != "tpch_production_binding_capture_contract_v1":
        raise ValueError("contract schema mismatch")
    if contract["workload_seed"] != 4508333 or contract["terminals"] != 2 or contract["scale_factor"] != 10:
        raise ValueError("frozen seed/terminals/scale mismatch")
    if [row["query_id"] for row in contract["queries"]] != expected_ids or sum(row["weight"] for row in contract["queries"]) != 100:
        raise ValueError("Q1-Q22 coverage/weight mismatch")
    if any(row["representative_bindings"] for row in contract["queries"]):
        raise ValueError("unobserved representative bindings were invented")
    if contract["production_benchbase_jar"]["sha256"] != "0acf80b463321f52323dc75f10643f17994046d0d20fd3a5faa1f3c94d38a032":
        raise ValueError("production JAR mismatch")
    if contract["candidate_instrumented_jar"]["production_authorized"] is not False:
        raise ValueError("candidate JAR incorrectly authorized")
    if contract["mysql_generic_custom_semantics_available"] is not False or contract["mysql_dynamic_plan_cluster_deletion_authorized"] is not False:
        raise ValueError("MySQL dynamic plan deletion must fail open")
    if contract["q15_direct_statement_special_case"] is not True or contract["execution_protocol_after_gate"]["q15_required_statement_sequence"] != ["CREATE VIEW", "SELECT", "DROP VIEW"]:
        raise ValueError("Q15 special capture contract missing")
    if gate["contract_sha256"] != contract_sha or gate["controller_mode"] != "DRY_RUN_ONLY" or gate["planned_mutations"] is not False:
        raise ValueError("dry-run gate contract mismatch")
    if gate["workload_started"] is not False or gate["explain_started"] is not False or gate["database_connection_attempted"] is not False:
        raise ValueError("forbidden workload/EXPLAIN/database activity recorded")
    if gate["pg_driver_processes"] and gate["status"] != "BLOCKED_PG_DRIVER_ACTIVE":
        raise ValueError("active PG driver did not block")
    if not gate["pg_driver_processes"] and gate["status"] != "READY_REQUEST_MAIN_CONTROLLER":
        raise ValueError("gate may only request, never execute")
    print(json.dumps({"contract_sha256": contract_sha, "gate_sha256": gate_sha, "queries": 22, "status": "PASS_DRY_RUN"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error), "status": "FAIL"}, sort_keys=True))
        raise SystemExit(1)
