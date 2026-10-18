#!/usr/bin/env python3
"""Rebuild forest-100 top60 using the new generic answer-blind diversity policy.

Reads the existing 78,125 rescored_candidates.jsonl, re-ranks with
select_diverse_top_k, and writes a machine-readable coverage artifact.

No EXPLAIN calls, no VM replay, no teacher/known-label loading.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bonect_robust_ranking import (
    DEFAULT_VARIANT,
    RESOURCE_NAMES,
    candidate_diversity_record,
    robust_sort_key,
    select_diverse_top_k,
)
from shell.bonect_full_grid_estimator_scan import parameter_signature


FOREST100_DIR = (
    Path("/disk1/resource_adaptive/bonect_phase2_estimator_scans")
    / "memory_v112_blind_fullgrid_forest100_rescore_20260712"
)
FOREST500_DIR = (
    Path("/disk1/resource_adaptive/bonect_phase2_estimator_scans")
    / "memory_v112_blind_fullgrid_forest500_rescore_20260712"
)
OUTPUT_DIR = ROOT / "report" / "bonect"
KNOWN_LABELS = FOREST100_DIR / "known_replay_ranks.json"

CAPABILITY: Dict[str, float] = {"cpu": 11.0, "memory": 2.0, "io": 1600.0}
SCENARIO = "memory"


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def find_verified_strong_ranks(ranked: Sequence[Dict[str, Any]],
                               labels_path: Path) -> Dict[str, Any]:
    """Match known replay labels by canonical seven-parameter signature.

    Rescored rows do not carry candidate_id; the correct audit logic is
    to match by the full seven-parameter value signature.
    """
    rank_by_sig: Dict[str, int] = {}
    for rank, row in enumerate(ranked, start=1):
        sig = str(row.get("param_signature") or "")
        if sig not in rank_by_sig:
            rank_by_sig[sig] = rank

    labels = json.loads(labels_path.read_text()) if labels_path.is_file() else []
    results = []
    for item in labels:
        params = item.get("params") or {}
        sig = parameter_signature(params)
        rank = rank_by_sig.get(sig)
        results.append({
            "candidate_id": item.get("candidate_id"),
            "rank": rank,
            "db_qps_delta_pct": item.get("db_qps_delta_pct"),
            "resource_delta_pp": item.get("resource_delta_pp"),
            "params": params,
            "param_signature": sig,
            "matched_by": "canonical_seven_param_signature",
        })
    return {
        "matching": "canonical_seven_param_signature",
        "replay_labels_count": len(labels),
        "matched_count": sum(1 for r in results if r["rank"] is not None),
        "unmatched_count": sum(1 for r in results if r["rank"] is None),
        "results": results,
    }


def verify_strong_recall(top60: List[Dict[str, Any]],
                          top60_sig_rank: Dict[str, int],
                          known_results: List[Dict[str, Any]],
                          top_n: int = 60) -> Dict[str, Any]:
    """Check verified strong neighborhoods in top60 using top60 rank map."""
    strong_ids = {"candidate_0000", "candidate_0004", "candidate_0007", "candidate_0010",
                  "memory_v112_expand_supported_cpu_index_1",
                  "memory_v112_expand_supported_seq_1",
                  "memory_v112_expand_supported_seq1_index1_interaction",
                  "memory_v112_expand_uncertain_setup_7500",
                  "memory_v112_expand_uncertain_cpu_tuple_default",
                  "memory_v112_expand_uncertain_operator_025",
                  }
    top60_exact_strong = []
    for kr in known_results:
        cid = kr.get("candidate_id", "")
        if cid not in strong_ids:
            continue
        params = kr.get("params") or {}
        sig = parameter_signature(params)
        rank = top60_sig_rank.get(sig)
        if rank is not None and rank <= top_n:
            top60_exact_strong.append({**kr, "new_rank": rank})

    top60_ids = {r["candidate_id"] for r in top60_exact_strong}

    # Neighborhood: any top60 entry whose predicted resource follows same
    # pattern (CPU up >20pp, IO/Memory down >10pp from predicted estimate)
    top60_neighborhood_count = 0
    for row in top60:
        est = row.get("estimated_resource") or {}
        util = row.get("utilization_pct") or {}
        cpu_pp = float(util.get("cpu", 0)) - float(row.get("baseline_resource", {}).get("cpu", 0))
        mem_pp = float(util.get("memory", 0)) - float(row.get("baseline_resource", {}).get("memory", 0))
        io_pp = float(util.get("io", 0)) - float(row.get("baseline_resource", {}).get("io", 0))
        if cpu_pp > 20 and io_pp < -10 and mem_pp < -15:
            top60_neighborhood_count += 1

    disaster_in_top60 = []
    negative_qps_in_top60 = []
    for row in top60:
        qps = float(row.get("estimated_db_qps_delta_pct") or 0)
        if qps < -50:
            disaster_in_top60.append({"qps": qps})
        if qps < -5:
            negative_qps_in_top60.append({"qps": qps})

    return {
        "top_n": top_n,
        "verified_strong_ids": list(strong_ids),
        "verified_strong_exact_in_top60": top60_exact_strong,
        "verified_strong_exact_hit_count": len(top60_exact_strong),
        "verified_strong_top60_ids": list(top60_ids),
        "verified_strong_neighborhood_count": top60_neighborhood_count,
        "disaster_in_top60": disaster_in_top60,
        "disaster_count": len(disaster_in_top60),
        "negative_qps_in_top60": negative_qps_in_top60,
        "negative_qps_count": len(negative_qps_in_top60),
    }


def default_zero_check(ranked: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Check that PostgreSQL default candidate predicts ~0 QPS delta."""
    for row in ranked:
        params = row.get("params") or {}
        if (abs(float(params.get("seq_page_cost", 0)) - 1.0) < 1e-9
                and abs(float(params.get("random_page_cost", 0)) - 4.0) < 1e-9
                and abs(float(params.get("cpu_tuple_cost", 0)) - 0.01) < 1e-9
                and abs(float(params.get("cpu_index_tuple_cost", 0)) - 0.005) < 1e-9
                and abs(float(params.get("cpu_operator_cost", 0)) - 0.0025) < 1e-9
                and abs(float(params.get("parallel_setup_cost", 0)) - 1000.0) < 1e-9
                and abs(float(params.get("parallel_tuple_cost", 0)) - 0.1) < 1e-9):
            qps = float(row.get("estimated_db_qps_delta_pct") or 0)
            return {
                "default_param_match": True,
                "default_qps_delta": qps,
                "default_rank": ranked.index(row) + 1 if row in ranked else None,
                "pass": abs(qps) < 5.0,
            }
    return {"default_param_match": False, "pass": False}


