#!/usr/bin/env python3
"""Verify unsupported delta-model candidates are retained for diagnostic replay."""

from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parameter_optimizer import ParameterOptimizer, ParameterRange


DEFAULT_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


class ConstantEstimator:
    def __init__(self) -> None:
        self.last_stats = {}
        self.last_window = None
        self.last_metadata = {}

    def __call__(self, _sql_set: object, _params: dict[str, float]) -> dict[str, float]:
        self.last_stats = {}
        self.last_window = None
        self.last_metadata = {}
        return {"cpu": 10.0, "memory": 20.0, "io": 30.0}


def write_artifact(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "baseline_params": DEFAULT_PARAMS,
                "baseline_plan_features": {},
                "baseline_resource": {"cpu": 10.0, "memory": 20.0, "io": 30.0},
                "feature_names": ["param_log:seq_page_cost"],
                "feature_scales": [1.0],
                "bandwidth": 0.4,
                "support": {"max_nearest_distance": 0.1},
                "training_gate": {"passed": True},
                "points": [
                    {
                        "x": [0.0],
                        "labels": {
                            "db_qps_delta_pct": 0.0,
                            "cpu_delta_pct": 0.0,
                            "memory_delta_pct": 0.0,
                            "io_delta_pct": 0.0,
                        },
                    },
                    {
                        "x": [math.log(2.0)],
                        "labels": {
                            "db_qps_delta_pct": 10.0,
                            "cpu_delta_pct": 5.0,
                            "memory_delta_pct": 2.0,
                            "io_delta_pct": 3.0,
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="phase2-delta-unsupported-") as temp_dir:
        root = Path(temp_dir)
        artifact = root / "delta.json"
        write_artifact(artifact)
        optimizer = ParameterOptimizer(
            resource_estimation_func=ConstantEstimator(),
            capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
            verbose=False,
        )
        result = optimizer.optimize(
            p_real=dict(DEFAULT_PARAMS),
            p_candidate=dict(DEFAULT_PARAMS),
            param_ranges={
                "seq_page_cost": ParameterRange(
                    min_value=1.0,
                    max_value=4.0,
                    values=[1.0, 4.0],
                    discrete_count=2,
                )
            },
            sql_set=None,
            max_iter=1,
            early_stop_threshold=1,
            candidate_top_k=3,
            candidate_gate={
                "enabled": False,
                "target_resource": "none",
                "baseline_resource_override": {
                    "cpu": 10.0,
                    "memory": 20.0,
                    "io": 30.0,
                },
                "real_delta_calibration": {
                    "enabled": True,
                    "artifact_path": str(artifact),
                    "require_training_gate": True,
                    "require_prediction": True,
                    "require_support": True,
                    "unsupported_policy": "keep_uncertain",
                    "uncertain_replay_min_count": 1,
                    "min_plan_feature_coverage": 1.0,
                },
            },
            candidate_pair_probe={"enabled": False},
            log_path=str(root / "optimizer.jsonl"),
        )
        candidates = list(getattr(result, "candidate_rankings", []) or [])

    unsupported = [
        candidate
        for candidate in candidates
        if candidate.get("phase2_real_delta_uncertain")
    ]
    assert unsupported, candidates
    assert any(
        candidate.get("selection_reason") == "real_delta_support_boundary_probe"
        for candidate in unsupported
    ), unsupported
    assert all(
        not (candidate.get("phase2_real_delta_calibration") or {}).get("applied")
        for candidate in unsupported
    ), unsupported
    print("PASS Phase2 unsupported delta candidates enter diagnostic replay", flush=True)


if __name__ == "__main__":
    main()
