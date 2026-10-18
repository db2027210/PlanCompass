#!/usr/bin/env python3
"""Offline verifier for frozen mysql_plan_workload_registry_v1 artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trained_model_estimator import MySQLExplainFeatureProvider  # noqa: E402


DEFAULT_DIR = (
    ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries"
)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def verify_sidecar(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = f"{digest}  {path.name}\n"
    sidecar = path.with_name(path.name + ".sha256")
    if sidecar.read_text(encoding="ascii") != expected:
        raise ValueError(f"invalid SHA sidecar: {sidecar}")
    return digest


def verify_source_ref(ref: dict[str, Any]) -> None:
    path = Path(ref["path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
        raise ValueError(f"binding source SHA mismatch: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    start, end = int(ref["line_start"]), int(ref["line_end"])
    payload = ("\n".join(lines[start - 1:end]) + "\n").encode()
    if start < 1 or end < start or end > len(lines) or hashlib.sha256(payload).hexdigest() != ref["line_sha256"]:
        raise ValueError(f"binding source line citation mismatch: {path}:{start}-{end}")


def verify(directory: Path) -> dict[str, Any]:
    registry_path = directory / "job.mysql_plan_workload_registry_v1.json"
    feature_path = directory / "job.trained_estimator_feature_source.json"
    audit_path = directory / "registry_build_audit.json"
    binding_gap_path = directory / "binding_authority_gap_audit.json"
    for path in (registry_path, feature_path, audit_path, binding_gap_path):
        if not path.is_file():
            raise ValueError(f"missing artifact: {path}")
        document = json.loads(path.read_text(encoding="utf-8"))
        if path.read_bytes() != canonical_bytes(document):
            raise ValueError(f"artifact is not canonical JSON: {path}")
        verify_sidecar(path)

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    feature = json.loads(feature_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    binding_gap = json.loads(binding_gap_path.read_text(encoding="utf-8"))
    if registry.get("schema_version") != "mysql_plan_workload_registry_v1":
        raise ValueError("wrong registry schema")
    if registry.get("immutable") is not True or registry.get("benchmark") != "job":
        raise ValueError("registry is not immutable JOB authority")
    rows = registry.get("queries")
    if not isinstance(rows, list) or len(rows) != 9:
        raise ValueError("JOB registry must contain exactly nine ordered statements")
    feature_rows = feature.get("queries")
    if not isinstance(feature_rows, list) or len(feature_rows) != len(rows):
        raise ValueError("feature source does not exactly cover registry")
    for order, (row, source) in enumerate(zip(rows, feature_rows), 1):
        required = {
            "order", "query_id", "sql_sha256", "weight", "dynamic",
            "representative_bindings", "required_plan_modes",
        }
        if set(row) != required or row["order"] != order:
            raise ValueError(f"invalid registry fields/order at row {order}")
        if not isinstance(row["query_id"], str) or not row["query_id"]:
            raise ValueError(f"invalid query_id at row {order}")
        if not isinstance(row["weight"], float) or not math.isfinite(row["weight"]) or row["weight"] <= 0:
            raise ValueError(f"weight must be a positive JSON float at row {order}")
        if row["dynamic"] is not False or row["representative_bindings"] != []:
            raise ValueError(f"JOB row unexpectedly claims dynamic bindings at row {order}")
        if row["required_plan_modes"] != []:
            raise ValueError(f"static MySQL row invents a generic/custom requirement at row {order}")
        sql = source.get("sql")
        if source.get("query_id") != row["query_id"] or source.get("weight") != row["weight"]:
            raise ValueError(f"feature-source identity/weight mismatch at row {order}")
        if not isinstance(sql, str) or not sql.upper().startswith(("SELECT", "WITH")) or "?" in sql:
            raise ValueError(f"feature SQL is not static read-only SQL at row {order}")
        if hashlib.sha256(sql.encode()).hexdigest() != row["sql_sha256"]:
            raise ValueError(f"SQL hash mismatch at row {order}")

    registry_sha = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    if (
        feature.get("workload_registry_path") != str(registry_path.resolve())
        or feature.get("workload_registry_sha256") != registry_sha
        or feature.get("registry_query_ids") != [row["query_id"] for row in rows]
        or feature.get("require_strict_plan_registry") is not True
    ):
        raise ValueError("feature source is not bound to the exact registry")
    if not math.isclose(float(feature.get("active_sql_count")), sum(row["weight"] for row in rows)):
        raise ValueError("feature-source active_sql_count mismatch")
    MySQLExplainFeatureProvider(object(), feature)

    if audit.get("generated_without_database_access") is not True:
        raise ValueError("build audit does not assert offline construction")
    if [row.get("benchmark") for row in audit.get("published", [])] != ["job"]:
        raise ValueError("unexpected published benchmark set")
    unpublished = {row.get("benchmark"): row for row in audit.get("unpublished", [])}
    if set(unpublished) != {"hybench_ap", "tpch", "tpcc", "hybench_xp"}:
        raise ValueError("gap audit does not cover every unpublished benchmark")
    if any(row.get("decision") != "FAIL_CLOSED_NOT_PUBLISHED" for row in unpublished.values()):
        raise ValueError("an incomplete benchmark was not failed closed")
    if (
        binding_gap.get("schema_version") != "mysql_binding_authority_gap_audit_v1"
        or binding_gap.get("generated_without_database_access") is not True
        or binding_gap.get("mysql_generic_custom_semantics_claimed") is not False
        or binding_gap.get("mysql_dynamic_plan_cluster_deletion_authorized") is not False
    ):
        raise ValueError("binding-authority audit does not fail closed")
    gaps = {row.get("benchmark"): row for row in binding_gap.get("benchmarks", [])}
    if set(gaps) != {"hybench_ap", "tpch"}:
        raise ValueError("binding-authority audit must cover exactly AP and TPC-H")
    ap_rows = gaps["hybench_ap"].get("queries")
    tpch_rows = gaps["tpch"].get("queries")
    if not isinstance(ap_rows, list) or len(ap_rows) != 13 or not isinstance(tpch_rows, list) or len(tpch_rows) != 22:
        raise ValueError("binding-authority audit has incomplete query coverage")
    dynamic_ap = {f"AP-{value}" for value in (1, 2, 3, 4, 5, 7, 12, 13)}
    for row in ap_rows:
        verify_source_ref(row["sql_source"])
        is_dynamic = row["query_id"] in dynamic_ap
        if row.get("dynamic") is not is_dynamic or row.get("representative_bindings") != []:
            raise ValueError(f"invalid AP binding closure: {row.get('query_id')}")
        if is_dynamic:
            verify_source_ref(row["binding_generator_source"])
            if row.get("closure") != "MISSING_AUTHORITATIVE_CONCRETE_BINDING":
                raise ValueError(f"dynamic AP query did not fail closed: {row['query_id']}")
        elif row.get("closure") != "STATIC_NO_BINDING_REQUIRED":
            raise ValueError(f"static AP query classified incorrectly: {row['query_id']}")
    for ref in gaps["hybench_ap"]["global_seed_sources"]:
        verify_source_ref(ref)
    for row in tpch_rows:
        verify_source_ref(row["binding_generator_source"])
        if (
            row.get("dynamic") is not True
            or row.get("representative_bindings") != []
            or row.get("closure") != "MISSING_AUTHORITATIVE_CONCRETE_BINDING"
        ):
            raise ValueError(f"TPC-H query did not fail closed: {row.get('query_id')}")
    trace = gaps["tpch"].get("trace_audit", {})
    if trace.get("binding_columns_present") is not False or trace.get("missing_completed_query_ids") != ["Q9", "Q15"]:
        raise ValueError("TPC-H trace gap is not the audited Q9/Q15 binding gap")
    for ref in gaps["tpch"]["runtime_seed_chain"].values():
        if "line_start" in ref:
            verify_source_ref(ref)
        elif hashlib.sha256(Path(ref["path"]).read_bytes()).hexdigest() != ref["sha256"]:
            raise ValueError(f"TPC-H runtime seed artifact SHA mismatch: {ref['path']}")
    for ref in trace.get("traces", []):
        if hashlib.sha256(Path(ref["path"]).read_bytes()).hexdigest() != ref["sha256"]:
            raise ValueError(f"TPC-H raw trace SHA mismatch: {ref['path']}")
    return {
        "audit_sha256": verify_sidecar(audit_path),
        "binding_authority_gap_audit_sha256": verify_sidecar(binding_gap_path),
        "feature_source_sha256": verify_sidecar(feature_path),
        "registry_sha256": registry_sha,
        "status": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()
    try:
        result = verify(args.directory.resolve())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
