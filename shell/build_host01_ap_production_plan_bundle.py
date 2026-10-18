#!/usr/bin/env python3
"""Build the AP production binding registry and complete fail-open plan bundle."""

from __future__ import annotations

from collections import defaultdict
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
ORDER = ["AP-1", "AP-2-RAND", "AP-2"] + [f"AP-{i}" for i in range(3, 14)]
DYNAMIC = {"AP-1", "AP-2-RAND", "AP-2", "AP-3", "AP-4", "AP-5", "AP-7", "AP-12", "AP-13"}
WEIGHTS = {query_id: 6.0 for query_id in ORDER}
WEIGHTS["AP-10"] = 28.0


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write(path: Path, value: Any) -> str:
    payload = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def binding_values(row: dict[str, Any]) -> list[Any]:
    return [binding["value"] for binding in row["actual_bindings"]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    run_root = args.run_root.resolve()
    trace_path = run_root / "artifacts/production_server_binding_trace.jsonl"
    rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_query[row["query_id"]].append(row)
    if set(by_query) != set(ORDER):
        raise ValueError("production trace query set mismatch")

    registry_rows = []
    binding_ids: dict[str, list[str]] = {}
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for order, query_id in enumerate(ORDER, 1):
        captures = sorted(by_query[query_id], key=lambda row: row["connection_id"])
        template_hashes = {row["template_sql_sha256"] for row in captures}
        if len(template_hashes) != 1:
            raise ValueError(f"template hash mismatch: {query_id}")
        representatives = []
        if query_id in DYNAMIC:
            for row in captures:
                values = binding_values(row)
                value_hash = hashlib.sha256(canonical_bytes(values)).hexdigest()[:16]
                binding_id = f"observed_{value_hash}"
                selected.setdefault((query_id, binding_id), row)
            for (candidate_query, binding_id), row in sorted(selected.items()):
                if candidate_query == query_id:
                    representatives.append({"binding_id": binding_id, "values": binding_values(row)})
            binding_ids[query_id] = [row["binding_id"] for row in representatives]
        else:
            binding_ids[query_id] = ["static"]
            selected[(query_id, "static")] = captures[0]
        registry_rows.append({
            "dynamic": query_id in DYNAMIC,
            "order": order,
            "query_id": query_id,
            "representative_bindings": representatives,
            "required_plan_modes": ["generic", "custom"] if query_id in DYNAMIC else [],
            "sql_sha256": next(iter(template_hashes)),
            "weight": WEIGHTS[query_id],
        })

    registry = {
        "benchmark": "hybench_ap",
        "exact_frozen_production_jar_sha256": "6e615d1b704da0b095266fd5592fc72245a9c5034707a37ef91abfa65a7fc7f4",
        "freeze_id": "frozen_mysql_workloads_and_parameters_20260723_v3",
        "immutable": True,
        "logical_query_weight_sum": 100.0,
        "mysql_generic_custom_semantics_available": False,
        "physical_sql_weight_sum_including_ap2_selector": 106.0,
        "queries": registry_rows,
        "schema_version": "mysql_plan_workload_registry_v1",
        "source_binding_trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
        "workload_seed": 4508333,
    }
    registry_path = REGISTRY_DIR / "hybench_ap.mysql_plan_workload_registry_v1.json"
    registry_sha = write(registry_path, registry)

    captures = []
    raw_inventory = []
    for query_order, query_id in enumerate(ORDER, 1):
        for binding_id in binding_ids[query_id]:
            row = selected[(query_id, binding_id)]
            prefix = f"{rows.index(row) + 1:02d}_{row['connection_id']}_{row['connection_sequence']:02d}_{query_id}"
            raw_path = run_root / "artifacts/raw_explain" / f"{prefix}.json"
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            raw_inventory.append({
                "binding_id": binding_id, "path": str(raw_path), "query_id": query_id,
                "raw_file_sha256": raw_sha,
            })
            captures.append({
                "binding_id": binding_id,
                "binding_values": binding_values(row) if query_id in DYNAMIC else None,
                "dynamic": query_id in DYNAMIC,
                "generic_plan_semantics_available": False if query_id in DYNAMIC else None,
                "plan_mode": "custom",
                "query_id": query_id,
                "query_order": query_order,
                "query_sql_sha256": row["template_sql_sha256"],
                "query_weight": WEIGHTS[query_id],
                "raw_explain": raw,
                "required_binding_ids": binding_ids[query_id] if query_id in DYNAMIC else [],
                "required_plan_modes": ["generic", "custom"] if query_id in DYNAMIC else [],
            })
    bundle = build_canonical_plan_bundle(
        captures, registry_query_ids=ORDER, registry_sha256=registry_sha,
        registry_authority_verified=True,
    )
    validation = validate_canonical_plan_bundle(bundle)
    if validation["valid"] is not True:
        raise ValueError(f"canonical bundle invalid: {validation}")
    expected_reasons = sorted(
        f"generic_plan_semantics_unavailable:{query_id}" for query_id in ORDER if query_id in DYNAMIC
    )
    if bundle["strict_cluster_usable"] is not False \
            or bundle["strict_cluster_ineligible_reasons"] != expected_reasons \
            or bundle["plan_similarity_cluster_id"] is not None \
            or bundle["strict_canonical_physical_bundle_sha256"] is not None:
        raise ValueError(
            "dynamic MySQL bundle did not fail open exactly as required: "
            + json.dumps({
                "expected_reasons": expected_reasons,
                "plan_similarity_cluster_id": bundle["plan_similarity_cluster_id"],
                "reasons": bundle["strict_cluster_ineligible_reasons"],
                "strict_canonical_physical_bundle_sha256": bundle[
                    "strict_canonical_physical_bundle_sha256"
                ],
                "strict_cluster_usable": bundle["strict_cluster_usable"],
            }, sort_keys=True)
        )
    bundle_path = run_root / "artifacts/canonical_plan_bundle.json"
    bundle_sha = write(bundle_path, bundle)
    inventory_sha = write(run_root / "artifacts/raw_explain_inventory.json", {
        "all_56_raw_explain_files_retained": True,
        "bundle_selected_capture_count": len(captures),
        "inventory": raw_inventory,
        "raw_explain_file_count": 56,
        "schema_version": "mysql_ap_raw_explain_inventory_v1",
    })
    evidence = {
        "binding_registry": {"path": str(registry_path), "sha256": registry_sha},
        "canonical_plan_bundle": {"path": str(bundle_path), "sha256": bundle_sha},
        "exact_frozen_production_jar_sha256": registry["exact_frozen_production_jar_sha256"],
        "generic_custom_plan_semantics_claimed": False,
        "mysql_dynamic_plan_cluster_deletion_authorized": False,
        "plan_similarity_cluster_id": None,
        "raw_explain_inventory_sha256": inventory_sha,
        "schema_version": "mysql_ap_production_binding_and_plan_evidence_v1",
        "status": "BINDING_REGISTRY_COMPLETE_RAW_PLANS_COMPLETE_STRICT_CLUSTER_FAIL_OPEN",
        "strict_cluster_ineligible_reasons": bundle["strict_cluster_ineligible_reasons"],
        "workload_seed": 4508333,
    }
    evidence_sha = write(run_root / "production_binding_and_plan_evidence.json", evidence)
    print(json.dumps({
        "bundle_sha256": bundle_sha, "evidence_sha256": evidence_sha,
        "registry_sha256": registry_sha, "status": "PASS_FAIL_OPEN",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
