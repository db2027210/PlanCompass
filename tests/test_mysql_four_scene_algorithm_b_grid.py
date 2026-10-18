from scripts import mysql_four_scene_algorithm_b_grid_v1 as grid


def release(active: list[str]) -> dict[str, object]:
    incumbent = {
        "io_block_read_cost": 1.0,
        "memory_block_read_cost": 0.25,
        "row_evaluate_cost": 0.1,
        "memory_temptable_row_cost": 0.1,
    }
    return {
        "active_parameters": active,
        "algorithm_c_final_incumbent_params": incumbent,
        "algorithm_b_initial_binary_intervals": {
            name: {
                "lower": incumbent[name] / 1.004,
                "upper": incumbent[name] * 1.004,
                "transform": "log",
            }
            for name in active
        },
        "algorithm_c_final_real_measurement": {
            "db_qps": 10.0,
            "p95_ms": 1.0,
            "p99_ms": 2.0,
            "resource_means": {"cpu": 0.4, "memory": 0.9, "io": 0.2},
        },
    }


def test_job_grid_is_fixed_top5_inside_c_intervals() -> None:
    active = [
        "io_block_read_cost",
        "memory_block_read_cost",
        "row_evaluate_cost",
        "memory_temptable_row_cost",
    ]
    document = grid.generate_scene_top5(
        "job_memory", release(active), round_index=1,
    )
    assert document["status"] == "PASS"
    assert document["grid_contract"] == {
        "grid_points": 15,
        "inner_passes": 20,
        "convergence_patience": 2,
        "combination_sizes": [2, 4],
        "combination_values_per_parameter": 3,
        "combination_candidate_cap": 0,
        "fixed_real_top_k": 5,
    }
    assert [row["rank"] for row in document["selected_top5"]] == [1, 2, 3, 4, 5]
    assert document["qps_read_by_estimator_or_score"] is False
    assert document["candidate_hard_pruning"] is False
    intervals = release(active)["algorithm_b_initial_binary_intervals"]
    for row in document["selected_top5"]:
        for name in active:
            assert intervals[name]["lower"] <= row["optimized_params"][name]
            assert row["optimized_params"][name] <= intervals[name]["upper"]


def test_interval_wider_than_c_proof_fails_closed() -> None:
    active = [
        "io_block_read_cost",
        "memory_block_read_cost",
        "row_evaluate_cost",
        "memory_temptable_row_cost",
    ]
    value = release(active)
    value["algorithm_b_initial_binary_intervals"][active[0]]["upper"] = 2.0
    try:
        grid.generate_scene_top5("job_memory", value, round_index=1)
    except RuntimeError as error:
        assert "C-derived interval differs" in str(error)
    else:
        raise AssertionError("wide interval was not rejected")
