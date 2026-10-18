from __future__ import annotations

import json
from pathlib import Path

from scripts import mysql_algorithm_b_pg_outer_policy_v31 as policy
from scripts import run_mysql_four_scene_algorithm_b_dynamic_support_resume_core_v27 as bcore
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v2 as ccore


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v32"


def load(name: str):
    return json.loads((FREEZE / name).read_text(encoding="utf-8"))


def test_unified_flow_freezes_runtime_truth_for_a_b_c():
    contract = load("unified_algorithm_flow_contract.json")
    variants = contract["algorithm_variants"]
    assert variants["A"]["mysql_runtime_status"] == "NOT_IMPLEMENTED_NOT_LAUNCHABLE"
    assert variants["B"]["outer_flow"] == variants["C"]["outer_flow"]
    assert variants["B"]["mysql_runtime_status"] == "FROZEN_LAUNCHABLE"
    assert variants["C"]["mysql_runtime_status"] == "FROZEN_LAUNCHABLE"
    assert contract["flow_equivalence_boundary"]["explicit_experiment_override"][
        "post_search_stability_confirmation"
    ] is False


def test_b_and_c_freeze_the_same_outer_contract():
    b = load("algorithm_b_fresh_contract.json")["pg_search_outer"]
    c = load("algorithm_c_optimization_contract.json")["pg_search_outer"]
    for key in (
        "real_top_k_limit", "top_k_is_upper_bound", "partial_top_k_allowed",
        "candidate_batches", "required_acceptance_tier", "qps_reference_mode",
        "qps_threshold_mode", "minimum_db_qps_gain_ratio",
        "minimum_target_drop_ratio", "accepted_range_shrink",
        "cached_valid_replay_redecision_before_new_physical_work",
        "fallback_plan_change_expansion", "full_range_plan_basin_escape",
        "technical_only_round_semantic_state_unchanged", "maximum_search_rounds",
        "accepted_target", "post_search_stability_confirmation",
        "post_target_physical_replays",
    ):
        assert b[key] == c[key], key
    assert b["all_physical_waves_complete_before_semantic_batch_selection"] is True
    assert c["all_physical_waves_complete_before_semantic_batch_selection"] is True


def test_runtime_constants_match_frozen_pg_search_outer():
    outer = load("algorithm_b_fresh_contract.json")["pg_search_outer"]
    assert policy.ACCEPTED_TARGET == outer["accepted_target"] == bcore.TARGET_ACCEPTED_SHRINKS
    assert policy.MAX_SEARCH_ROUNDS == outer["maximum_search_rounds"] == bcore.MAXIMUM_OUTER_ATTEMPTS
    assert policy.REPLAY_BATCH_SIZES == (2, 2, 1)
    assert ccore.MAXIMUM_OUTER_ATTEMPTS == outer["maximum_search_rounds"]
    assert ccore.TARGET_ACCEPTED_ROUNDS == outer["accepted_target"]


def test_coordination_freeze_uses_the_same_physical_wave_shape():
    contract = load("coordination_contract.json")
    assert contract["candidate_batches"] == [[1, 2], [3, 4], [5]]
