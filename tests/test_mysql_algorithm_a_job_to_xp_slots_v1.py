from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts import execute_mysql_algorithm_a_job_to_xp_scene_v1 as execute
from scripts import monitor_mysql_algorithm_a_job_to_xp_slots_v1 as monitor
from scripts import mysql_algorithm_a_job_to_xp_slots_v1 as slots
from scripts import run_mysql_algorithm_a_job_to_xp_resume_v1 as resume


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_dynamic_slots_20260821_v1"
    / "job_four_worker_resource_successor.json"
)


def load_source():
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def measurement(worker_id: str, qps: float):
    return {
        "worker_id": worker_id,
        "db_qps": qps,
        "p95_ms": 10,
        "p99_ms": 20,
        "resource_means": {"cpu": 0.5, "memory": 0.6, "io": 0.7},
        "hard_gate_passed": True,
        "coverage_complete": True,
    }


def test_four_job_slots_map_two_per_xp_scene_with_scene_envelopes():
    freeze = load_source()
    additions = slots.build_xp_workers(freeze)
    by_id = {row["worker_id"]: row for row in additions}
    source = {row["worker_id"]: row for row in freeze["workers"]}
    assert {
        worker_id: by_id[worker_id]["slot_origin"] for worker_id in by_id
    } == {
        "xp_cpu_w3": "job_w1", "xp_cpu_w4": "job_w2",
        "xp_io_w3": "job_w3", "xp_io_w4": "job_w4",
    }
    for worker_id, prototype in (
        ("xp_cpu_w3", "xp_cpu_w1"), ("xp_cpu_w4", "xp_cpu_w1"),
        ("xp_io_w3", "xp_io_w1"), ("xp_io_w4", "xp_io_w1"),
    ):
        for field in slots.RESOURCE_FIELDS:
            assert by_id[worker_id][field] == source[prototype][field]
        donor = source[by_id[worker_id]["slot_origin"]]
        for field in slots.POSITION_FIELDS:
            assert by_id[worker_id][field] == donor[field]


def test_successor_contains_exactly_four_workers_per_xp_scene():
    freeze = load_source()
    successor = slots.build_successor_freeze(
        freeze, slots.build_xp_workers(freeze), created_at="test",
        job_evidence={"job_memory": {"complete": True}},
    )
    for scene in slots.XP_SCENES:
        assert len([
            row for row in successor["workers"] if row["scene"] == scene
        ]) == 4
    assert len(successor["workers"]) == 8


def test_cpu_and_io_transitions_are_explicitly_independent():
    contract = monitor.validate_contract()
    assert contract["per_scene_independent_transition"] is True
    assert contract["xp_cpu_does_not_wait_for_xp_io"] is True
    assert contract["xp_io_does_not_wait_for_xp_cpu"] is True
    assert execute.validate_contract()["contract"] == slots.TRANSITION_CONTRACT


def test_job_completion_gate_requires_pg_flow_contract():
    state = {
        "algorithm": "A", "async_scene": "job_memory",
        "formal_scope": "mysql_only", "status": "COMPLETE",
        "stage": "COMPLETE", "converged": True, "accepted": 10,
        "normalized_width": 0.001, "round_checkpoint": None,
        "completion_contract": {"postgresql_algorithm_a_flow_matched": False},
    }
    assert monitor.job_complete(state) is False
    state["completion_contract"]["postgresql_algorithm_a_flow_matched"] = True
    assert monitor.job_complete(state) is True


def test_overlap_and_resource_drift_fail_closed():
    freeze = load_source()
    additions = slots.build_xp_workers(freeze)
    overlap = copy.deepcopy(additions)
    overlap[1]["database_cpuset"] = overlap[0]["database_cpuset"]
    with pytest.raises(ValueError, match="database CPU overlap"):
        slots.validate_xp_workers(freeze, overlap)
    drift = copy.deepcopy(additions)
    drift[0]["memory_bytes"] += 4096
    with pytest.raises(ValueError, match="XP envelope differs"):
        slots.validate_xp_workers(freeze, drift)


def test_resume_requires_two_complete_baseline_repetitions():
    first = {
        f"xp_cpu_w{i}": measurement(f"xp_cpu_w{i}", float(i))
        for i in range(1, 5)
    }
    second = copy.deepcopy(first)
    result = resume.average_worker_repetitions([first, second])
    assert set(result) == set(first)
    with pytest.raises(RuntimeError, match="two full repetitions"):
        resume.average_worker_repetitions([first, {"xp_cpu_w1": first["xp_cpu_w1"]}])
