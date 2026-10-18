from __future__ import annotations

from scripts import (
    run_mysql_four_scene_algorithm_b_independent_lane_failure_isolation_core_v22
    as core,
)


class RecordingModel:
    def __init__(self) -> None:
        self.rows: list[tuple[dict, dict, dict]] = []

    def add(self, params, resources, raw) -> None:
        self.rows.append((dict(params), dict(resources), dict(raw)))

    def snapshot(self):
        return list(self.rows)


def evaluation(*, rank: int, valid: bool = True) -> dict:
    return {
        "rank": rank,
        "hard_gate_passed": valid,
        "coverage_complete": valid,
        "params": {
            "io_block_read_cost": 1.25,
            "memory_block_read_cost": 0.4,
        },
        "resource_means": {"cpu": 0.4, "memory": 0.8, "io": 0.2},
        "db_qps": 12.5,
        "p95_ms": 100.0,
        "p99_ms": 120.0,
    }


def test_valid_formal_top5_resources_are_added_and_invalid_rows_are_skipped():
    model = RecordingModel()
    audit = core.update_resource_model_from_formal_evaluations(
        scene="job_memory",
        model=model,
        evaluations=[evaluation(rank=1), evaluation(rank=2, valid=False)],
        outer_attempt=10,
    )

    assert audit["added_count"] == 1
    assert audit["skipped_count"] == 1
    assert audit["model_row_count_after"] == 1
    assert model.rows[0][1] == {"cpu": 0.4, "memory": 0.8, "io": 0.2}
    assert model.rows[0][2]["db_qps"] == 12.5
    assert model.rows[0][2]["phase"] == "formal_top5_online_resource_correction"


def test_online_update_contract_keeps_qps_out_of_resource_inputs_and_score():
    model = RecordingModel()
    audit = core.update_resource_model_from_formal_evaluations(
        scene="hybench_xp_cpu",
        model=model,
        evaluations=[evaluation(rank=3)],
        outer_attempt=11,
    )

    _, resource_input, _ = model.rows[0]
    assert set(resource_input) == {"cpu", "memory", "io"}
    assert audit["qps_used_as_model_input_feature"] is False
    assert audit["qps_used_for_resource_target"] is False
    assert audit["qps_used_for_resource_prediction_gradient_or_score"] is False
    assert audit["parameter_distance_and_resource_targets_only"] is True
