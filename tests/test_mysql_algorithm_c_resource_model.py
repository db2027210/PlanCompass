from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mysql_algorithm_c_resource_model import (
    confidence_adjusted_score,
    enhance_resource_prediction,
)


STATS = {
    "t": {
        "table_rows": 1_000_000,
        "data_length": 100_000_000,
        "avg_row_length": 100,
    }
}
EXECUTOR = {
    "sort_buffer_size": 256 * 1024,
    "tmp_table_size": 16 * 1024 * 1024,
    "max_heap_table_size": 16 * 1024 * 1024,
    "join_buffer_size": 256 * 1024,
}


def table_plan(rows: int, *, filesort: bool = False) -> dict:
    return {
        "query_block": {
            "table": {
                "table_name": "t",
                "access_type": "RANGE",
                "rows_examined_per_scan": rows,
                "rows_produced_per_join": rows,
                "filtered": 100,
                "using_filesort": filesort,
                "cost_info": {"data_read_per_join": f"{rows * 100}B"},
            }
        }
    }


def test_cardinality_and_spill_context_change_soft_prediction_without_pruning() -> None:
    result = enhance_resource_prediction(
        incumbent_resources={"cpu": 0.8, "memory": 0.8, "io": 0.8},
        experience_prediction={"cpu": 0.79, "memory": 0.79, "io": 0.79},
        baseline_plans={"q": table_plan(1_000)},
        candidate_plans={"q": table_plan(1_000_000, filesort=True)},
        relation_stats=STATS,
        executor_parameters=EXECUTOR,
        concurrency=8,
    )
    assert result["raw_plan_work_ratios"]["cpu"] > 1
    assert result["raw_plan_work_ratios"]["io"] > 1
    assert result["candidate_work"]["memory_demand_bytes"] > 0
    assert result["predicted_resources"]["cpu"] > 0.79
    assert result["hard_prune_allowed"] is False
    assert result["operator_count_rule_role"] == "soft_prior_only"
    assert result["confidence"] < 0.55


def test_context_model_records_unavailable_runtime_features_explicitly() -> None:
    result = enhance_resource_prediction(
        incumbent_resources={"cpu": 0.8, "memory": 0.8, "io": 0.8},
        experience_prediction={"cpu": 0.8, "memory": 0.8, "io": 0.8},
        baseline_plans={"q": table_plan(10_000)},
        candidate_plans={"q": table_plan(10_000)},
        relation_stats=STATS,
        executor_parameters=EXECUTOR,
        concurrency=4,
    )
    assert result["features_used"]["estimated_cardinality"] is True
    assert result["features_used"]["estimated_row_width"] is True
    assert result["features_used"]["estimated_nested_loop_multiplier"] is True
    assert result["risk_flags"]["actual_rows_available_before_replay"] is False
    assert result["risk_flags"]["actual_operator_time_available_before_replay"] is False
    assert result["plan_changed"] is False


def test_planner_cost_only_change_is_not_a_physical_plan_transition() -> None:
    baseline = table_plan(10_000)
    candidate = table_plan(10_000)
    baseline["query_block"]["table"]["cost_info"]["read_cost"] = "10.0"
    candidate["query_block"]["table"]["cost_info"]["read_cost"] = "999.0"
    result = enhance_resource_prediction(
        incumbent_resources={"cpu": 0.8, "memory": 0.8, "io": 0.8},
        experience_prediction={"cpu": 0.8, "memory": 0.8, "io": 0.8},
        baseline_plans={"q": baseline},
        candidate_plans={"q": candidate},
        relation_stats=STATS,
        executor_parameters=EXECUTOR,
        concurrency=4,
    )
    assert result["baseline_raw_explain_sha256"] != result[
        "candidate_raw_explain_sha256"
    ]
    assert result["baseline_plan_sha256"] == result["candidate_plan_sha256"]
    assert result["plan_changed"] is False
    assert result["planner_cost_fields_excluded_from_physical_identity"] is True


def test_confidence_only_softens_ranking_score() -> None:
    assert confidence_adjusted_score(2.0, 0.5) == 1.0
    assert confidence_adjusted_score(-2.0, 0.5) == -4.0


def test_formal_controller_wires_context_model_without_hard_pruning() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/run_mysql_four_scene_algorithm_c_v1.py").read_text(
        encoding="utf-8"
    )
    contract_path = (
        root / "config/formal"
        / "frozen_mysql_algorithm_c_contextual_plan_physics_20260809_v2"
        / "model_contract.json"
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    model = root / contract["model"]["path"]
    assert hashlib.sha256(model.read_bytes()).hexdigest() == contract["model"]["sha256"]
    assert "candidate_plans = capture_scene_plans" in source
    assert "enhance_resource_prediction(" in source
    assert '"operator_count_hard_pruning": False' in source
    assert contract["ranking"]["operator_count_hard_pruning"] is False
    assert contract["ranking"]["real_replay_required"] is True
