#!/usr/bin/env python3
"""Prepare the host04 TPC-H production binding capture contract; never run SQL."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_DIR = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries"
AUDIT_PATH = REGISTRY_DIR / "binding_authority_gap_audit.json"
CONFIG_PATH = ROOT / "history/brute_formal_mixed_20260722_v6_remaining_v1/mysql/brute_force/session_inputs/benchmark_configs/tpch/benchmark_config.json"
DEFAULT_OUTPUT = ROOT / "history/mysql_sgd_env_agents_20260723/tpch/host04_tpch_production_binding_plan_dry_run"
PRODUCTION_JAR = Path("/home/anonymous/benchbase-mysql/benchbase.jar")
PRODUCTION_JAR_SHA = "0acf80b463321f52323dc75f10643f17994046d0d20fd3a5faa1f3c94d38a032"
SEED_AGENT = ROOT / "formal_clients_20260721/tpch-seed-agent.jar"
SEED_AGENT_SHA = "5f48249ebc47a50b87d878985e5cb1fee9b72a46b6ce4f87571f858312687ec4"
CANDIDATE_JAR = ROOT / "formal_benchbase_src_20260723/target/benchbase-2023-SNAPSHOT-mysql-formal-binding-trace-candidate.jar"
CANDIDATE_JAR_SHA = "bd2be63c29a58a7afeac699fc0a196abb854ae44417eabe236fe8b65cc510307"


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


def assert_sha(path: Path, expected: str) -> None:
    actual = sha(path)
    if actual != expected:
        raise ValueError(f"SHA mismatch: {path}: {actual} != {expected}")


def build_contract() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    tpch = next(row for row in audit["benchmarks"] if row["benchmark"] == "tpch")
    assert_sha(PRODUCTION_JAR, PRODUCTION_JAR_SHA)
    assert_sha(SEED_AGENT, SEED_AGENT_SHA)
    if CANDIDATE_JAR.exists():
        assert_sha(CANDIDATE_JAR, CANDIDATE_JAR_SHA)
    frozen = config["frozen_workload_config"]
    if frozen["query_ids"] != list(range(1, 23)) or frozen["query_weights"] != [4] * 17 + [16] + [4] * 4:
        raise ValueError("frozen Q1-Q22 order/weights mismatch")
    if frozen["scale_factor"] != 10 or frozen["terminals"] != 2 or config["workload_seed"] != 4508333:
        raise ValueError("frozen scale/terminal/seed mismatch")
    queries = []
    for expected_number, row in enumerate(tpch["queries"], 1):
        if row["query_id"] != f"Q{expected_number}" or row["representative_bindings"]:
            raise ValueError("binding audit query order or authority mismatch")
        source = row["binding_generator_source"]
        source_path = Path(source["path"])
        assert_sha(source_path, source["sha256"])
        lines = source_path.read_bytes().splitlines(keepends=True)
        line_payload = b"".join(lines[source["line_start"] - 1:source["line_end"]])
        if hashlib.sha256(line_payload).hexdigest() != source["line_sha256"]:
            raise ValueError(f"source line-range mismatch: {row['query_id']}")
        queries.append({
            "binding_formula_domain": row["binding_formula_domain"],
            "binding_generator_source": source,
            "concrete_binding_status": "MUST_BE_OBSERVED_FROM_PRODUCTION_EXECUTION",
            "dynamic": True,
            "query_id": row["query_id"],
            "representative_bindings": [],
            "weight": row["weight"],
        })
    return {
        "benchmark": "tpch",
        "capture_authority": {
            "binding_values": "MySQL_8.0_general_log_Query_argument_from_exact_production_JAR",
            "jdbc_types_and_formulas": "SHA_closed_frozen_BenchBase_source_contract",
            "rng_state_claimed": False,
            "server_log_limit": "server log observes executable SQL, not JDBC setter types or RNG state",
        },
        "candidate_instrumented_jar": {
            "path": str(CANDIDATE_JAR), "production_authorized": False, "sha256": CANDIDATE_JAR_SHA,
        },
        "execution_protocol_after_gate": {
            "capture_before_explain": True,
            "full_successful_query_coverage_required": [f"Q{i}" for i in range(1, 23)],
            "mysql_general_log_required": True,
            "q15_required_statement_sequence": ["CREATE VIEW", "SELECT", "DROP VIEW"],
            "raw_explain_only_after_binding_trace_complete": True,
            "retain_attempt_connection_terminal_and_sequence_identity": True,
            "retain_retries_and_technical_failures": True,
            "two_terminal_occurrences_required": True,
        },
        "freeze_id": "frozen_mysql_workloads_and_parameters_20260723_v3",
        "mysql_dynamic_plan_cluster_deletion_authorized": False,
        "mysql_generic_custom_semantics_available": False,
        "production_benchbase_jar": {"path": str(PRODUCTION_JAR), "sha256": PRODUCTION_JAR_SHA},
        "queries": queries,
        "q15_direct_statement_special_case": True,
        "scale_factor": 10,
        "schema_version": "tpch_production_binding_capture_contract_v1",
        "seed_agent": {"path": str(SEED_AGENT), "sha256": SEED_AGENT_SHA},
        "terminals": 2,
        "weight_sum": 100.0,
        "workload_seed": 4508333,
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
  sed -n '1,20p' /proc/$p/cgroup 2>/dev/null || true
  echo "$cmd"
  echo
  echo "PROCESS_END $p"
done
docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | grep '^pg5_tpch_s04_20260722 ' || true'''
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, remote_script]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=15, check=False)
    raw = completed.stdout
    processes = []
    for block in raw.split("PROCESS_BEGIN ")[1:]:
        first, body = block.split("\n", 1)
        pid = int(first.strip())
        body = body.split(f"PROCESS_END {pid}", 1)[0]
        fields = {line.split(":", 1)[0]: line.split(":", 1)[1].strip()
                  for line in body.splitlines() if ":" in line and line.split(":", 1)[0] in {"Name", "Cpus_allowed_list", "Mems_allowed_list"}}
        processes.append({"cmdline": next((line for line in body.splitlines() if "parameter_optimizer.py" in line), ""),
                          "cpus_allowed_list": fields.get("Cpus_allowed_list"),
                          "mems_allowed_list": fields.get("Mems_allowed_list"), "pid": pid})
    reachable = completed.returncode == 0
    blocked = (not reachable) or bool(processes)
    return {
        "database_connection_attempted": False,
        "explain_started": False,
        "host": host,
        "host_reachable": reachable,
        "pg_driver_processes": processes,
        "raw_read_only_ssh_sha256": hashlib.sha256((completed.stdout + completed.stderr).encode()).hexdigest(),
        "remote_exit_code": completed.returncode,
        "status": "BLOCKED_PG_DRIVER_ACTIVE" if processes else ("BLOCKED_HOST_UNREACHABLE" if not reachable else "READY_REQUEST_MAIN_CONTROLLER"),
        "workload_started": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="anonymous@192.0.2.7")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    contract = build_contract()
    contract_path = REGISTRY_DIR / "tpch.production_binding_capture_contract_v1.json"
    contract_sha = write(contract_path, contract)
    gate = read_host_gate(args.host)
    gate.update({
        "contract_sha256": contract_sha,
        "controller_mode": "DRY_RUN_ONLY",
        "next_action": "request main controller execution only after pg_driver_processes is empty",
        "planned_mutations": False,
        "schema_version": "host04_tpch_production_binding_dry_run_gate_v1",
        "ssh_target_quoted": shlex.quote(args.host),
    })
    gate_sha = write(output_root / "dry_run_gate.json", gate)
    print(json.dumps({"contract_sha256": contract_sha, "gate_sha256": gate_sha, "status": gate["status"]}, sort_keys=True))
    return 0 if gate["status"].startswith("BLOCKED_") or gate["status"] == "READY_REQUEST_MAIN_CONTROLLER" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error), "status": "FAIL"}, sort_keys=True))
        raise SystemExit(1)
