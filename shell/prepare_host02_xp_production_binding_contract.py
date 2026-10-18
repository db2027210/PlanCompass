#!/usr/bin/env python3
"""Freeze the HyBench XP production binding/plan capture contract without DB access."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries/hybench_xp.production_binding_capture_contract_v1.json"
BASE = Path("/home/anonymous/program/resource_adaptive_double_loop_sgd_isolated/config/formal/frozen_mysql_workloads_and_parameters_20260722_v2/mysql_workloads.json")
EFFECTIVE = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/effective_workload_bundle.json"
STATEMENTS = Path("/home/anonymous/HyBench-2024-master/conf/stmt_mysql.toml")
AP_CLIENT = ROOT / "formal_hybench_src_20260723/src/main/java/com/hybench/workload/APClient.java"
TP_CLIENT = ROOT / "formal_hybench_src_20260723/src/main/java/com/hybench/workload/TPClient.java"
TRACE = ROOT / "formal_hybench_src_20260723/src/main/java/com/hybench/util/FormalBindingTrace.java"
SQLSTMTS = ROOT / "formal_hybench_src_20260723/src/main/java/com/hybench/workload/Sqlstmts.java"
TEMPLATE = ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/xp_template_deployment_20260723.json"
ENVELOPE = ROOT / "history/mysql_sgd_env_agents_20260723/xp/formal_mysql_normalized_envelope_readback.json"
EXPECTED_STATEMENT_SHA = "54ca28e6959d963c584e4d0c80a0cd39f582badf67945f3261fb20d4bf477bb8"


def digest(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"required source is absent or non-regular: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": digest(path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    workload_doc = json.loads(BASE.read_text(encoding="utf-8"))
    workload = workload_doc["workloads"]["hybench_xp"]
    if workload_doc["freeze_id"] != "frozen_mysql_workloads_and_parameters_20260722_v2":
        raise ValueError("unexpected base workload freeze")
    if digest(STATEMENTS) != EXPECTED_STATEMENT_SHA:
        raise ValueError("HyBench MySQL statement artifact changed")
    expected = {
        "formal_name": "HyBench-XP-AP13-mixed-AP10-weighted",
        "database_name": "hybench_sf10x",
        "clients": {"xap": 8, "xtp": 8},
        "ap_query_ids": list(range(1, 14)),
        "ap_query_weights": [6] * 9 + [28] + [6] * 3,
        "at_percent": {"AT1": 40, "AT2": 30, "AT3": 15, "AT4": 5, "AT5": 7, "AT6": 3},
    }
    for key, value in expected.items():
        if workload.get(key) != value:
            raise ValueError(f"frozen XP workload field changed: {key}")

    document = {
        "schema_version": "hybench_xp_production_binding_capture_contract_v1",
        "benchmark": "hybench_xp",
        "freeze_id": "frozen_mysql_workloads_and_parameters_20260723_v3",
        "generated_without_database_access": True,
        "contract_generation_is_not_r0_or_sgd": True,
        "execution_authorized": False,
        "authority": {
            "base_workload": ref(BASE),
            "effective_workload_bundle": ref(EFFECTIVE),
            "statement_artifact": ref(STATEMENTS),
            "ap_client_source": ref(AP_CLIENT),
            "tp_client_source": ref(TP_CLIENT),
            "sql_loader_source": ref(SQLSTMTS),
            "current_trace_source": ref(TRACE),
            "canonical_template_evidence": ref(TEMPLATE),
            "mysql_envelope_readback": ref(ENVELOPE),
        },
        "frozen_workload": {
            "formal_name": expected["formal_name"],
            "database_name": expected["database_name"],
            "scale_factor": "10x",
            "xap_clients": 8,
            "xtp_clients": 8,
            "ap_query_ids": [f"AP-{value}" for value in range(1, 14)],
            "ap_query_weights": expected["ap_query_weights"],
            "at_mix_percent": expected["at_percent"],
            "required_at_types": [f"AT{value}" for value in range(1, 7)],
        },
        "current_implementation_gap": {
            "production_binding_contract_preexisting": False,
            "xp_raw_explain_preexisting": False,
            "formal_binding_trace_scope": "AP13_AND_AT1_AT6",
            "ap_events_include_at_transaction_context": False,
            "tpclient_at1_at6_instrumented": True,
            "candidate_jar_production_authorized": False,
            "decision": "FAIL_OPEN_RETAIN_ALL_REAL_REPLAYS",
            "reason": "XP-specific source instrumentation covers AP13 and AT1-AT6; exact production capture, raw EXPLAIN, and production authorization remain pending",
        },
        "required_trace": {
            "capture_from_exact_production_client": True,
            "capture_entire_bounded_stream_without_sampling": True,
            "worker_identity_sets": {"xap": [f"XAP-{i}" for i in range(1, 9)], "xtp": [f"XTP-{i}" for i in range(1, 9)]},
            "event_types": ["transaction_begin", "statement", "retry", "commit", "rollback", "technical_failure"],
            "statement_fields": ["event_id", "worker_id", "connection_id", "transaction_id", "transaction_type", "attempt", "retry", "statement_ordinal", "template_sql_sha256", "actual_bindings", "canonical_executable_sql_sha256", "outcome"],
            "binding_fields": ["position", "jdbc_type", "value"],
            "coverage": {
                "every_ap_query": True,
                "ap2_selector_and_observed_database_rand": True,
                "every_at1_at6_type": True,
                "every_observed_branch_and_loop_statement": True,
                "every_retry_commit_rollback_and_technical_failure": True,
                "successful_committed_occurrence_per_at_type": True,
            },
            "seal_before_explain": True,
        },
        "raw_explain_contract": {
            "only_after_complete_trace_seal": True,
            "only_form": "EXPLAIN FORMAT=JSON <one observed actual SQL statement>",
            "analyze_allowed": False,
            "transaction_replay_for_plan_capture_allowed": False,
            "all_distinct_observed_statement_binding_variants": True,
            "raw_output_retained": True,
            "unsupported_statement_action": "record explicit unsupported evidence and fail open",
        },
        "strict_plan_decision": {
            "mysql_generic_custom_plan_semantics_available": False,
            "dynamic_plan_cluster_deletion_authorized": False,
            "strict_cluster_usable_now": False,
            "missing_or_invalid_binding_action": "FAIL_OPEN_RETAIN_REAL_REPLAY",
            "missing_or_invalid_raw_explain_action": "FAIL_OPEN_RETAIN_REAL_REPLAY",
            "missing_joint_coverage_action": "FAIL_OPEN_RETAIN_REAL_REPLAY",
        },
        "host02_execution_gate": {
            "shared_mutex": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_pressure.lock",
            "scenario": "hybench_xp_io",
            "session_owned_work_copy_only": True,
            "sealed_template_must_not_start": True,
            "job_and_xp_pressure_must_be_zero_before_acquisition": True,
            "hold_mutex_through_shutdown_recovery_checksum_and_cleanup": True,
            "postgresql_job_is_not_xp_counterpart": True,
            "formal_cross_engine_static_gate": False,
        },
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    output.write_text(payload, encoding="utf-8")
    sidecar = output.with_name(output.name + ".sha256")
    sidecar.write_text(f"{hashlib.sha256(payload.encode()).hexdigest()}  {output.name}\n", encoding="utf-8")
    print(json.dumps({"status": "PASS_FAIL_OPEN_CONTRACT", "output": str(output), "sha256": digest(output), "r0": False, "sgd": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
