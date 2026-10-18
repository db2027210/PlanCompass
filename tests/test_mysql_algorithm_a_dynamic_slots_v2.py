from __future__ import annotations

import json
from pathlib import Path

from scripts import mysql_algorithm_a_dynamic_slots_v2 as slots
from scripts import monitor_mysql_algorithm_a_dynamic_slots_v2 as monitor
from scripts import run_mysql_algorithm_a_async_scene_v2 as base
from scripts import run_mysql_algorithm_a_dynamic_resume_v3 as resume


ROOT = Path(__file__).resolve().parents[1]
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)


def state(scene: str, *, complete: bool) -> dict:
    accepted = 10 if complete else 2
    return {
        "algorithm": "A",
        "async_scene": scene,
        "formal_scope": "mysql_only",
        "method": "exhaustive_binary_factorial_algorithm_a_v2",
        "candidate_search_contract": (
            "lane_local_complete_lr_cartesian_physical_attempt_v2"
        ),
        "complete_round_requires_every_parameter_vector_attempted": True,
        "status": "COMPLETE" if complete else "RUNNING",
        "stage": "COMPLETE" if complete else "ROUND_003_WAVE_001_MEASURING",
        "converged": complete,
        "accepted": accepted,
        "accepted_target": 10,
        "rejected_rounds": 3,
        "consecutive_rejected_rounds": 1,
        "incumbent_reference_refresh_count": 1,
        "incumbent_reference_source": "last_accepted_round",
        "last_incumbent_reference_refresh": {"round_index": 2},
        "normalized_width": 0.5**accepted,
        "completion_contract": {
            "postgresql_algorithm_a_flow_matched": complete,
        },
        "round_checkpoint": None if complete else {},
        "ranges": {"x": {"min": 1, "max": 2}},
        "incumbent_params": {"x": 1.5},
        "incumbent_validation": {"db_qps": 2},
        "accepted_history": [{"accepted": index} for index in range(1, accepted + 1)],
        "canonical_baseline": {"db_qps": 1},
    }


def test_v4_slot_contract_maps_memory_positions_to_xp() -> None:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    additions = slots.build_released_xp_workers(freeze)
    assert slots.TRANSITION_CONTRACT.endswith("_v4")
    assert {row["worker_id"] for row in additions} == {
        "xp_cpu_w3", "xp_cpu_w4", "xp_io_w3", "xp_io_w4",
    }
    assert all("20260822" in row["container"] for row in additions)


def test_transition_hash_preserves_reference_refresh_state() -> None:
    value = state("hybench_xp_cpu", complete=False)
    payload = slots.search_state_payload(value)
    assert payload["consecutive_rejected_rounds"] == 1
    assert payload["incumbent_reference_refresh_count"] == 1
    assert payload["incumbent_reference_source"] == "last_accepted_round"
    assert payload["last_incumbent_reference_refresh"] == {"round_index": 2}


def test_v3_resume_uses_last_accepted_controller() -> None:
    assert resume.base is base
    assert resume.base.pg_outer.policy_contract()["qps_reference_mode"] == (
        "last_accepted"
    )
    assert resume.base.INCUMBENT_REFRESH_AFTER_CONSECUTIVE_REJECTIONS == 2


def test_v2_monitor_requires_converged_memory_scenes() -> None:
    assert monitor.strict_complete(state("job_memory", complete=True), "job_memory")
    assert not monitor.strict_complete(
        state("job_memory", complete=False), "job_memory"
    )
