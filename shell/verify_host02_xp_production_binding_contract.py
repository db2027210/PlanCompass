#!/usr/bin/env python3
"""Independently verify the XP capture contract remains fail-open and source bound."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries/hybench_xp.production_binding_capture_contract_v1.json"


def digest(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"missing or non-regular artifact: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    claimed, name = CONTRACT.with_name(CONTRACT.name + ".sha256").read_text(encoding="utf-8").split()
    if name != CONTRACT.name or claimed != digest(CONTRACT):
        raise ValueError("contract sidecar mismatch")
    doc = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if doc.get("schema_version") != "hybench_xp_production_binding_capture_contract_v1" or doc.get("benchmark") != "hybench_xp":
        raise ValueError("contract identity mismatch")
    if doc.get("generated_without_database_access") is not True or doc.get("execution_authorized") is not False:
        raise ValueError("dry-run scope mismatch")
    for item in doc["authority"].values():
        path = Path(item["path"])
        if digest(path) != item["sha256"]:
            raise ValueError(f"authority changed: {path}")
    workload = doc["frozen_workload"]
    if workload["ap_query_ids"] != [f"AP-{i}" for i in range(1, 14)] or workload["required_at_types"] != [f"AT{i}" for i in range(1, 7)]:
        raise ValueError("XP AP/AT coverage mismatch")
    if workload["xap_clients"] != 8 or workload["xtp_clients"] != 8 or sum(workload["ap_query_weights"]) != 100 or sum(workload["at_mix_percent"].values()) != 100:
        raise ValueError("XP client/mix mismatch")
    gap = doc["current_implementation_gap"]
    if gap != {
        "production_binding_contract_preexisting": False,
        "xp_raw_explain_preexisting": False,
        "formal_binding_trace_scope": "AP13_AND_AT1_AT6",
        "ap_events_include_at_transaction_context": False,
        "tpclient_at1_at6_instrumented": True,
        "candidate_jar_production_authorized": False,
        "decision": "FAIL_OPEN_RETAIN_ALL_REAL_REPLAYS",
        "reason": "XP-specific source instrumentation covers AP13 and AT1-AT6; exact production capture, raw EXPLAIN, and production authorization remain pending",
    }:
        raise ValueError("implementation gap no longer matches sealed audit")
    trace = doc["required_trace"]
    required_events = {"transaction_begin", "statement", "retry", "commit", "rollback", "technical_failure"}
    if set(trace["event_types"]) != required_events or trace["seal_before_explain"] is not True:
        raise ValueError("trace completeness contract weakened")
    raw = doc["raw_explain_contract"]
    if raw["only_form"] != "EXPLAIN FORMAT=JSON <one observed actual SQL statement>" or raw["analyze_allowed"] is not False or raw["transaction_replay_for_plan_capture_allowed"] is not False:
        raise ValueError("raw EXPLAIN safety contract weakened")
    decision = doc["strict_plan_decision"]
    if decision["strict_cluster_usable_now"] is not False or decision["dynamic_plan_cluster_deletion_authorized"] is not False or any(value != "FAIL_OPEN_RETAIN_REAL_REPLAY" for key, value in decision.items() if key.endswith("_action")):
        raise ValueError("strict-plan fail-open contract weakened")
    gate = doc["host02_execution_gate"]
    if gate["postgresql_job_is_not_xp_counterpart"] is not True or gate["formal_cross_engine_static_gate"] is not False or gate["sealed_template_must_not_start"] is not True:
        raise ValueError("host02 formal scope mismatch")
    print(json.dumps({"status": "PASS_EXPECTED_FAIL_OPEN", "contract_sha256": digest(CONTRACT), "binding_capture_complete": False, "raw_explain_complete": False, "strict_cluster_usable": False, "real_replay_retained": True, "r0": False, "sgd": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
