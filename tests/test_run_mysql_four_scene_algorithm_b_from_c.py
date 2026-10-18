from __future__ import annotations

from scripts import run_mysql_four_scene_algorithm_b_from_c_v1 as runtime


def _release() -> dict:
    return {
        "scenes": {
            scene: {
                "active_parameters": ["io_block_read_cost"],
                "algorithm_c_final_incumbent_params": {
                    "io_block_read_cost": 1.004,
                    "row_evaluate_cost": 0.1,
                },
                "algorithm_b_initial_binary_intervals": {
                    "io_block_read_cost": {
                        "lower": 1.0,
                        "upper": 1.009,
                        "transform": "log",
                    },
                },
                "algorithm_c_final_real_measurement": {
                    "db_qps": 100.0,
                    "p95_ms": 10.0,
                    "p99_ms": 20.0,
                    "resource_means": {"cpu": 0.6, "memory": 0.7, "io": 0.5},
                },
            }
            for scene in runtime.SCENES
        }
    }


def test_seed_resets_b_progress_without_mutating_c_checkpoint() -> None:
    checkpoint = {
        "completed_outer_attempts": 17,
        "reconstructed_state": {
            "outer_attempt": 17,
            "accepted_rounds": {scene: 10 for scene in runtime.SCENES},
        },
    }

    seed = runtime.build_seed_checkpoint(checkpoint, _release())

    assert checkpoint["completed_outer_attempts"] == 17
    assert seed["completed_outer_attempts"] == 0
    reconstructed = seed["reconstructed_state"]
    assert reconstructed["accepted_rounds"] == {
        scene: 0 for scene in runtime.SCENES
    }
    assert reconstructed["acceptance_tier"] == {
        scene: "strict_three_level" for scene in runtime.SCENES
    }
    assert all(value is False for value in reconstructed["converged"].values())
    assert reconstructed["ranges_by_scene"]["job_memory"] == {
        "io_block_read_cost": {
            "lower": 1.0,
            "upper": 1.009,
            "transform": "log",
        }
    }


def test_algorithm_b_top5_is_not_hard_pruned() -> None:
    rows = [{"rank": rank} for rank in range(1, 6)]
    reordered, pruned, audit = runtime._passthrough_plan_history(
        rows, [{"negative": "history"}],
    )
    assert reordered == rows
    assert reordered is not rows
    assert pruned == []
    assert audit["candidate_hard_pruning"] is False
