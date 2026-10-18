from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from scripts import mysql_algorithm_b_all_scene_incumbent_plan_reranker_v6 as ranker
from scripts import run_mysql_four_scene_algorithm_b_fresh_plan_stability_v26 as subject


ROOT = Path(__file__).resolve().parents[1]
V25 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v25_formal_fresh_common_plan_20260819_attempt024"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v26_contract_is_sealed_and_all_sources_match():
    contract = subject.validate_contract()
    assert contract["release_version"] == "v26"
    assert contract["formal_scope"] == "mysql_only"
    assert contract["cross_engine_comparison_prohibited"] is True
    assert contract["candidate_batches"] == [[1, 2], [3, 4], [5]]
    assert contract["fresh_campaign"]["predecessor_v25_outer_credit"] is False
    for binding in contract["source_bindings"].values():
        path = ROOT / binding["path"]
        assert path.is_file() and not path.is_symlink()
        assert _sha(path) == binding["sha256"]


def test_v25_failure_is_four_bad_tpch_points_not_a_global_runtime_failure():
    failed = []
    for batch in (1, 3):
        physical = _load(
            V25 / f"outer_001/top5_batch_{batch}/physical_joint_wave_batch_result.json"
        )
        for row in physical["measurements"].values():
            if row["scene"] == "tpch_sf4_memory":
                failed.append((row["rank"], row["hard_gate_passed"], row["coverage_complete"]))
            else:
                assert row["hard_gate_passed"] is True
                assert row["coverage_complete"] is True
    rank5 = _load(V25 / "outer_001/top5_batch_5/batch_result.json")
    tpch5 = rank5["measurements"]["tpch_w1"]
    assert sorted(failed) == [
        (1, False, False), (2, False, False),
        (3, False, False), (4, False, False),
    ]
    assert tpch5["rank"] == 5
    assert tpch5["hard_gate_passed"] is True
    assert tpch5["coverage_complete"] is True


def test_v26_offline_rerank_moves_v25_bad_tpch_family_out_of_top5():
    rankings = _load(V25 / "outer_001/predicted_rankings.json")
    baseline = _load(V25 / "formal_independent_baseline/batch_result.json")
    design = _load(V25 / "calibration_design.json")
    state = _load(V25 / "run_state.json")
    rows = copy.deepcopy(
        rankings["scene_audit"]["tpch_sf4_memory"]["plan_rerank_shortlist"]
    )
    seeds = [row["params"] for row in design["scene_designs"]["tpch_sf4_memory"]]
    ranges = state["ranges_by_scene"]["tpch_sf4_memory"]
    active = list(ranges)
    for row in rows:
        distance = ranker.calibration_support_distance(
            params=row["optimized_params"], calibration_seed_points=seeds,
            ranges=ranges, active=active,
        )
        row["calibration_support_distance"] = distance
        row["calibration_support_weight"] = ranker.calibration_support_weight(distance)
        row["candidate_hard_pruned_by_calibration_support"] = False
    ranker._apply_common_score(
        rows, scene="tpch_sf4_memory",
        incumbent_resources=baseline["measurements"]["tpch_w1"]["resource_means"],
    )
    selected = ranker.select_global_and_local_top5(rows)
    selected_keys = {
        ranker.base.alg.optimizer_params_key(row["optimized_params"])
        for row in selected
    }
    old_bad_keys = {
        ranker.base.alg.optimizer_params_key(row["optimized_params"])
        for row in rankings["selected_top_k"]["tpch_sf4_memory"][:4]
    }
    assert selected_keys.isdisjoint(old_bad_keys)
    assert all(row["candidate_hard_pruned_by_plan_stability"] is False for row in selected)
    assert all(row["candidate_hard_pruned_by_calibration_support"] is False for row in selected)
    assert all(row["qps_used_for_incumbent_relative_plan_score"] is False for row in selected)
