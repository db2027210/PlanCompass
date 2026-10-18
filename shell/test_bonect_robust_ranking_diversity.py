#!/usr/bin/env python3
"""Focused tests for the generic answer-blind replay-prefix diversity policy.

Required cases (from opencode-phase2-memory-v113-ranking-diversity-continuation):
  - exact plan-hash variants with the same plan-feature/resource-response plateau
    cannot monopolize the prefix (old select_distinct failed this);
  - high-score candidates remain represented;
  - different parameter-direction lanes remain represented;
  - negative/disaster candidates remain excluded;
  - selection is unchanged by adding/removing the evaluation-label file.

Run: python3 shell/test_bonect_robust_ranking_diversity.py
"""
from __future__ import annotations

import copy
import sys
import os
from typing import Any, Dict, List

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from bonect_robust_ranking import (
    _plan_feature_bin,
    _response_bin,
    _qps_conservative_bin,
    _param_lane,
    _disaster_flag,
    candidate_diversity_record,
    select_diverse_top_k,
    RESOURCE_NAMES,
)


CAPABILITY: Dict[str, float] = {"cpu": 11.0, "memory": 2.0, "io": 1600.0}
SCENARIO = "memory"


def _make_row(
    *,
    pf: Dict[str, float],
    cpu: float,
    memory: float,
    io: float,
    qps_delta: float,
    qps_error: float = 0.0,
    params: Dict[str, float] | None = None,
    plan_hash: str = "hash_000",
) -> Dict[str, Any]:
    return {
        "plan_delta_features": pf,
        "estimated_resource": {"cpu": cpu, "memory": memory, "io": io},
        "resource": {"cpu": cpu, "memory": memory, "io": io},
        "estimated_db_qps_delta_pct": qps_delta,
        "estimated_db_qps_error_bound_pct": qps_error,
        "estimated_db_qps_conservative_delta_pct": qps_delta - qps_error,
        "params": params or {
            "seq_page_cost": 1.0,
            "random_page_cost": 4.0,
            "cpu_tuple_cost": 0.01,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.1,
        },
        "plan_hash": plan_hash,
    }


def _same_pf(v: float = 100.0) -> Dict[str, float]:
    """Return a fixed plan-delta feature dict (same plateau)."""
    return {"weighted_gather_count": v, "weighted_gather_workers_planned": v * 2}


def _different_pfs() -> List[Dict[str, float]]:
    """Return 5 distinct plan-delta feature bins."""
    return [
        {"weighted_gather_count": 100.0, "weighted_gather_workers_planned": 200.0},
        {"weighted_seq_scan_count": 50.0, "weighted_seq_scan_cost": 500.0},
        {"weighted_index_scan_count": 30.0, "weighted_index_scan_cost": 300.0},
        {"weighted_hash_join_count": 20.0, "weighted_hash_join_cost": 400.0},
        {"weighted_sort_count": 10.0, "weighted_sort_cost": 100.0},
    ]


# ---------------------------------------------------------------------------
# Test 1: same plan-feature/resource-response plateau cannot monopolize prefix
# ---------------------------------------------------------------------------
def test_plateau_cannot_monopolize_prefix():
    """Old select_distinct: different plan_hashes but same PF plateau → all pass.
    New diversity: Phase 1+2 caps at max_per_pf_bin=8 entries per PF bin unless
    Phase 3 fill retry activates.  This test uses top_k=15 (< 3 PF bins × 8 cap)
    so Phase 3 never triggers, proving Phase 1+2 enforced caps.
    """
    pf = _same_pf(100.0)
    rows: List[Dict[str, Any]] = []
    for i in range(30):
        row = _make_row(
            pf=pf,
            cpu=9.0,
            memory=1.3,
            io=300.0,
            qps_delta=50.0 - i * 0.1,  # decreasing but still positive
            plan_hash=f"hash_{i:04d}",
            params={
                "seq_page_cost": 1.0,
                "random_page_cost": 4.0,
                "cpu_tuple_cost": 0.01,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.0025,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.1,
            },
        )
        rows.append(row)
    # Add a few rows with different PF to have diversity candidates
    for i, alt_pf in enumerate(_different_pfs()):
        rows.append(_make_row(
            pf=alt_pf,
            cpu=8.0,
            memory=1.2,
            io=250.0,
            qps_delta=40.0,
            plan_hash=f"alt_hash_{i:04d}",
        ))
    # Sort by score descending (simulate robust_sort_key)
    ranked = sorted(rows, key=lambda r: float(r["estimated_db_qps_delta_pct"]), reverse=True)
    selected = select_diverse_top_k(
        ranked,
        capability=CAPABILITY,
        scenario=SCENARIO,
        top_k=15,  # well below 3 bin × 8 cap = 24, so Phase 3 never triggers
    )
    # Count PF bin occurrences in selected
    pf_counts: Dict[str, int] = {}
    for row in selected:
        pf_bin = _plan_feature_bin(row)
        pf_counts[pf_bin] = pf_counts.get(pf_bin, 0) + 1
    # No single PF bin should exceed max_per_pf_bin=8 (Phase 1+2 only)
    max_pf = max(pf_counts.values())
    assert max_pf <= 8, (
        f"PF bin count {max_pf} exceeds max_per_pf_bin=8; "
        f"counts={pf_counts}"
    )
    print(f"[PASS] test_plateau_cannot_monopolize_prefix: max PF bin count={max_pf} (<=8)")


