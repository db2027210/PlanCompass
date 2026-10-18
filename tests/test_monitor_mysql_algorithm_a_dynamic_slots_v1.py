from __future__ import annotations

from scripts import monitor_mysql_algorithm_a_dynamic_slots_v1 as monitor


def complete_state(scene: str):
    return {
        "algorithm": "A",
        "async_scene": scene,
        "formal_scope": "mysql_only",
        "status": "COMPLETE",
        "stage": "COMPLETE",
        "converged": True,
        "accepted": 10,
        "normalized_width": 1 / 128,
        "completion_contract": {
            "postgresql_algorithm_a_flow_matched": True,
        },
    }


def test_strict_complete_requires_full_pg_aligned_terminal():
    state = complete_state("job_memory")
    assert monitor.strict_complete(state, "job_memory")
    state["accepted"] = 6
    assert not monitor.strict_complete(state, "job_memory")


def test_strict_complete_rejects_wrong_scene_and_legacy_status():
    state = complete_state("hybench_xp_cpu")
    assert not monitor.strict_complete(state, "hybench_xp_io")
    state["status"] = "NONCONVERGENCE_CLEAN"
    assert not monitor.strict_complete(state, "hybench_xp_cpu")


def test_strict_terminal_accepts_pg_aligned_clean_nonconvergence():
    state = {
        "algorithm": "A",
        "async_scene": "tpch_sf4_memory",
        "formal_scope": "mysql_only",
        "method": "exhaustive_binary_factorial_algorithm_a_v2",
        "candidate_search_contract": (
            "lane_local_complete_lr_cartesian_physical_attempt_v2"
        ),
        "complete_round_requires_every_parameter_vector_attempted": True,
        "status": "NONCONVERGENCE_CLEAN",
        "stage": "ALGORITHM_A_NONCONVERGENCE_CLEAN",
        "converged": False,
        "accepted": 1,
        "accepted_target": 10,
        "rejected_rounds": 1,
        "round_checkpoint": None,
    }
    assert monitor.strict_terminal(state, "tpch_sf4_memory")
    state["round_checkpoint"] = {"attempted_ranks": [1]}
    assert not monitor.strict_terminal(state, "tpch_sf4_memory")
