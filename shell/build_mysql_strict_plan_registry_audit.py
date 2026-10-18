#!/usr/bin/env python3
"""Seal the current five-workload strict-plan readiness audit without DB access."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries"
TRACE_ROOT = ROOT / "history/mysql_sgd_env_agents_20260723/ap/candidate_binding_trace_20260723_0503"
TRACE = TRACE_ROOT / "remote_artifacts/raw_trace.jsonl"
TRACE_MANIFEST = TRACE_ROOT / "remote_artifacts/raw_trace.jsonl.manifest.json"
TRACE_RESULT = TRACE_ROOT / "execution_result.json"
XP_COUNT = ROOT / "history/mysql_sgd_env_agents_20260723/xp/xp_sql_count_20260723_0620/sql_count_verdict.json"
JOB_REGISTRY = OUT / "job.mysql_plan_workload_registry_v1.json"
AP_REGISTRY = OUT / "hybench_ap.mysql_plan_workload_registry_v1.json"
AP_PRODUCTION_ROOT = (
    ROOT / "history/mysql_sgd_env_agents_20260723/ap/production_binding_trace_20260723_0630"
)
AP_PRODUCTION_EVIDENCE = AP_PRODUCTION_ROOT / "production_binding_and_plan_evidence.json"
AP_PRODUCTION_BUNDLE = AP_PRODUCTION_ROOT / "artifacts/canonical_plan_bundle.json"
AP_PRODUCTION_RAW_INVENTORY = AP_PRODUCTION_ROOT / "artifacts/raw_explain_inventory.json"
JOB_PLAN_ROOT = ROOT / "history/mysql_sgd_env_agents_20260723/job/job_strict_plan_capture_20260723_0642"
JOB_PLAN_BUNDLE = JOB_PLAN_ROOT / "artifacts/canonical_plan_bundle.json"
JOB_PLAN_EVIDENCE = JOB_PLAN_ROOT / "job_strict_plan_evidence.json"
FREEZE_ID = "frozen_mysql_workloads_and_parameters_20260723_v3"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: Any) -> str:
    payload = canonical_bytes(value)
    sha = hashlib.sha256(payload).hexdigest()
    path.write_bytes(payload)
    path.with_name(path.name + ".sha256").write_text(f"{sha}  {path.name}\n", encoding="ascii")
    return sha


def main() -> int:
    rows = [json.loads(line) for line in TRACE.read_text(encoding="utf-8").splitlines()]
    by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_query[row["query_id"]].append(row)
    ap_queries = []
    for number in range(1, 14):
        query_id = f"AP-{number}"
        captures = sorted(by_query[query_id], key=lambda row: row["worker_or_terminal"])
        ap_queries.append({
            "actual_binding_captures": [{
                "actual_bindings": row["actual_bindings"],
                "canonical_executable_sql_sha256": row["canonical_executable_sql_sha256"],
                "event_id": row["event_id"],
                "worker": row["worker_or_terminal"],
            } for row in captures],
            "capture_count": len(captures),
            "plan_capture_present": False,
            "query_id": query_id,
            "template_sql_sha256": captures[0]["template_sql_sha256"],
        })
    selectors = sorted(by_query["AP-2-RAND"], key=lambda row: row["worker_or_terminal"])
    result = json.loads(TRACE_RESULT.read_text(encoding="utf-8"))
    manifest = json.loads(TRACE_MANIFEST.read_text(encoding="utf-8"))
    ap_evidence = {
        "binding_coverage_complete_for_this_candidate_run": True,
        "benchmark": "hybench_ap",
        "candidate_jar_sha256": result["candidate_jar_sha256"],
        "formal_frozen_registry_publication_authorized": False,
        "freeze_id_target": FREEZE_ID,
        "generic_custom_plan_semantics_claimed": False,
        "plan_capture_present": False,
        "production_jar_sha256": result["production_jar_sha256"],
        "queries": ap_queries,
        "rand_selector_captures": [{
            "database_rand_value": row["seed_derivation"]["database_rand_value"],
            "event_id": row["event_id"],
            "worker": row["worker_or_terminal"],
        } for row in selectors],
        "rejection_reasons": [
            "execution_result_marks_trace_nonformal_candidate_and_formal_ready_false",
            "trace_base_random_seed_20260723_differs_from_frozen_seed_4508333",
            "candidate_jar_sha256_differs_from_production_jar_sha256",
            "no_raw_EXPLAIN_FORMAT_JSON_capture_exists_for_any_query",
            "MySQL_generic_custom_plan_semantics_are_not_available_and_are_not_claimed",
        ],
        "run_id": manifest["run_id"],
        "schema_version": "mysql_hybench_ap_candidate_binding_evidence_v1",
        "source": {
            "execution_result": {"path": str(TRACE_RESULT), "sha256": digest(TRACE_RESULT)},
            "manifest": {"path": str(TRACE_MANIFEST), "sha256": digest(TRACE_MANIFEST)},
            "trace": {"event_count": len(rows), "path": str(TRACE), "sha256": digest(TRACE)},
        },
        "status": "VALID_NONFORMAL_CANDIDATE_BINDING_EVIDENCE_ONLY",
        "trace_base_random_seeds": sorted({row["seed_derivation"]["base_random_seed"] for row in rows}),
        "trace_query_counts": dict(sorted(Counter(row["query_id"] for row in rows).items())),
        "trace_workers": sorted({row["worker_or_terminal"] for row in rows}),
    }
    evidence_path = OUT / "hybench_ap.candidate_binding_trace_evidence_v1.json"
    evidence_sha = write(evidence_path, ap_evidence)

    audit = {
        "freeze_id": FREEZE_ID,
        "formal_r0_or_sgd_started": False,
        "mysql_generic_custom_semantics_claimed": False,
        "published_workload_registries": [
            {"benchmark": "hybench_ap", "path": str(AP_REGISTRY), "sha256": digest(AP_REGISTRY)},
            {"benchmark": "job", "path": str(JOB_REGISTRY), "sha256": digest(JOB_REGISTRY)},
        ],
        "publishable_strict_physical_plan_registries": [
            {"benchmark": "job", "path": str(JOB_PLAN_BUNDLE), "sha256": digest(JOB_PLAN_BUNDLE)},
        ],
        "schema_version": "mysql_five_scenario_strict_plan_registry_audit_v1",
        "scenarios": [
            {
                "benchmark": "hybench_ap",
                "binding_event_count": 56,
                "canonical_plan_bundle": {
                    "path": str(AP_PRODUCTION_BUNDLE), "sha256": digest(AP_PRODUCTION_BUNDLE),
                },
                "decision": "WORKLOAD_AUTHORITY_AND_RAW_PLANS_PUBLISHED_DYNAMIC_STRICT_CLUSTER_FAIL_OPEN",
                "dynamic_plan_cluster_deletion_authorized": False,
                "generic_custom_plan_semantics_available": False,
                "missing_collection_actions": [],
                "nonformal_candidate_binding_evidence_retained": {
                    "path": str(evidence_path), "sha256": evidence_sha,
                },
                "production_binding_and_plan_evidence": {
                    "path": str(AP_PRODUCTION_EVIDENCE), "sha256": digest(AP_PRODUCTION_EVIDENCE),
                },
                "raw_explain_count": 56,
                "raw_explain_inventory": {
                    "path": str(AP_PRODUCTION_RAW_INVENTORY),
                    "sha256": digest(AP_PRODUCTION_RAW_INVENTORY),
                },
                "strict_cluster_usable": False,
                "strict_physical_plan_bundle_complete": True,
                "workload_registry": {"path": str(AP_REGISTRY), "sha256": digest(AP_REGISTRY)},
            },
            {
                "benchmark": "tpcc",
                "decision": "FAIL_CLOSED_STRICT_PLAN_REGISTRY_NOT_PUBLISHABLE",
                "missing_collection_actions": [
                    "instrument the frozen production client and seal ordered statement, transaction, retry, commit/rollback, JDBC type and actual positional value events for NewOrder, Payment, OrderStatus, Delivery and StockLevel",
                    "capture raw EXPLAIN FORMAT=JSON for every explainable statement/binding variant; preserve unsupported write-plan cases as explicit fail-open evidence",
                ],
            },
            {
                "benchmark": "job",
                "canonical_plan_bundle": {"path": str(JOB_PLAN_BUNDLE), "sha256": digest(JOB_PLAN_BUNDLE)},
                "decision": "WORKLOAD_AUTHORITY_AND_STRICT_STATIC_PLAN_BUNDLE_PUBLISHED",
                "missing_collection_actions": [],
                "plan_evidence": {"path": str(JOB_PLAN_EVIDENCE), "sha256": digest(JOB_PLAN_EVIDENCE)},
                "raw_explain_count": 9,
                "strict_cluster_usable": True,
                "workload_registry": {"path": str(JOB_REGISTRY), "sha256": digest(JOB_REGISTRY)},
            },
            {
                "benchmark": "tpch",
                "decision": "FAIL_CLOSED_STRICT_PLAN_REGISTRY_NOT_PUBLISHABLE",
                "missing_collection_actions": [
                    "seal actual prepared-statement binding values and JDBC types for Q1-Q22 from the frozen production run, including successful Q9 and Q15 occurrences",
                    "capture and retain raw EXPLAIN FORMAT=JSON for every Q1-Q22 representative binding under each candidate configuration",
                    "mark MySQL generic-plan semantics unavailable and do not delete candidates by dynamic-query plan similarity",
                ],
            },
            {
                "benchmark": "hybench_xp",
                "decision": "FAIL_CLOSED_STRICT_PLAN_REGISTRY_NOT_PUBLISHABLE",
                "missing_collection_actions": [
                    "seal the complete XP execution stream: AP1-AP13 plus every SQL statement, actual binding, retry and commit/rollback event in AT1-AT6",
                    "capture raw EXPLAIN FORMAT=JSON for every explainable AP/AT statement and binding variant; preserve unsupported write-plan cases as explicit fail-open evidence",
                    "do not treat the 87060000-row template SQL-count closure as statement, binding or plan evidence",
                ],
                "template_sql_count_only": {"path": str(XP_COUNT), "sha256": digest(XP_COUNT)},
            },
        ],
        "strict_plan_registry_publishable_count": 1,
    }
    audit_path = OUT / "strict_plan_registry_status_20260723.json"
    write(audit_path, audit)
    print(json.dumps({"ap_evidence_sha256": evidence_sha, "status_audit_sha256": digest(audit_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