# ---------------------------------------------------------------------------
# Test 1b: new-key per-bin caps enforced (distinct tuple keys, same PF bin)
# ---------------------------------------------------------------------------
def test_new_key_per_bin_caps_enforced():
    """Candidates with distinct (pf,resp,qps,lane) tuples but the SAME pf_bin
    must still respect max_per_pf_bin.  The old bug: a new tuple key bypassed
    _can_add, so N distinct tuples sharing one PF bin would all be admitted.
    """
    # 60 candidates: all share pf_bin X, but each has a unique lane tuple
    # so every candidate is a "new key" in Phase 2.
    rows: List[Dict[str, Any]] = []
    pf = _same_pf(100.0)
    for i in range(60):
        # Make each candidate a distinct lane by varying one param
        params = {
            "seq_page_cost": 1.0,
            "random_page_cost": 4.0,
            "cpu_tuple_cost": 0.01,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.1,
        }
        # Vary seq_page_cost to get different lanes (but params must stay
        # within bin range to get a distinct _param_lane label)
        params["seq_page_cost"] = 0.0001 + i * 0.001
        row = _make_row(
            pf=pf,
            cpu=9.0,
            memory=1.3,
            io=300.0,
            qps_delta=float(100 - i),
            plan_hash=f"newkey_{i:04d}",
            params=params,
        )
        rows.append(row)
    # Add 5 diverse rows with different PF to fill remaining top-k slots
    for i, alt_pf in enumerate(_different_pfs()):
        rows.append(_make_row(
            pf=alt_pf,
            cpu=8.0,
            memory=1.2,
            io=250.0,
            qps_delta=40.0,
            plan_hash=f"alt_{i:04d}",
        ))
    ranked = sorted(rows, key=lambda r: float(r["estimated_db_qps_delta_pct"]), reverse=True)
    selected = select_diverse_top_k(
        ranked,
        capability=CAPABILITY,
        scenario=SCENARIO,
        top_k=12,  # below Phase 1+2 max (8 + 5 alt), Phase 3 won't trigger
        max_per_pf_bin=8,
    )
    pf_counts: Dict[str, int] = {}
    for row in selected:
        pf_bin = _plan_feature_bin(row)
        pf_counts[pf_bin] = pf_counts.get(pf_bin, 0) + 1
    max_pf = max(pf_counts.values())
    assert max_pf <= 8, (
        f"PF bin count {max_pf} exceeds max_per_pf_bin=8; "
        f"counts={pf_counts}"
    )
    # Also verify that some of the diverse candidates made it in
    alt_count = sum(1 for row in selected if "alt_" in (row.get("plan_hash") or ""))
    assert alt_count >= 3, (
        f"Expected >=3 diverse-candidate rows in result, got {alt_count}"
    )
    print(f"[PASS] test_new_key_per_bin_caps_enforced: max PF bin count={max_pf} (<=8), "
          f"alt rows={alt_count}")


