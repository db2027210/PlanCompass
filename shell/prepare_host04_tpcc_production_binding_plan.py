#!/usr/bin/env python3
"""Build the TPC-C production binding/plan contract and a no-SQL host04 gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_DIR = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries"
BASE_WORKLOAD = Path("/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/config/formal/frozen_mysql_workloads_and_parameters_20260722_v2/mysql_workloads.json")
SOURCE_DIR = Path("/home/anonymous/benchbase/src/main/java/com/oltpbenchmark/benchmarks/tpcc/procedures")
CONSTANTS_PATH = SOURCE_DIR.parent / "TPCCConstants.java"
SENSITIVE_PATH = Path("/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/config/workloads/mysql_tpcc_sensitive_item_semijoin.sql")
PRODUCTION_JAR = Path("/home/anonymous/benchbase-mysql/benchbase.jar")
PRODUCTION_JAR_SHA = "0acf80b463321f52323dc75f10643f17994046d0d20fd3a5faa1f3c94d38a032"
DEFAULT_OUTPUT = ROOT / "history/mysql_sgd_env_agents_20260723/tpcc/host04_tpcc_production_binding_plan_dry_run"
TRANSACTIONS = ["NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel"]
EXPECTED_STATEMENT_COUNTS = {"NewOrder": 10, "Payment": 10, "OrderStatus": 4, "Delivery": 7, "StockLevel": 2}
SQL_PATTERN = re.compile(
    r"(?:public|private|protected)\s+(?:final\s+)?SQLStmt\s+(\w+)\s*=\s*\n?\s*new SQLStmt\(\s*\n?\s*\"\"\"(.*?)\"\"\"\s*\n?\s*\.formatted\(((?:TPCCConstants\.TABLENAME_\w+\s*,?\s*)+)\)\)",
    re.DOTALL,
)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: Any) -> str:
    payload = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def normalized_sql(value: str) -> str:
    sql = "\n".join(line.rstrip() for line in value.strip().splitlines())
    return sql if sql.endswith(";") else sql + ";"


def read_constants() -> dict[str, str]:
    text = CONSTANTS_PATH.read_text(encoding="utf-8")
    return dict(re.findall(r'public static final String (TABLENAME_\w+) = "([^"]+)";', text))


def build_contract() -> dict[str, Any]:
    if sha(PRODUCTION_JAR) != PRODUCTION_JAR_SHA:
        raise ValueError("frozen production BenchBase JAR SHA mismatch")
    workload_doc = json.loads(BASE_WORKLOAD.read_text(encoding="utf-8"))
    workload = workload_doc["workloads"]["tpcc"]
    expected_weights = {"NewOrder": 45, "Payment": 43, "OrderStatus": 4, "Delivery": 4, "StockLevel": 4}
    if workload["scale_factor"] != 10 or workload["warehouses"] != 10 or workload["terminals"] != 32:
        raise ValueError("TPC-C SF/warehouse/terminal freeze mismatch")
    if workload["transaction_weights_percent"] != expected_weights:
        raise ValueError("TPC-C native mix mismatch")
    if sha(SENSITIVE_PATH) != workload["sensitive_sql"]["sha256"]:
        raise ValueError("sensitive SQL SHA mismatch")
    constants = read_constants()
    procedures = []
    statement_total = 0
    for transaction in TRANSACTIONS:
        source_path = SOURCE_DIR / f"{transaction}.java"
        source_text = source_path.read_text(encoding="utf-8")
        matches = SQL_PATTERN.findall(source_text)
        if len(matches) != EXPECTED_STATEMENT_COUNTS[transaction]:
            raise ValueError(f"incomplete SQLStmt extraction for {transaction}: {len(matches)}")
        statements = []
        for declaration_order, (statement_id, source_template, constant_expression) in enumerate(matches, 1):
            source_sql = normalized_sql(source_template)
            table_constants = re.findall(r"TPCCConstants\.(TABLENAME_\w+)", constant_expression)
            if source_sql.count("%s") != len(table_constants) or any(name not in constants for name in table_constants):
                raise ValueError(f"unresolved SQL table for {transaction}.{statement_id}")
            production_sql = source_sql
            for table_constant in table_constants:
                production_sql = production_sql.replace("%s", constants[table_constant], 1)
            first_word = production_sql.lstrip().split(None, 1)[0].upper()
            kind = "write" if first_word in {"INSERT", "UPDATE", "DELETE", "REPLACE"} else "read"
            statements.append({
                "actual_bindings": [],
                "concrete_binding_status": "MUST_BE_OBSERVED_FROM_PRODUCTION_EXECUTION",
                "declaration_order_not_execution_order": declaration_order,
                "dynamic": "?" in production_sql,
                "production_sql_template": production_sql,
                "production_sql_template_sha256": hashlib.sha256(production_sql.encode()).hexdigest(),
                "statement_id": statement_id,
                "statement_kind": kind,
                "table_constants": table_constants,
            })
        statement_total += len(statements)
        procedures.append({
            "runtime_order_contract": "observe every actual branch/loop statement; never infer order from declarations",
            "source": {"path": str(source_path), "sha256": sha(source_path)},
            "statements": statements,
            "transaction": transaction,
            "weight_percent": expected_weights[transaction],
        })
    sensitive_sql = normalized_sql(SENSITIVE_PATH.read_text(encoding="utf-8"))
    return {
        "benchmark": "tpcc",
        "capture_authority": {
            "actual_executable_sql_and_values": "MySQL_8.0_general_log_Prepare_Execute_Query_arguments_from_exact_production_JAR",
            "jdbc_type_rule": "publish only when exact SHA-closed setter site maps unambiguously; otherwise binding remains missing",
            "required_runtime_events": ["transaction_begin", "statement", "retry", "commit", "rollback", "technical_failure"],
            "server_log_limit": "general log does not by itself prove JDBC setter types or client RNG state",
        },
        "complete_sql_inventory": {
            "native_statement_count": statement_total,
            "procedures": procedures,
            "sensitive_statement": {
                "actual_bindings": [],
                "dynamic": False,
                "logical_name": workload["sensitive_sql"]["logical_name"],
                "production_sql_template": sensitive_sql,
                "production_sql_template_sha256": hashlib.sha256(sensitive_sql.encode()).hexdigest(),
                "source": {"path": str(SENSITIVE_PATH), "sha256": sha(SENSITIVE_PATH)},
                "statement_kind": "read",
            },
            "total_statement_templates": statement_total + 1,
        },
        "execution_protocol_after_gate": {
            "binding_trace_before_explain": True,
            "capture_all_32_terminal_connection_and_sequence_ids": True,
            "capture_all_five_transaction_types": TRANSACTIONS,
            "capture_branch_loop_retry_commit_rollback": True,
            "missing_binding_or_statement_action": "FAIL_OPEN_RETAIN_REAL_REPLAY",
            "sensitive_global_interval_seconds": 15,
        },
        "explain_safety_contract": {
            "analyze_allowed": False,
            "execute_dml_allowed": False,
            "only_form": "EXPLAIN FORMAT=JSON <one observed actual SQL statement>",
            "plain_explain_only": True,
            "raw_output_retained_per_actual_binding_variant": True,
            "transaction_replay_for_plan_capture_allowed": False,
            "unsupported_statement_action": "record explicit unsupported evidence and fail open",
        },
        "freeze_id": "frozen_mysql_workloads_and_parameters_20260723_v3",
        "mysql_dynamic_plan_cluster_deletion_authorized": False,
        "mysql_generic_custom_semantics_available": False,
        "native_mix_percent": expected_weights,
        "production_benchbase_jar": {"path": str(PRODUCTION_JAR), "sha256": PRODUCTION_JAR_SHA},
        "scale_factor": 10,
        "schema_version": "tpcc_production_binding_capture_contract_v1",
        "sensitive_mix": {"global_interval_seconds": 15, "may_run_unthrottled": False},
        "source_constants": {"path": str(CONSTANTS_PATH), "sha256": sha(CONSTANTS_PATH)},
        "terminals": 32,
        "warehouses": 10,
        "workload_seed": workload_doc["global"]["workload_seed"],
    }


def read_host_gate(host: str) -> dict[str, Any]:
    remote_script = r'''date -u +%Y-%m-%dT%H:%M:%SZ
hostname
needle=parameter_optimizer
needle=${needle}.py
for proc in /proc/[0-9]*; do
  p=${proc#/proc/}
  cmd=$(tr '\0' ' ' <"$proc/cmdline" 2>/dev/null || true)
  case "$cmd" in *"$needle"*) ;; *) continue ;; esac
  echo "PROCESS_BEGIN $p"
  grep -E '^(Name|Cpus_allowed_list|Mems_allowed_list):' /proc/$p/status 2>/dev/null || true
  echo "$cmd"
  echo "PROCESS_END $p"
done'''
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, remote_script],
        text=True, capture_output=True, timeout=15, check=False,
    )
    processes = []
    for block in result.stdout.split("PROCESS_BEGIN ")[1:]:
        first, body = block.split("\n", 1)
        pid = int(first.strip())
        body = body.split(f"PROCESS_END {pid}", 1)[0]
        fields = {line.split(":", 1)[0]: line.split(":", 1)[1].strip()
                  for line in body.splitlines() if ":" in line and line.split(":", 1)[0] in {"Cpus_allowed_list", "Mems_allowed_list"}}
        processes.append({
            "cmdline": next((line for line in body.splitlines() if line.startswith("/") and "python" in line), ""),
            "cpus_allowed_list": fields.get("Cpus_allowed_list"),
            "mems_allowed_list": fields.get("Mems_allowed_list"),
            "pid": pid,
        })
    reachable = result.returncode == 0
    return {
        "database_connection_attempted": False,
        "explain_started": False,
        "host": host,
        "host_reachable": reachable,
        "pg_driver_processes": processes,
        "remote_exit_code": result.returncode,
        "status": "BLOCKED_PG_DRIVER_ACTIVE" if processes else ("BLOCKED_HOST_UNREACHABLE" if not reachable else "READY_REQUEST_MAIN_CONTROLLER"),
        "workload_started": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="anonymous@192.0.2.7")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    contract = build_contract()
    contract_path = REGISTRY_DIR / "tpcc.production_binding_capture_contract_v1.json"
    contract_sha = write(contract_path, contract)
    gate = read_host_gate(args.host)
    gate.update({
        "contract_sha256": contract_sha,
        "controller_mode": "DRY_RUN_ONLY",
        "next_action": "request main controller execution only after pg_driver_processes is empty",
        "planned_mutations": False,
        "r0_or_sgd": False,
        "schema_version": "host04_tpcc_production_binding_dry_run_gate_v1",
    })
    gate_sha = write(args.output_root.resolve() / "dry_run_gate.json", gate)
    print(json.dumps({"contract_sha256": contract_sha, "gate_sha256": gate_sha, "status": gate["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error), "status": "FAIL"}, sort_keys=True))
        raise SystemExit(1)
