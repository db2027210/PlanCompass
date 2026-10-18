#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.bonect_full_grid_estimator_scan import (
    apply_paired_delta_calibration,
    load_baseline_calibration,
)
from shell.bonect_replay_delta_tree import (
    PARAM_NAMES,
    fit_replay_delta_forest,
    fit_replay_delta_knn,
    fit_replay_delta_tree,
    predict_replay_delta_tree,
)


DEFAULT = {
    "seq_page_cost": 0.0001,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.000001,
    "cpu_index_tuple_cost": 0.000001,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 10000.0,
    "parallel_tuple_cost": 0.000001,
}


def replay_row(index: int, *, strong: bool) -> dict:
    params = dict(DEFAULT)
    params["random_page_cost"] = float(index + 1)
    if not strong:
        params["parallel_tuple_cost"] = 0.1
    return {
        "candidate_id": f"c{index}",
        "params": params,
        "plan_delta_features": {
            "weighted_gather_count": 258.0,
            "weighted_gather_workers_planned": 516.0,
            "weighted_parallel_aware_nodes": 258.0,
        },
        "db_qps_delta_pct": 140.0 + index if strong else 20.0 + index,
        "resource_delta_pp": {
            "cpu": 30.0 if strong else 2.0,
            "memory": -20.0 if strong else -3.0,
            "io": -15.0 if strong else -4.0,
        },
        "capability": {"cpu": 10.0, "memory": 2.0, "io": 1000.0},
    }


def main() -> None:
    rows = [replay_row(index, strong=index % 2 == 0) for index in range(8)]
    artifact = fit_replay_delta_tree(rows, max_depth=3, min_samples_leaf=1, seed=7)
    assert artifact["training_row_count"] == 8
    assert artifact["feature_names"][0] == "param_log10.seq_page_cost"
    assert artifact["split_feature_names"]
    assert artifact["leave_one_out_metrics"]["absolute_residual"]["db_qps_delta_pct"]["p95"] >= 0.0
    assert len(PARAM_NAMES) == 7
    prediction, detail = predict_replay_delta_tree(
        artifact,
        rows[0]["params"],
        rows[0]["plan_delta_features"],
    )
    assert set(prediction) == {"db_qps_delta_pct", "cpu_delta", "memory_delta", "io_delta"}
    assert prediction["db_qps_delta_pct"] > 100.0
    assert detail["leaf_samples"] >= 1
    assert "out_of_support_split_features" in detail

    knn_artifact = fit_replay_delta_knn(rows, n_neighbors=3)
    assert knn_artifact["model_type"] == "multioutput_standardized_knn_regressor"
    assert knn_artifact["n_neighbors"] == 3
    knn_prediction, knn_detail = predict_replay_delta_tree(
        knn_artifact,
        rows[0]["params"],
        rows[0]["plan_delta_features"],
    )
    assert abs(knn_prediction["db_qps_delta_pct"] - rows[0]["db_qps_delta_pct"]) < 1e-9
    assert knn_detail["training_candidates"] == ["c0"]
    assert knn_artifact["leave_one_out_metrics"]["qps_pairwise_error_aware_10pp"]["rate"] >= 0.0

    baseline_row = replay_row(99, strong=False)
    baseline_row.update(
        {
            "candidate_id": "__baseline_default__",
            "db_qps_delta_pct": 0.0,
            "resource_delta_pp": {"cpu": 0.0, "memory": 0.0, "io": 0.0},
        }
    )
    forest_artifact = fit_replay_delta_forest(
        rows + [baseline_row],
        n_estimators=32,
        max_depth=3,
        seed=7,
    )
    forest_prediction, forest_detail = predict_replay_delta_tree(
        forest_artifact,
        baseline_row["params"],
        baseline_row["plan_delta_features"],
    )
    assert all(abs(value) < 1e-9 for value in forest_prediction.values())
    assert forest_detail["forest_tree_count"] == 32
    assert forest_artifact["validation_protocol"]["baseline_candidates"] == [
        "__baseline_default__"
    ]

    with tempfile.TemporaryDirectory(prefix="bonect_replay_tree_") as tmp:
        root = Path(tmp)
        model_path = root / "model.json"
        model_path.write_text(json.dumps(artifact), encoding="utf-8")
        calibration_path = root / "calibration.json"
        calibration_path.write_text(
            json.dumps(
                {
                    "actual_resource": {"cpu": 5.0, "memory": 1.7, "io": 400.0},
                    "scales": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
                    "replay_delta_tree": {
                        "enabled": True,
                        "required": True,
                        "source": "unit_test",
                        "model_path": "model.json",
                    },
                }
            ),
            encoding="utf-8",
        )
        calibration = load_baseline_calibration(calibration_path)
        calibrated, qps_delta, metadata = apply_paired_delta_calibration(
            {"cpu": 1.0, "memory": 1.0, "io": 1.0},
            rows[0]["plan_delta_features"],
            calibration,
            rows[0]["params"],
        )
        assert qps_delta is not None and qps_delta > 100.0
        assert calibrated["cpu"] > 5.0
        assert calibrated["memory"] < 1.7
        assert metadata["source"] == "unit_test"
        assert metadata["teacher_coordinates_used"] is False
    print("bonect replay delta tree tests passed", flush=True)


if __name__ == "__main__":
    main()