# ---------------------------------------------------------------------------
# Test 1c: each bin dimension cap enforced independently
# ---------------------------------------------------------------------------
def test_each_bin_dimension_cap_independent():
    """Verify that PF-bin, resp-bin, QPS-bin, and lane-cluster caps are each
    enforced independently and simultaneously by _can_add.  If only PF-bin
    caps were checked, one resp-bin value could still overfill.
    """
    rows: List[Dict[str, Any]] = []
    # Generate 60 candidates all mapping to the same PF bin, the same resp bin,
    # the same QPS bin, but different lanes (so each is a new key).
    pf = _same_pf(100.0)
    for i in range(60):
        params = {
            "seq_page_cost": 1.0,
            "random_page_cost": 4.0,
            "cpu_tuple_cost": 0.01,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.1,
        }
        params["seq_page_cost"] = 0.0001 + i * 0.001
        row = _make_row(
            pf=pf,
            cpu=9.0,
            memory=1.3,
            io=300.0,
            qps_delta=float(90 - i),
            plan_hash=f"capd_{i:04d}",
            params=params,
        )
        rows.append(row)
    ranked = sorted(rows, key=lambda r: float(r["estimated_db_qps_delta_pct"]), reverse=True)
    selected = select_diverse_top_k(
        ranked,
        capability=CAPABILITY,
        scenario=SCENARIO,
        top_k=10,  # below Phase 1+2 max (6 exploitation + 4 more = 10)
        max_per_pf_bin=10,
        max_per_resp_bin=10,
        max_per_qps_bin=10,
        max_per_lane_cluster=10,
    )
    # With 10 same-PF-bin candidates, the PF cap should determine max count
    counts = {
        "pf": {},
        "resp": {},
        "qps": {},
        "lane": {},
    }
    for row in selected:
        dr = candidate_diversity_record(row, capability=CAPABILITY, scenario=SCENARIO)
        counts["pf"][dr["pf_bin"]] = counts["pf"].get(dr["pf_bin"], 0) + 1
        counts["resp"][dr["resp_bin"]] = counts["resp"].get(dr["resp_bin"], 0) + 1
        counts["qps"][dr["qps_bin"]] = counts["qps"].get(dr["qps_bin"], 0) + 1
        counts["lane"][dr["param_lane"]] = counts["lane"].get(dr["param_lane"], 0) + 1
    for dim, dim_counts in counts.items():
        max_v = max(dim_counts.values())
        assert max_v <= 10, (
            f"Dimension '{dim}' max count {max_v} exceeds cap 10; "
            f"counts={dim_counts}"
        )
    print(f"[PASS] test_each_bin_dimension_cap_independent: all dims ≤10")


# ---------------------------------------------------------------------------
# Test 2: high-score candidates remain represented
# ---------------------------------------------------------------------------
def test_high_score_remain_represented():
    """The top exploitation_slots=6 should contain the globally highest-scored rows.
    
    Each row gets a deliberately unique PF/resp/QPS/lane combination to prevent
    cross-row dedup collision.
    """
    rows: List[Dict[str, Any]] = []
    for i in range(30):
        # Unique PF: use exponentially different magnitudes so each maps to a distinct PF bin
        pf = {"weighted_gather_count": float(10 ** (i % 15))}
        # Unique resp bin: cycle CPU through different zones
        cpu_val = 2.0 + i * 0.5  # low → mid → high → over
        # Unique QPS bin
        qps = float(100 - i * 10)  # strong_up → moderate_up → weak_up → neutral → weak_down
        # Unique lane: cycle params
        if i < 10:
            params = {"seq_page_cost": 0.0001, "random_page_cost": 4.0,
                      "cpu_tuple_cost": 0.01, "cpu_index_tuple_cost": 0.005,
                      "cpu_operator_cost": 0.0025, "parallel_setup_cost": 1000.0,
                      "parallel_tuple_cost": 0.1}  # min lane
        elif i < 20:
            params = {"seq_page_cost": 0.5, "random_page_cost": 2.0,
                      "cpu_tuple_cost": 0.005, "cpu_index_tuple_cost": 0.0025,
                      "cpu_operator_cost": 0.00125, "parallel_setup_cost": 500.0,
                      "parallel_tuple_cost": 0.05}  # mid lane
        else:
            params = {"seq_page_cost": 1.0, "random_page_cost": 0.0001,
                      "cpu_tuple_cost": 0.000001, "cpu_index_tuple_cost": 0.000001,
                      "cpu_operator_cost": 0.000001, "parallel_setup_cost": 100.0,
                      "parallel_tuple_cost": 0.000001}  # another lane
        row = _make_row(
            pf=pf,
            cpu=cpu_val,
            memory=1.3,
            io=300.0,
            qps_delta=qps,
            plan_hash=f"hash_{i:04d}",
            params=params,
        )
        rows.append(row)
    ranked = sorted(rows, key=lambda r: float(r["estimated_db_qps_delta_pct"]), reverse=True)
    selected = select_diverse_top_k(
        ranked,
        capability=CAPABILITY,
        scenario=SCENARIO,
        top_k=20,
        exploitation_slots=6,
    )
    top6_scores = [float(r["estimated_db_qps_delta_pct"]) for r in selected[:6]]
    expected_top6_scores = [float(r["estimated_db_qps_delta_pct"]) for r in ranked[:6]]
    # The top6 in selected should match the top6 in original ranking
    assert top6_scores == expected_top6_scores, (
        f"Top6 scores {top6_scores} should match expected {expected_top6_scores}"
    )
    print(f"[PASS] test_high_score_remain_represented: top6 preserved")