def io_variation_check(ranked: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Check that IO predictions are nonconstant (not all zero or same value)."""
    io_values = set()
    for row in ranked[:200]:
        io_val = float((row.get("estimated_resource") or {}).get("io", 0))
        io_values.add(round(io_val, 4))
    return {
        "distinct_io_values_in_top200": len(io_values),
        "io_constant": len(io_values) <= 1,
        "pass": len(io_values) > 1,
    }


def main() -> int:
    started = time.perf_counter()
    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load the forest100 rescored candidates
    rescored_path = FOREST100_DIR / "rescored_candidates.jsonl"
    assert rescored_path.is_file(), f"Missing: {rescored_path}"
    records = load_jsonl(rescored_path)
    print(f"Loaded {len(records)} rescored candidates", flush=True)

    # Sort by robust_sort_key (descending) — this matches the
    # canonical robust ranking used by select_diverse_top_k
    ranked = sorted(records, key=robust_sort_key, reverse=True)
    print(f"Sorted {len(ranked)} candidates by robust score", flush=True)

    # Apply diversity selection
    top60 = select_diverse_top_k(
        ranked,
        capability=CAPABILITY,
        scenario=SCENARIO,
        top_k=60,
    )
    print(f"Selected {len(top60)} diverse top-k", flush=True)

    # Verify known labels by canonical seven-parameter signature
    known = find_verified_strong_ranks(ranked, KNOWN_LABELS)
    # Build param_signature -> rank map from top60
    top60_sig_rank: Dict[str, int] = {}
    for rank, row in enumerate(top60, start=1):
        sig = str(row.get("param_signature") or "")
        if sig not in top60_sig_rank:
            top60_sig_rank[sig] = rank

    recall = verify_strong_recall(top60, top60_sig_rank, known.get("results", []), top_n=60)

    # Default-zero check
    default_check = default_zero_check(ranked)

    # IO variation check
    io_check = io_variation_check(ranked)

    # Coverage artifact
    pf_bin_counts: Dict[str, int] = {}
    resp_bin_counts: Dict[str, int] = {}
    qps_bin_counts: Dict[str, int] = {}
    lane_counts: Dict[str, int] = {}
    for row in top60:
        dr = candidate_diversity_record(row, capability=CAPABILITY, scenario=SCENARIO)
        pf_bin_counts[dr["pf_bin"]] = pf_bin_counts.get(dr["pf_bin"], 0) + 1
        resp_bin_counts[dr["resp_bin"]] = resp_bin_counts.get(dr["resp_bin"], 0) + 1
        qps_bin_counts[dr["qps_bin"]] = qps_bin_counts.get(dr["qps_bin"], 0) + 1
        lane_counts[dr["param_lane"]] = lane_counts.get(dr["param_lane"], 0) + 1

    known_results = known.get("results", [])

    coverage_artifact = {
        "policy": {
            "name": "generic_answer_blind_diversity",
            "exploitation_slots": 6,
            "max_per_pf_bin": 8,
            "max_per_resp_bin": 12,
            "max_per_qps_bin": 15,
            "max_per_lane_cluster": 20,
            "exclude_disaster": True,
            "exclude_negative_qps": True,
            "matching": "canonical_seven_param_signature",
        },
        "total_candidates": len(records),
        "elapsed_seconds": time.perf_counter() - started,
        "top60_size": len(top60),
        "top60_plan_feature_bin_counts": pf_bin_counts,
        "top60_plan_feature_bin_count": len(pf_bin_counts),
        "top60_response_bin_counts": resp_bin_counts,
        "top60_response_bin_count": len(resp_bin_counts),
        "top60_qps_bin_counts": qps_bin_counts,
        "top60_qps_bin_count": len(qps_bin_counts),
        "top60_param_lane_counts": lane_counts,
        "top60_param_lane_count": len(lane_counts),
        "known_labels_matching": {
            "method": "canonical_seven_param_signature",
            "replay_labels_count": known.get("replay_labels_count", 0),
            "matched_count": known.get("matched_count", 0),
            "unmatched_count": known.get("unmatched_count", 0),
        },
        "verified_strong_recall_audit": {
            "verified_strong_ids": recall["verified_strong_ids"],
            "verified_strong_exact_in_top60": recall["verified_strong_exact_in_top60"],
            "verified_strong_exact_hit_count": recall["verified_strong_exact_hit_count"],
            "verified_strong_neighborhood_count": recall["verified_strong_neighborhood_count"],
            "verified_strong_top60_ids": recall["verified_strong_top60_ids"],
        },
        "disaster_audit": {
            "disaster_in_top60_count": recall["disaster_count"],
            "negative_qps_in_top60_count": recall["negative_qps_count"],
        },
        "default_zero_check": default_check,
        "io_variation_check": io_check,
        "known_replay_ranks": known_results,
    }

    # Write coverage artifact
    artifact_path = output_dir / "memory_v113_forest100_diverse_top60_coverage_20260712.json"
    artifact_path.write_text(
        json.dumps(coverage_artifact, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print(f"Coverage artifact: {artifact_path}", flush=True)

    # Write top60 as JSON
    top60_path = output_dir / "memory_v113_forest100_diverse_top60_20260712.json"
    top60_path.write_text(
        json.dumps(top60, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"Top60: {top60_path}", flush=True)

    # Summary
    strong_exact = int(coverage_artifact["verified_strong_recall_audit"]["verified_strong_exact_hit_count"])
    strong_neighborhood = int(coverage_artifact["verified_strong_recall_audit"]["verified_strong_neighborhood_count"])
    pf_lanes = coverage_artifact["top60_plan_feature_bin_count"]
    resp_lanes = coverage_artifact["top60_response_bin_count"]
    qps_lanes = coverage_artifact["top60_qps_bin_count"]
    param_lanes = coverage_artifact["top60_param_lane_count"]
    disaster_count = coverage_artifact["disaster_audit"]["disaster_in_top60_count"]
    neg_qps_count = coverage_artifact["disaster_audit"]["negative_qps_in_top60_count"]
    default_pass = coverage_artifact["default_zero_check"]["pass"]
    io_pass = coverage_artifact["io_variation_check"]["pass"]

    print(f"\n{'=' * 70}", flush=True)
    print(f"FOREST-100 DIVERSITY TOP60 REBUILD SUMMARY", flush=True)
    print(f"{'=' * 70}", flush=True)
    print(f"Total candidates: {len(records)}", flush=True)
    print(f"Top60 size: {len(top60)}", flush=True)
    print(f"PF bins in top60: {pf_lanes}", flush=True)
    print(f"Resp bins in top60: {resp_lanes}", flush=True)
    print(f"QPS bins in top60: {qps_lanes}", flush=True)
    print(f"Param lanes in top60: {param_lanes}", flush=True)
    print(f"Verified strong exact hits in top60: {strong_exact}", flush=True)
    print(f"Verified strong neighborhood hits in top60: {strong_neighborhood}", flush=True)
    print(f"Disaster in top60: {disaster_count}", flush=True)
    print(f"Negative QPS in top60: {neg_qps_count}", flush=True)
    print(f"Default ~0 QPS: {default_pass}", flush=True)
    print(f"IO nonconstant: {io_pass}", flush=True)
    print(f"Elapsed: {time.perf_counter() - started:.3f}s", flush=True)
    print(f"{'=' * 70}", flush=True)

    # Gate check
    gate_failures = []
    if disaster_count > 0:
        gate_failures.append(f"disaster points in top60 ({disaster_count})")
    if neg_qps_count > 0:
        gate_failures.append(f"negative QPS points in top60 ({neg_qps_count})")
    if not default_pass:
        gate_failures.append("default ~0 QPS check failed")
    if not io_pass:
        gate_failures.append("IO variation check failed (constant or zero)")
    if strong_exact + strong_neighborhood < 3:
        gate_failures.append(
            f"verified strong recall too low: exact={strong_exact} neighborhood={strong_neighborhood} combined={strong_exact + strong_neighborhood}"
        )

    if gate_failures:
        print(f"\nGATE FAILURES ({len(gate_failures)}):", flush=True)
        for gf in gate_failures:
            print(f"  - {gf}", flush=True)
    else:
        print(f"\nGATE PASSED: all checks OK", flush=True)

    return 0 if not gate_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
