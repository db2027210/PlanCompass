from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts import mysql_algorithm_a_dynamic_slots_v1 as slots


ROOT = Path(__file__).resolve().parents[1]
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)


def load_freeze():
    return json.loads(FREEZE.read_text(encoding="utf-8"))


def state(
    scene: str, *, complete: bool, accepted: int = 3,
    clean_nonconvergence: bool = False,
):
    status = (
        "COMPLETE" if complete else
        "NONCONVERGENCE_CLEAN" if clean_nonconvergence else
        "RUNNING"
    )
    stage = (
        "COMPLETE" if complete else
        "ALGORITHM_A_NONCONVERGENCE_CLEAN" if clean_nonconvergence else
        "ROUND_004_WAVE_001_MEASURING"
    )
    return {
        "algorithm": "A",
        "async_scene": scene,
        "formal_scope": "mysql_only",
        "method": "exhaustive_binary_factorial_algorithm_a_v2",
        "candidate_search_contract": (
            "lane_local_complete_lr_cartesian_physical_attempt_v2"
        ),
        "complete_round_requires_every_parameter_vector_attempted": True,
        "status": status,
        "stage": stage,
        "converged": complete,
        "accepted": 10 if complete else accepted,
        "accepted_target": 10,
        "normalized_width": 1 / 1024 if complete else 0.5**accepted,
        "completion_contract": {
            "postgresql_algorithm_a_flow_matched": complete,
        },
        "rejected_rounds": 1 if clean_nonconvergence else 0,
        "round_checkpoint": None if clean_nonconvergence else {},
        "ranges": {"x": {"min": 1, "max": 2}},
        "incumbent_params": {"x": 1.5},
        "incumbent_validation": {"db_qps": 2},
        "accepted_history": [{"accepted": index} for index in range(1, accepted + 1)],
        "canonical_baseline": {"db_qps": 1},
    }


def test_released_slots_map_two_job_to_cpu_and_two_tpch_to_io():
    freeze = load_freeze()
    additions = slots.build_released_xp_workers(freeze)
    by_id = {row["worker_id"]: row for row in additions}
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    assert set(by_id) == {"xp_cpu_w3", "xp_cpu_w4", "xp_io_w3", "xp_io_w4"}
    assert by_id["xp_cpu_w3"]["database_cpuset"] == frozen["job_w1"]["database_cpuset"]
    assert by_id["xp_cpu_w4"]["client_cpuset"] == frozen["job_w2"]["client_cpuset"]
    assert by_id["xp_io_w3"]["database_cpuset"] == frozen["tpch_w1"]["database_cpuset"]
    assert by_id["xp_io_w4"]["client_cpuset"] == frozen["tpch_w2"]["client_cpuset"]
    for worker_id, prototype_id in (
        ("xp_cpu_w3", "xp_cpu_w1"), ("xp_cpu_w4", "xp_cpu_w1"),
        ("xp_io_w3", "xp_io_w1"), ("xp_io_w4", "xp_io_w1"),
    ):
        for field in slots.RESOURCE_FIELDS:
            assert by_id[worker_id][field] == frozen[prototype_id][field]


def test_transition_requires_both_memory_scenes_complete():
    freeze = load_freeze()
    memory = {
        "job_memory": state("job_memory", complete=True),
        "tpch_sf4_memory": state("tpch_sf4_memory", complete=False),
    }
    xp = {scene: state(scene, complete=False) for scene in slots.XP_SCENES}
    with pytest.raises(ValueError, match="both memory scenes"):
        slots.build_transition_document(
            source_freeze_path=FREEZE,
            source_freeze_sha256="a" * 64,
            source_freeze=freeze,
            memory_states=memory,
            xp_states=xp,
        )


def test_clean_nonconvergence_does_not_release_memory_scene_slots():
    freeze = load_freeze()
    memory = {
        "job_memory": state("job_memory", complete=True),
        "tpch_sf4_memory": state(
            "tpch_sf4_memory", complete=False, accepted=1,
            clean_nonconvergence=True,
        ),
    }
    xp = {scene: state(scene, complete=False) for scene in slots.XP_SCENES}
    with pytest.raises(ValueError, match="both memory scenes"):
        slots.build_transition_document(
            source_freeze_path=FREEZE,
            source_freeze_sha256="a" * 64,
            source_freeze=freeze,
            memory_states=memory,
            xp_states=xp,
        )


def test_transition_preserves_xp_search_state_hash_and_requests_four_workers():
    freeze = load_freeze()
    memory = {scene: state(scene, complete=True) for scene in slots.MEMORY_SCENES}
    xp = {scene: state(scene, complete=False, accepted=2) for scene in slots.XP_SCENES}
    document = slots.build_transition_document(
        source_freeze_path=FREEZE,
        source_freeze_sha256="a" * 64,
        source_freeze=freeze,
        memory_states=memory,
        xp_states=xp,
    )
    assert document["parallelism"] == {
        "initial_per_scene": 2,
        "hybench_xp_cpu_after_transition": 4,
        "hybench_xp_io_after_transition": 4,
        "four_scene_wave_barrier": False,
    }
    assert document["boundary"]["mixed_worker_cohort_round_prohibited"] is True
    for scene in slots.XP_SCENES:
        assert document["xp_states"][scene]["transition_required"] is True
        assert document["xp_states"][scene]["search_state_sha256"] == slots.canonical_sha256(
            slots.search_state_payload(xp[scene])
        )


def test_position_overlap_fails_closed():
    freeze = load_freeze()
    additions = slots.build_released_xp_workers(freeze)
    additions = copy.deepcopy(additions)
    additions[1]["database_cpuset"] = additions[0]["database_cpuset"]
    with pytest.raises(ValueError, match="database CPU overlap"):
        slots.validate_released_xp_workers(freeze, additions)