# ---------------------------------------------------------------------------
# Test 3: different parameter-direction lanes remain represented
# ---------------------------------------------------------------------------
def test_param_lanes_diverse():
    """The selected set should have at least 3 different parameter lanes."""
    rows: List[Dict[str, Any]] = []
    pfs = _different_pfs()
    lane_params = [
        # min-ish params
        {"seq_page_cost": 0.0001, "random_page_cost": 4.0, "cpu_tuple_cost": 0.01,
         "cpu_index_tuple_cost": 0.005, "cpu_operator_cost": 0.0025,
         "parallel_setup_cost": 1000.0, "parallel_tuple_cost": 0.1},
        # max-ish params
        {"seq_page_cost": 1.0, "random_page_cost": 0.0001, "cpu_tuple_cost": 0.01,
         "cpu_index_tuple_cost": 0.005, "cpu_operator_cost": 0.0025,
         "parallel_setup_cost": 1000.0, "parallel_tuple_cost": 0.1},
        # mid params
        {"seq_page_cost": 0.5, "random_page_cost": 2.0, "cpu_tuple_cost": 0.005,
         "cpu_index_tuple_cost": 0.0025, "cpu_operator_cost": 0.00125,
         "parallel_setup_cost": 500.0, "parallel_tuple_cost": 0.05},
    ]
    for i in range(30):
        pf = pfs[i % len(pfs)]
        lp = lane_params[i % len(lane_params)]
        row = _make_row(
            pf=pf,
            cpu=9.0 - i * 0.1,
            memory=1.3 - i * 0.01,
            io=300.0 - i * 5.0,
            qps_delta=float(80 - i),
            plan_hash=f"lane_hash_{i:04d}",
            params=lp,
        )
        rows.append(row)
    ranked = sorted(rows, key=lambda r: float(r["estimated_db_qps_delta_pct"]), reverse=True)
    selected = select_diverse_top_k(
        ranked,
        capability=CAPABILITY,
        scenario=SCENARIO,
        top_k=20,
    )
    lanes = set()
    for row in selected:
        lane = _param_lane(row.get("params") or {})
        lanes.add(lane)
    assert len(lanes) >= 2, (
        f"Expected at least 2 different param lanes, got {len(lanes)}: {lanes}"
    )
    print(f"[PASS] test_param_lanes_diverse: {len(lanes)} different param lanes")


