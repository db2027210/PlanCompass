from __future__ import annotations

import copy

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_parallel_221_resume_v9 as subject,
)


def _workers():
    freeze = subject.alg.load(subject.predecessor.FREEZE)
    return {row["worker_id"]: row for row in freeze["workers"]}


def _rankings():
    return {
        scene: [
            {
                "rank": rank,
                "optimized_params": {
                    name: float(value) + rank * 1e-8
                    for name, value in subject.alg.DEFAULTS.items()
                },
                "resource_model": {
                    "candidate_plan_sha256": f"{scene}-{rank}",
                },
            }
            for rank in range(1, 6)
        ]
        for scene in subject.SCENES
    }


def _measurement(worker_id, scene, rank):
    return {
        "worker_id": worker_id,
        "scene": scene,
        "rank": rank,
        "hard_gate_passed": True,
        "coverage_complete": True,
        "resource_means": {"cpu": 0.5, "memory": 0.5, "io": 0.5},
        "db_qps": 1.0,
        "p95_ms": 1.0,
        "p99_ms": 1.0,
    }


def test_outer1_replay_boundary_is_complete_and_uncommitted():
    batches = subject.validate_outer1_replay_source()
    assert sorted(batches) == [1, 2, 3, 4, 5]
    assert batches[2]["document"]["status"] == "FAILED_CLOSED"
    assert all(
        batches[rank]["document"]["status"] == "PASS"
        for rank in (1, 3, 4, 5)
    )


def test_dual_assignments_put_first_rank_on_w1_and_second_on_w2():
    workers = _workers()
    rankings = _rankings()
    params = {scene: copy.deepcopy(subject.alg.DEFAULTS) for scene in subject.SCENES}
    assignments, companions, slot_ranks = subject._dual_assignments(
        rankings=rankings, params_by_scene=params, workers=workers,
        ranks=(1, 2),
    )
    assert not companions
    assert set(assignments) == set(workers)
    for scene in subject.SCENES:
        scene_workers = sorted(
            worker_id for worker_id, row in workers.items()
            if row["scene"] == scene
        )
        assert assignments[scene_workers[0]]["rank"] == 1
        assert assignments[scene_workers[1]]["rank"] == 2
        assert slot_ranks[scene_workers[0]] == 1
        assert slot_ranks[scene_workers[1]] == 2


def test_lane_failure_isolated_and_unknown_failure_fails_both(tmp_path):
    workers = _workers()
    slot_ranks = {
        worker_id: 1 if worker_id.endswith("w1") else 2
        for worker_id in workers
    }
    measurements = {
        worker_id: _measurement(worker_id, row["scene"], slot_ranks[worker_id])
        for worker_id, row in workers.items()
    }
    joint = {
        "status": "FAILED_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "elapsed_seconds": 900.1,
        "measurements": measurements,
        "failures": [{"task": "pair_2", "error": "timed out after 900 seconds"}],
    }
    lane_path = tmp_path / "lane" / "batch_result.json"
    subject.alg.atomic(lane_path, joint)
    lane_docs = subject._split_physical_wave(
        output=lane_path.parent, measured=measurements, ranks=(1, 2),
        active_scenes=set(subject.SCENES), slot_ranks=slot_ranks,
    )
    assert lane_docs[1]["status"] == "PASS"
    assert lane_docs[1]["failures"] == []
    assert lane_docs[2]["status"] == "FAILED_CLOSED"
    assert lane_docs[2]["failures"] == joint["failures"]

    unknown = copy.deepcopy(joint)
    unknown["failures"] = [{"task": "unknown", "error": "unattributed failure"}]
    unknown_path = tmp_path / "unknown" / "batch_result.json"
    subject.alg.atomic(unknown_path, unknown)
    unknown_docs = subject._split_physical_wave(
        output=unknown_path.parent, measured=measurements, ranks=(1, 2),
        active_scenes=set(subject.SCENES), slot_ranks=slot_ranks,
    )
    assert unknown_docs[1]["status"] == "FAILED_CLOSED"
    assert unknown_docs[2]["status"] == "FAILED_CLOSED"


def test_score_bridge_is_resource_only():
    incumbent = {
        "db_qps": 10.0,
        "p95_ms": 1.0,
        "p99_ms": 2.0,
        "resource_means": {"cpu": 0.4, "memory": 0.8, "io": 0.3},
    }
    objective = subject.predecessor.build_objective("job_memory", incumbent)
    resource = subject.predecessor.measurement_from_payload(
        {"cpu": 0.5, "memory": 0.7, "io": 0.4},
        {"db_qps": 10.0, "p95_ms": 1.0, "p99_ms": 2.0},
    ).as_resource()
    score_without_raw = subject._score_with_resource_payload(objective, resource)
    score_low_qps = subject._score_with_resource_payload(
        objective, resource,
        {"db_qps": 1.0, "p95_ms": 1.0, "p99_ms": 2.0},
    )
    score_high_qps = subject._score_with_resource_payload(
        objective, resource,
        {"db_qps": 1000.0, "p95_ms": 1.0, "p99_ms": 2.0},
    )
    assert score_without_raw == score_low_qps == score_high_qps

