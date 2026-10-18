#!/usr/bin/env python3
"""Independent offline verifier for the five-workload strict-plan audit."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from formal_hybench_ap_binding_trace import validate_hybench_ap_trace  # noqa: E402
from mysql_plan_clustering import validate_canonical_plan_bundle  # noqa: E402

OUT = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def verify_file(path: Path) -> tuple[dict[str, Any], str]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if path.read_bytes() != canonical_bytes(document):
        raise ValueError(f"noncanonical JSON: {path}")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.with_name(path.name + ".sha256").read_text(encoding="ascii") != f"{sha}  {path.name}\n":
        raise ValueError(f"invalid sidecar: {path}")
    return document, sha


def verify_ref(ref: dict[str, Any]) -> Path:
    path = Path(ref["path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
        raise ValueError(f"source SHA mismatch: {path}")
    return path


def main() -> int:
    evidence, evidence_sha = verify_file(OUT / "hybench_ap.candidate_binding_trace_evidence_v1.json")
    audit, audit_sha = verify_file(OUT / "strict_plan_registry_status_20260723.json")
    if evidence["status"] != "VALID_NONFORMAL_CANDIDATE_BINDING_EVIDENCE_ONLY":
        raise ValueError("AP evidence incorrectly promoted")
    if evidence["formal_frozen_registry_publication_authorized"] is not False:
        raise ValueError("AP frozen publication incorrectly authorized")
    if evidence["generic_custom_plan_semantics_claimed"] is not False or evidence["plan_capture_present"] is not False:
        raise ValueError("AP evidence invents plan semantics/captures")
    trace = verify_ref(evidence["source"]["trace"])
    verify_ref(evidence["source"]["manifest"])
    result_path = verify_ref(evidence["source"]["execution_result"])
    result = json.loads(result_path.read_text(encoding="utf-8"))
    rows = validate_hybench_ap_trace(trace, expected_run_id=evidence["run_id"], expected_workers=4)
    counts = dict(sorted(Counter(row["query_id"] for row in rows).items()))
    if len(rows) != 56 or set(counts.values()) != {4} or counts != evidence["trace_query_counts"]:
        raise ValueError("AP 56-event coverage mismatch")
    if evidence["trace_base_random_seeds"] != [20260723]:
        raise ValueError("AP candidate seed mismatch was not preserved")
    if result["status"] != "nonformal_candidate_trace_validated" or result["formal_ready"] is not False:
        raise ValueError("AP source no longer proves nonformal status")
    if result["candidate_jar_sha256"] == result["production_jar_sha256"]:
        raise ValueError("AP candidate/production JAR distinction disappeared")
    if len(evidence["queries"]) != 13 or any(row["capture_count"] != 4 for row in evidence["queries"]):
        raise ValueError("AP query binding evidence is incomplete")
    if audit["formal_r0_or_sgd_started"] is not False or audit["strict_plan_registry_publishable_count"] != 1:
        raise ValueError("audit falsely claims launch or publishable strict plans")
    if audit["mysql_generic_custom_semantics_claimed"] is not False:
        raise ValueError("audit falsely claims generic semantics")
    scenarios = {row["benchmark"]: row for row in audit["scenarios"]}
    if set(scenarios) != {"hybench_ap", "tpcc", "job", "tpch", "hybench_xp"}:
        raise ValueError("five-scenario coverage mismatch")
    job_status = scenarios["job"]
    if job_status["decision"] != "WORKLOAD_AUTHORITY_AND_STRICT_STATIC_PLAN_BUNDLE_PUBLISHED" \
            or job_status["raw_explain_count"] != 9 or job_status["strict_cluster_usable"] is not True \
            or job_status["missing_collection_actions"] != []:
        raise ValueError("JOB authority status mismatch")
    job_bundle = json.loads(verify_ref(job_status["canonical_plan_bundle"]).read_text(encoding="utf-8"))
    if validate_canonical_plan_bundle(job_bundle)["valid"] is not True \
            or job_bundle["strict_cluster_usable"] is not True \
            or job_bundle["plan_similarity_cluster_id"] != job_bundle["strict_canonical_physical_bundle_sha256"]:
        raise ValueError("JOB strict bundle mismatch")
    verify_ref(job_status["plan_evidence"])
    strict_published = audit["publishable_strict_physical_plan_registries"]
    if len(strict_published) != 1 or strict_published[0]["benchmark"] != "job":
        raise ValueError("strict publishable bundle set mismatch")
    verify_ref(strict_published[0])
    ap = scenarios["hybench_ap"]
    if ap["decision"] != "WORKLOAD_AUTHORITY_AND_RAW_PLANS_PUBLISHED_DYNAMIC_STRICT_CLUSTER_FAIL_OPEN" \
            or ap["binding_event_count"] != 56 or ap["raw_explain_count"] != 56 \
            or ap["strict_physical_plan_bundle_complete"] is not True \
            or ap["strict_cluster_usable"] is not False \
            or ap["dynamic_plan_cluster_deletion_authorized"] is not False \
            or ap["generic_custom_plan_semantics_available"] is not False \
            or ap["missing_collection_actions"] != []:
        raise ValueError("AP completed-evidence/fail-open status mismatch")
    ap_registry = json.loads(verify_ref(ap["workload_registry"]).read_text(encoding="utf-8"))
    if ap_registry["workload_seed"] != 4508333 \
            or ap_registry["mysql_generic_custom_semantics_available"] is not False \
            or len(ap_registry["queries"]) != 14:
        raise ValueError("AP production workload registry mismatch")
    ap_bundle = json.loads(verify_ref(ap["canonical_plan_bundle"]).read_text(encoding="utf-8"))
    if validate_canonical_plan_bundle(ap_bundle)["valid"] is not True \
            or ap_bundle["strict_cluster_usable"] is not False \
            or ap_bundle["plan_similarity_cluster_id"] is not None \
            or ap_bundle["strict_canonical_physical_bundle_sha256"] is not None \
            or not all(reason.startswith("generic_plan_semantics_unavailable:")
                       for reason in ap_bundle["strict_cluster_ineligible_reasons"]):
        raise ValueError("AP canonical bundle does not fail open")
    ap_inventory = json.loads(verify_ref(ap["raw_explain_inventory"]).read_text(encoding="utf-8"))
    if ap_inventory["raw_explain_file_count"] != 56 \
            or ap_inventory["all_56_raw_explain_files_retained"] is not True:
        raise ValueError("AP raw EXPLAIN inventory mismatch")
    ap_final = json.loads(verify_ref(ap["production_binding_and_plan_evidence"]).read_text(encoding="utf-8"))
    if ap_final["mysql_dynamic_plan_cluster_deletion_authorized"] is not False:
        raise ValueError("AP final evidence authorizes candidate deletion")
    published = {row["benchmark"]: row for row in audit["published_workload_registries"]}
    if set(published) != {"hybench_ap", "job"}:
        raise ValueError("published workload registry set mismatch")
    for ref in published.values():
        verify_ref(ref)
    for name in ("tpcc", "tpch", "hybench_xp"):
        if scenarios[name]["decision"] != "FAIL_CLOSED_STRICT_PLAN_REGISTRY_NOT_PUBLISHABLE":
            raise ValueError(f"{name} is not failed closed")
    job = verify_ref(scenarios["job"]["workload_registry"])
    job_doc = json.loads(job.read_text(encoding="utf-8"))
    if job_doc["benchmark"] != "job" or len(job_doc["queries"]) != 9:
        raise ValueError("JOB workload registry mismatch")
    xp = json.loads(verify_ref(scenarios["hybench_xp"]["template_sql_count_only"]).read_text(encoding="utf-8"))
    if xp["passed"] is not True or xp["scope"] != "template_sql_count_closure_not_R0_not_SGD":
        raise ValueError("XP SQL-count evidence scope mismatch")
    print(json.dumps({"ap_evidence_sha256": evidence_sha, "status": "PASS", "status_audit_sha256": audit_sha}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error), "status": "FAIL"}, sort_keys=True))
        raise SystemExit(1)
