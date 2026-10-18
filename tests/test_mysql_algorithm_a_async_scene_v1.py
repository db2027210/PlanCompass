from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import run_mysql_algorithm_a_async_scene_v1 as algorithm_a


@pytest.mark.parametrize(
    "scene,active_count,candidate_count",
    [
        ("job_memory", 4, 16),
        ("tpch_sf4_memory", 4, 16),
        ("hybench_xp_cpu", 7, 128),
        ("hybench_xp_io", 7, 128),
    ],
)
def test_frozen_scene_cardinality(
    scene: str, active_count: int, candidate_count: int,
) -> None:
    active, ranges = algorithm_a.scene_parameter_space(scene)
    contract = algorithm_a.validate_a_contract(scene, active)
    assert len(active) == active_count
    assert set(active) == set(ranges)
    assert contract["scenes"][scene]["candidates_per_round"] == candidate_count
    assert contract["scenes"][scene]["physical_waves_per_round"] == candidate_count // 2


def test_same_worker_normalization_maps_baseline_to_canonical() -> None:
    canonical = {
        "db_qps": 20.0,
        "resource_means": {"cpu": 0.4, "memory": 0.6, "io": 0.2},
    }
    worker = {
        "db_qps": 10.0,
        "resource_means": {"cpu": 0.2, "memory": 0.3, "io": 0.1},
    }
    row = {
        "worker_id": "w1",
        "db_qps": 12.0,
        "p95_ms": 1.0,
        "p99_ms": 2.0,
        "resource_means": {"cpu": 0.1, "memory": 0.15, "io": 0.05},
        "hard_gate_passed": True,
        "coverage_complete": True,
    }
    normalized = algorithm_a.normalize_to_worker_baseline(row, worker, canonical)
    assert normalized["db_qps"] == pytest.approx(24.0)
    assert normalized["resource_means"] == pytest.approx({
        "cpu": 0.2, "memory": 0.3, "io": 0.1,
    })


def test_fresh_state_has_pg_a_terminal_contract() -> None:
    active, ranges = algorithm_a.scene_parameter_space("job_memory")
    state = algorithm_a.fresh_state(
        run_id="mysql_a_test_job",
        scene="job_memory",
        active=active,
        ranges=ranges,
        authority={},
    )
    assert state["accepted"] == 0
    assert state["accepted_target"] == 10
    assert state["maximum_rejected_outer_attempts"] == 10
    assert state["candidate_count_per_round"] == 16
    assert state["complete_round_requires_every_parameter_vector_attempted"] is True
    assert state["prediction_used_for_selection"] is False
    assert state["four_scene_wave_barrier"] is False
    assert state["post_convergence_stability_required"] is False
    assert state["post_target_physical_replays"] == 0


def test_runtime_resource_freeze_is_bound_from_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    freeze = tmp_path / "resource_freeze.json"
    freeze.write_text(json.dumps({
        "schema_version": (
            "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18"
        ),
    }) + "\n", encoding="utf-8")
    digest = hashlib.sha256(freeze.read_bytes()).hexdigest()
    preflight = tmp_path / "runtime_preflight.json"
    preflight.write_text(json.dumps({
        "freeze_path": str(freeze),
        "freeze_sha256": digest,
    }) + "\n", encoding="utf-8")

    monkeypatch.setattr(algorithm_a.alg, "FREEZE", Path("old-freeze"))
    monkeypatch.setattr(algorithm_a.alg.r0mod, "FREEZE", Path("old-freeze"))
    monkeypatch.setattr(algorithm_a.alg.r0mod, "FREEZE_SCHEMA", "old-schema")

    document, bound, schema = algorithm_a.bind_runtime_resource_freeze(preflight)

    assert document["freeze_sha256"] == digest
    assert bound == freeze.resolve()
    assert schema.endswith("_v18")
    assert algorithm_a.alg.FREEZE == freeze.resolve()
    assert algorithm_a.alg.r0mod.FREEZE == freeze.resolve()
    assert algorithm_a.alg.r0mod.FREEZE_SCHEMA == schema


def test_runtime_resource_freeze_rejects_digest_mismatch(tmp_path: Path) -> None:
    freeze = tmp_path / "resource_freeze.json"
    freeze.write_text(json.dumps({
        "schema_version": (
            "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18"
        ),
    }) + "\n", encoding="utf-8")
    preflight = tmp_path / "runtime_preflight.json"
    preflight.write_text(json.dumps({
        "freeze_path": str(freeze),
        "freeze_sha256": "0" * 64,
    }) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="freeze binding differs"):
        algorithm_a.bind_runtime_resource_freeze(preflight)