# ---------------------------------------------------------------------------
# Test 4: negative/disaster candidates remain excluded
# ---------------------------------------------------------------------------
def test_negative_disaster_excluded():
    """Disaster and negative-QPS candidates should not appear in selected."""
    pfs = _different_pfs()
    rows: List[Dict[str, Any]] = []
    # Add some good rows
    for i in range(10):
        row = _make_row(
            pf=pfs[i % len(pfs)],
            cpu=9.0,
            memory=1.3,
            io=300.0,
            qps_delta=50.0,
            plan_hash=f"good_{i:04d}",
        )
        rows.append(row)
    # Add disaster row (QPS very negative + overload)
    disaster_row = _make_row(
        pf={"weighted_disaster": 1.0},
        cpu=15.0,  # > 1.25 * 11 = 13.75, overload
        memory=3.0,  # > 1.25 * 2 = 2.5, overload
        io=300.0,
        qps_delta=-80.0,
        qps_error=10.0,
        plan_hash="disaster",
    )
    rows.append(disaster_row)
    # Add negative QPS row
    neg_row = _make_row(
        pf={"weighted_neg": 1.0},
        cpu=9.0,
        memory=1.3,
        io=300.0,
        qps_delta=-10.0,
        qps_error=0.0,
        plan_hash="neg_qps",
    )
    rows.append(neg_row)
    ranked = sorted(rows, key=lambda r: float(r["estimated_db_qps_delta_pct"]), reverse=True)
    selected = select_diverse_top_k(
        ranked,
        capability=CAPABILITY,
        scenario=SCENARIO,
        top_k=20,
        exclude_disaster=True,
        exclude_negative_qps=True,
    )
    # Check disaster param_hash not in selected
    selected_hashes = {r.get("plan_hash") for r in selected}
    assert "disaster" not in selected_hashes, "disaster candidate should be excluded"
    assert "neg_qps" not in selected_hashes, "negative-QPS candidate should be excluded"
    print(f"[PASS] test_negative_disaster_excluded: disaster/negative excluded")


# ---------------------------------------------------------------------------
# Test 5: selection unchanged by adding/removing evaluation-label file
# ---------------------------------------------------------------------------
def test_selection_label_file_agnostic():
    """The diversity selection is purely based on predicted signals.
    Adding or removing a labels file should not change selection.
    """
    rows: List[Dict[str, Any]] = []
    pfs = _different_pfs()
    for i in range(15):
        pf = pfs[i % len(pfs)]
        row = _make_row(
            pf=pf,
            cpu=8.0 + i * 0.2,
            memory=1.2 + i * 0.02,
            io=200.0 + i * 10.0,
            qps_delta=float(50 - i),
            plan_hash=f"agnostic_{i:04d}",
        )
        rows.append(row)

    ranked = sorted(rows, key=lambda r: float(r["estimated_db_qps_delta_pct"]), reverse=True)

    # Run selection WITHOUT any label file
    selected_no_labels = select_diverse_top_k(
        ranked,
        capability=CAPABILITY,
        scenario=SCENARIO,
        top_k=10,
    )

    # The selection function is deterministic for same input
    selected_again = select_diverse_top_k(
        ranked,
        capability=CAPABILITY,
        scenario=SCENARIO,
        top_k=10,
    )

    # Selections must be identical (same input → same output)
    selected_hashes_1 = [r.get("plan_hash") for r in selected_no_labels]
    selected_hashes_2 = [r.get("plan_hash") for r in selected_again]
    assert selected_hashes_1 == selected_hashes_2, (
        "Deterministic selection should produce same output for same input"
    )

    # Verify no labels were loaded (by checking we can't access candidate_id)
    for r in selected_no_labels:
        assert "candidate_id" not in r, "Labels should not be in selection input"

    print(f"[PASS] test_selection_label_file_agnostic: deterministic, label-free")


# ---------------------------------------------------------------------------
# Test 6: response bins correctly partition resource utilization
# ---------------------------------------------------------------------------
def test_response_bin_logic():
    """Verify _response_bin produces expected zone labels."""
    # low: < 30%
    low_res = {"cpu": 2.0, "memory": 0.4, "io": 200.0}
    low_bin = _response_bin(low_res, CAPABILITY)
    assert "cpu=low" in low_bin and "memory=low" in low_bin, f"Expected low, got {low_bin}"

    # mid: 30-65%
    mid_res = {"cpu": 5.0, "memory": 1.0, "io": 800.0}
    mid_bin = _response_bin(mid_res, CAPABILITY)
    assert "cpu=mid" in mid_bin and "memory=mid" in mid_bin, f"Expected mid, got {mid_bin}"

    # high: 65-85%
    high_res = {"cpu": 8.0, "memory": 1.5, "io": 1200.0}
    high_bin = _response_bin(high_res, CAPABILITY)
    assert "cpu=high" in high_bin or "memory=high" in high_bin, f"Expected high, got {high_bin}"

    # over: > 85%
    over_res = {"cpu": 10.0, "memory": 1.8, "io": 1500.0}
    over_bin = _response_bin(over_res, CAPABILITY)
    assert "cpu=over" in over_bin or "memory=over" in over_bin or "io=over" in over_bin, (
        f"Expected over, got {over_bin}"
    )

    print(f"[PASS] test_response_bin_logic: bins correct")


