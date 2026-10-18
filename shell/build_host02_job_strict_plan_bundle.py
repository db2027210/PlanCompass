#!/usr/bin/env python3
"""Build and seal the nine-query static JOB strict physical-plan bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mysql_plan_clustering import build_canonical_plan_bundle, validate_canonical_plan_bundle  # noqa: E402

REGISTRY_DIR = ROOT / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/plan_workload_registries"
REGISTRY_PATH = REGISTRY_DIR / "job.mysql_plan_workload_registry_v1.json"
FEATURE_PATH = REGISTRY_DIR / "job.trained_estimator_feature_source.json"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write(path: Path, value: Any) -> str:
    payload = canonical_bytes(value)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    run_root = args.run_root.resolve()
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    feature = json.loads(FEATURE_PATH.read_text(encoding="utf-8"))
    registry_sha = hashlib.sha256(REGISTRY_PATH.read_bytes()).hexdigest()
    feature_by_id = {row["query_id"]: row for row in feature["queries"]}
    captures = []
    inventory = []
    for row in registry["queries"]:
        query_id = row["query_id"]
        raw_path = run_root / "artifacts/raw_explain" / f"{row['order']:02d}_{query_id}.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        if hashlib.sha256(feature_by_id[query_id]["sql"].encode()).hexdigest() != row["sql_sha256"]:
            raise ValueError(f"registry/feature SQL mismatch: {query_id}")
        inventory.append({"path": str(raw_path), "query_id": query_id, "sha256": raw_sha})
        captures.append({
            "binding_id": "static",
            "binding_values": None,
            "dynamic": False,
            "plan_mode": "custom",
            "query_id": query_id,
            "query_order": row["order"],
            "query_sql_sha256": row["sql_sha256"],
            "query_weight": row["weight"],
            "raw_explain": raw,
            "required_binding_ids": [],
            "required_plan_modes": [],
        })
    bundle = build_canonical_plan_bundle(
        captures,
        registry_query_ids=[row["query_id"] for row in registry["queries"]],
        registry_sha256=registry_sha,
        registry_authority_verified=True,
    )
    validation = validate_canonical_plan_bundle(bundle)
    if validation["valid"] is not True or bundle["strict_cluster_usable"] is not True \
            or bundle["strict_cluster_ineligible_reasons"] != [] \
            or not bundle["plan_similarity_cluster_id"] \
            or bundle["plan_similarity_cluster_id"] != bundle["strict_canonical_physical_bundle_sha256"]:
        raise ValueError(f"JOB strict bundle invalid/ineligible: {validation} {bundle['strict_cluster_ineligible_reasons']}")
    bundle_path = run_root / "artifacts/canonical_plan_bundle.json"
    bundle_sha = write(bundle_path, bundle)
    inventory_sha = write(run_root / "artifacts/raw_explain_inventory.json", {
        "inventory": inventory,
        "raw_explain_count": 9,
        "schema_version": "mysql_job_raw_explain_inventory_v1",
    })
    evidence = {
        "canonical_plan_bundle": {"path": str(bundle_path), "sha256": bundle_sha},
        "generic_custom_plan_semantics_claimed": False,
        "plan_similarity_cluster_id": bundle["plan_similarity_cluster_id"],
        "raw_explain_count": 9,
        "raw_explain_inventory_sha256": inventory_sha,
        "registry": {"path": str(REGISTRY_PATH), "sha256": registry_sha},
        "schema_version": "mysql_job_strict_plan_evidence_v1",
        "status": "STRICT_STATIC_PLAN_BUNDLE_COMPLETE",
        "strict_cluster_usable": True,
    }
    evidence_sha = write(run_root / "job_strict_plan_evidence.json", evidence)
    print(json.dumps({
        "bundle_sha256": bundle_sha, "evidence_sha256": evidence_sha,
        "plan_similarity_cluster_id": bundle["plan_similarity_cluster_id"],
        "status": "PASS", "strict_cluster_usable": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
