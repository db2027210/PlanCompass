#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import bonect_full_grid_estimator_scan as scanner
from bonect_robust_ranking import robust_score_record
from bonect_full_grid_estimator_scan import (
    COST_PARAMS,
    apply_resource_correction,
    apply_paired_delta_calibration,
    apply_resource_model_dir,
    build_arg_parser,
    candidate_iter_from_values,
    extract_hybench_runtime,
    extract_plan_delta_features,
    load_baseline_calibration,
    load_workload_context,
    optimizer_compact_grid_values,
    parse_pg_endpoint,
    refresh_workload_stats,
)


def sample_config() -> dict:
    defaults = {
        "seq_page_cost": 1.0,
        "random_page_cost": 4.0,
        "cpu_tuple_cost": 0.01,
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "parallel_setup_cost": 1000.0,
        "parallel_tuple_cost": 0.1,
    }
    ranges = {
        name: {
            "min_value": 0.0001 if name != "parallel_setup_cost" else 0.0,
            "max_value": 100.0 if name != "parallel_setup_cost" else 10000.0,
            "discrete_count": 5,
        }
        for name in COST_PARAMS
    }
    return {"initial_params": defaults, "param_ranges": ranges}


def main() -> None:
    parsed = build_arg_parser().parse_args(
        [
            "--scenario",
            "memory",
            "--workload-sql-from-pg-statements",
            "--baseline-calibration-json",
            "baseline.json",
        ]
    )
    assert parsed.workload_sql_from_pg_statements is True
    assert parsed.workload_sql_json is None
    assert parsed.baseline_calibration_json == "baseline.json"
    config = sample_config()
    values = optimizer_compact_grid_values(config, 5)
    assert list(values) == COST_PARAMS
    assert all(len(values[name]) == 5 for name in COST_PARAMS)
    assert values["seq_page_cost"] == [0.0001, 1.0, 100.0, 25.000075, 75.000025]
    assert sum(1 for _ in candidate_iter_from_values(values)) == 5 ** 7

    base = {
        "host": "vm",
        "port": 25432,
        "user": "postgres",
        "dbname": "hybench_sf10x",
    }
    endpoint = parse_pg_endpoint("127.0.0.1:58432/hybench_sf10x", base)
    assert endpoint["host"] == "127.0.0.1"
    assert endpoint["port"] == 58432
    assert endpoint["dbname"] == "hybench_sf10x"
    assert endpoint["user"] == "postgres"

    with tempfile.TemporaryDirectory(prefix="bonect-model-dir-") as temp_dir:
        model_dir = Path(temp_dir)
        for resource_name in ("cpu", "memory", "io"):
            (model_dir / f"{resource_name}_resource_models.json").write_text("{}\n")
        model_config: dict = {}
        paths = apply_resource_model_dir(model_config, model_dir)
        assert set(paths) == {"cpu", "memory", "io"}
        plan_cfg = model_config["metrics"]["plan_estimator"]
        assert plan_cfg["model_retraining"]["enabled"] is False
        assert plan_cfg["cpu"]["learned_model"]["required"] is True

        context_path = model_dir / "workload_context.json"
        context_path.write_text(
            json.dumps(
                {
                    "workload_context": {
                        "elapsed_seconds": 355.0,
                        "concurrency": {"avg_estimated": 53.0},
                        "database_size_bytes": 1024,
                        "cache_hit_rate": 0.2,
                    }
                }
            ),
            encoding="utf-8",
        )
        context = load_workload_context(context_path)
        assert context["elapsed_seconds"] == 355.0

        calibration_path = model_dir / "baseline.json"
        calibration_path.write_text(
            json.dumps(
                {
                    "source": "unit_test",
                    "scales": {"cpu": 2.0, "io": 4.0, "memory": 3.0},
                    "exempt_resources": ["memory"],
                    "actual_resource": {"cpu": 8.0, "memory": 2.0, "io": 100.0},
                }
            ),
            encoding="utf-8",
        )
        calibration = load_baseline_calibration(calibration_path)
        corrected, detail = apply_resource_correction(
            {"cpu": 1.0, "memory": 2.0, "io": 3.0},
            calibration,
        )
        assert corrected == {"cpu": 2.0, "memory": 2.0, "io": 12.0}
        assert detail["applied"] is True
        assert detail["scales"] == {"cpu": 2.0, "io": 4.0}

        plan_features = extract_plan_delta_features(
            [
                {
                    "replay_count": 7,
                    "plan_root": {
                        "Node Type": "Gather",
                        "Workers Planned": 2,
                        "Plans": [{"Node Type": "Seq Scan", "Parallel Aware": True}],
                    },
                }
            ]
        )
        assert plan_features["weighted_gather_count"] == 7.0
        assert plan_features["weighted_gather_workers_planned"] == 14.0
        assert plan_features["weighted_parallel_aware_nodes"] == 7.0
        paired_calibration = {
            "actual_resource": {"cpu": 5.0, "memory": 2.0, "io": 100.0},
            "paired_delta_calibration": {
                "enabled": True,
                "source": "unit_test",
                "feature": "weighted_gather_workers_planned",
                "baseline_feature": 14.0,
                "teacher_feature": 0.0,
                "resource_teacher_delta": {"cpu": 2.0, "memory": -0.5, "io": -40.0},
                "db_qps_teacher_delta_pct": 20.0,
            },
        }
        paired_resource, paired_qps, paired_detail = apply_paired_delta_calibration(
            {"cpu": 1.0, "memory": 1.0, "io": 1.0},
            {"weighted_gather_workers_planned": 0.0},
            paired_calibration,
        )
        assert paired_resource == {"cpu": 7.0, "memory": 1.5, "io": 60.0}
        assert paired_qps == 20.0
        assert paired_detail["progress"] == 1.0
        assert paired_detail["teacher_coordinates_used"] is False

        props_path = model_dir / "memory blind.props"
        props_path.write_text(
            "xtpclient=4\nxapclient=1\nxpRunMins=5\napRunMins=10\n",
            encoding="utf-8",
        )
        runtime = extract_hybench_runtime(
            f"bash hybench -t runxp -c '{props_path}' -f statements.toml"
        )
        assert runtime["mode"] == "runxp"
        assert runtime["configured_concurrency"] == 5
        assert runtime["props"]["xpRunMins"] == 5

        original_psql_for_context = scanner.psql_query
        scanner.psql_query = lambda _pg, _sql: "0"
        try:
            built_context = scanner.build_workload_context(
                {"benchmark": {"workload_cmd": f"bash hybench -t runxp -c '{props_path}'"}},
                [{"total_time_ms": 300000.0}],
                {},
            )
        finally:
            scanner.psql_query = original_psql_for_context
        assert built_context["elapsed_seconds"] == 300.0
        assert built_context["concurrency"]["configured"] == 5

    original_time_column = scanner.pg_stat_statements_time_column
    original_psql_query = scanner.psql_query
    scanner.pg_stat_statements_time_column = lambda _pg: "total_exec_time"
    scanner.psql_query = lambda _pg, _sql: "-1\t4\t120.5\n2\t8\t300.0"
    try:
        refreshed = refresh_workload_stats(
            [
                {"queryid": "-1", "query": "select 1 from t", "replay_count": 1},
                {"queryid": "2", "query": "select 2 from t", "replay_count": 1},
            ],
            {},
        )
    finally:
        scanner.pg_stat_statements_time_column = original_time_column
        scanner.psql_query = original_psql_query
    assert [item["replay_count"] for item in refreshed] == [4.0, 8.0]
    assert [item["total_time_ms"] for item in refreshed] == [120.5, 300.0]

    robust_base = robust_score_record(
        {
            "score": 1.0,
            "estimated_resource": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
            "baseline_resource": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        },
        capability={"cpu": 10.0, "memory": 10.0, "io": 10.0},
        scenario="memory",
    )
    robust_qps = robust_score_record(
        {
            "score": 1.0,
            "estimated_resource": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
            "baseline_resource": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
            "estimated_db_qps_delta_pct": 20.0,
        },
        capability={"cpu": 10.0, "memory": 10.0, "io": 10.0},
        scenario="memory",
    )
    assert robust_qps["robust_score"] == robust_base["robust_score"] + 2.0
    assert robust_qps["db_qps_surrogate_applied"] is True

    memory_strong = robust_score_record(
        {
            "score": 4.3,
            "estimated_resource": {"cpu": 8.318, "memory": 1.140, "io": 191.51},
            "baseline_resource": {"cpu": 6.185, "memory": 1.623, "io": 587.16},
            "estimated_db_qps_delta_pct": 117.16,
        },
        capability={"cpu": 11.0, "memory": 2.0, "io": 1600.0},
        scenario="memory",
    )
    assert memory_strong["resource_direction_passed"] is True
    assert memory_strong["resource_direction"]["best_other_up_delta_util_pp"] > 0.0
    assert (
        memory_strong["resource_direction"]["components"]["non_target_down_excess_penalty"]
        <= 2.0
    )
    assert memory_strong["robust_score"] > robust_base["robust_score"]
    print("bonect full-grid parallel scan tests passed", flush=True)


if __name__ == "__main__":
    main()