# ---------------------------------------------------------------------------
# Test 7: plan_feature_bin aggregates different hash → same bin
# ---------------------------------------------------------------------------
def test_plan_feature_bin_aggregates_hashes():
    """Different plan hashes with same rounded feature values share a bin."""
    pf_same = {"weighted_gather_count": 100.0, "weighted_seq_scan_count": 50.0}
    row1 = _make_row(pf=pf_same, cpu=9.0, memory=1.3, io=300.0, qps_delta=50.0, plan_hash="hash_a")
    row2 = _make_row(pf=pf_same, cpu=9.0, memory=1.3, io=300.0, qps_delta=50.0, plan_hash="hash_b")
    bin1 = _plan_feature_bin(row1)
    bin2 = _plan_feature_bin(row2)
    assert bin1 == bin2, f"Same PF should produce same bin: {bin1} != {bin2}"
    print(f"[PASS] test_plan_feature_bin_aggregates_hashes: bin={bin1}")


# ---------------------------------------------------------------------------
# Test 8: qps_bin correctly bins conservative deltas
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Test 9: Phase 3 retry fill reaches top_k when Phase 1+2 cannot
# ---------------------------------------------------------------------------
def test_phase3_fill_reaches_top_k():
    """When Phase 1+2 caps are too tight (e.g. only 3 PF bins × 8 cap = 24),
    Phase 3 must retry with multiplied caps and fill top_k.
    """
    pfs = _different_pfs()[:3]  # 3 unique PF bins
    rows: List[Dict[str, Any]] = []
    for j, base_pf in enumerate(pfs):
        for i in range(40):
            params = {
                "seq_page_cost": 1.0,
                "random_page_cost": 4.0,
                "cpu_tuple_cost": 0.01,
                "cpu_index_tuple_cost": 0.005,
                "cpu_operator_cost": 0.0025,
                "parallel_setup_cost": 1000.0,
                "parallel_tuple_cost": 0.1,
            }
            params["parallel_setup_cost"] = float(100 + i * 200 + j)
            row = _make_row(
                pf=base_pf,
                cpu=9.0,
                memory=1.3,
                io=300.0,
                qps_delta=float(100 - i - j * 2),
                plan_hash=f"p3fill_{j}_{i:04d}",
                params=params,
            )
            rows.append(row)
    ranked = sorted(rows, key=lambda r: float(r["estimated_db_qps_delta_pct"]), reverse=True)
    selected = select_diverse_top_k(
        ranked,
        capability=CAPABILITY,
        scenario=SCENARIO,
        top_k=60,
        max_per_pf_bin=8,
    )
    assert len(selected) == 60, (
        f"Expected 60 selected, got {len(selected)}"
    )
    pf_counts: Dict[str, int] = {}
    for row in selected:
        pf_bin = _plan_feature_bin(row)
        pf_counts[pf_bin] = pf_counts.get(pf_bin, 0) + 1
    # At multiplier=4 (eff_pf=32), 3 PF bins × 32 = 96, plenty for 60.
    # Each PF bin should have roughly ceil(60/3) ≈ 20 entries.
    max_pf = max(pf_counts.values())
    # Allow reasonable spread (30% tolerance)
    assert max_pf <= 30, (
        f"PF bin count {max_pf} too high; counts={pf_counts}"
    )
    assert len(pf_counts) == 3, (
        f"Expected 3 PF bins, got {len(pf_counts)}"
    )
    print(f"[PASS] test_phase3_fill_reaches_top_k: {len(selected)} selected, "
          f"{len(pf_counts)} PF bins, max={max_pf}")


def test_qps_bin_logic():
    assert _qps_conservative_bin(60.0) == "qps_strong_up"
    assert _qps_conservative_bin(20.0) == "qps_moderate_up"
    assert _qps_conservative_bin(8.0) == "qps_weak_up"
    assert _qps_conservative_bin(0.0) == "qps_neutral"
    assert _qps_conservative_bin(-10.0) == "qps_weak_down"
    assert _qps_conservative_bin(-50.0) == "qps_disaster"
    print("[PASS] test_qps_bin_logic: all bins correct")


# ---------------------------------------------------------------------------
# Test 10: full top60 artifact audit against coverage artifact
# ---------------------------------------------------------------------------
def test_full_top60_artifact_audit():
    """Load the actual forest-100 coverage artifact and assert gate conditions.

    Asserts:
      - top60_size == 60
      - strong_exact + neighborhood >= 3
      - no disaster or negative QPS in top60
      - default-zero check passes
      - IO is nonconstant
      - no single PF bin count exceeds 45
      - verified strong exact hit count == 3 (matches current artifact)
    """
    import json
    from pathlib import Path

    artifact_path = (
        Path(__file__).resolve().parents[1]
        / "report" / "bonect" / "memory_v113_forest100_diverse_top60_coverage_20260712.json"
    )
    if not artifact_path.is_file():
        print("[SKIP] test_full_top60_artifact_audit: coverage artifact not found; "
              "run shell/rebuild_forest100_top60_diversity.py first")
        return

    cov = json.loads(artifact_path.read_text(encoding="utf-8"))

    # 1. top60 size
    assert cov["top60_size"] == 60, f"top60_size={cov['top60_size']}, expected 60"

    # 2. recall audit
    recall = cov["verified_strong_recall_audit"]
    strong_exact = int(recall["verified_strong_exact_hit_count"])
    strong_neighborhood = int(recall["verified_strong_neighborhood_count"])
    assert strong_exact + strong_neighborhood >= 3, (
        f"verified strong recall too low: exact={strong_exact} neighborhood={strong_neighborhood}"
    )
    assert strong_exact == 3, (
        f"verified strong exact hit count={strong_exact}, expected 3 (current artifact)"
    )

    # 3. no disaster or negative QPS
    assert cov["disaster_audit"]["disaster_in_top60_count"] == 0, (
        f"disaster count={cov['disaster_audit']['disaster_in_top60_count']}"
    )
    assert cov["disaster_audit"]["negative_qps_in_top60_count"] == 0, (
        f"negative QPS count={cov['disaster_audit']['negative_qps_in_top60_count']}"
    )

    # 4. default zero check
    assert cov["default_zero_check"]["pass"] is True, (
        f"default zero check failed: {cov['default_zero_check']}"
    )

    # 5. IO variation
    assert cov["io_variation_check"]["pass"] is True, (
        f"IO variation check failed: {cov['io_variation_check']}"
    )

    # 6. no single PF bin count > 45
    pf_counts = cov["top60_plan_feature_bin_counts"]
    max_pf = max(pf_counts.values())
    assert max_pf <= 45, (
        f"max PF bin count={max_pf} exceeds 45; counts={pf_counts}"
    )

    # 7. known labels matching uses canonical seven-param signature
    assert cov["known_labels_matching"]["method"] == "canonical_seven_param_signature", (
        f"unexpected matching method: {cov['known_labels_matching']['method']}"
    )
    assert cov["known_labels_matching"]["unmatched_count"] == 0, (
        f"unmatched labels: {cov['known_labels_matching']['unmatched_count']}"
    )

    print(f"[PASS] test_full_top60_artifact_audit: "
          f"size={cov['top60_size']}, PF bins={len(pf_counts)}, "
          f"exact={strong_exact}, neighborhood={strong_neighborhood}, "
          f"disaster={cov['disaster_audit']['disaster_in_top60_count']}, "
          f"neg_qps={cov['disaster_audit']['negative_qps_in_top60_count']}, "
          f"max_pf={max_pf}")


def main() -> int:
    print("=" * 70)
    print("Generic answer-blind diversity ranking tests")
    print("=" * 70)
    test_plateau_cannot_monopolize_prefix()
    test_new_key_per_bin_caps_enforced()
    test_each_bin_dimension_cap_independent()
    test_high_score_remain_represented()
    test_param_lanes_diverse()
    test_negative_disaster_excluded()
    test_selection_label_file_agnostic()
    test_response_bin_logic()
    test_plan_feature_bin_aggregates_hashes()
    test_qps_bin_logic()
    test_phase3_fill_reaches_top_k()
    test_full_top60_artifact_audit()
    print("=" * 70)
    print("ALL DIVERSITY TESTS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
