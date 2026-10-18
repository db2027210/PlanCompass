#!/usr/bin/env python3
"""Verify No-bottleneck rescue uses any resource reaching its sweet range."""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys


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


class NoneSweetEstimator:
    def __init__(self) -> None:
        self.last_stats = {}
        self.last_window = None
        self.last_metadata = {}

    def __call__(self, _sql_set: object, params: dict[str, float]) -> dict[str, float]:
        value = float(params["seq_page_cost"])
        self.last_window = None
        self.last_metadata = {}
        if value == 2.0:
            # Only CPU rises into its 65-80% sweet range.
            resource = {"cpu": 70.0, "memory": 20.0, "io": 20.0}
            qps = 110.0
        elif value == 3.0:
            # All resources rise, but none reaches its configured sweet range.
            resource = {"cpu": 40.0, "memory": 40.0, "io": 40.0}
            qps = 150.0
        else:
            resource = {"cpu": 25.0, "memory": 25.0, "io": 25.0}
            qps = 100.0
        self.last_stats = {"qps": {"avg": qps}}
        return resource


def main() -> None:
    optimizer = ParameterOptimizer(
        resource_estimation_func=NoneSweetEstimator(),
        capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        resource_target_ranges={
            "cpu": {"min_pct": 65.0, "max_pct": 80.0},
            "memory": {"min_pct": 70.0, "max_pct": 80.0},
            "io": {"min_pct": 50.0, "max_pct": 75.0},
        },
        verbose=False,
    )
    with tempfile.TemporaryDirectory(prefix="phase2-none-sweet-") as temp_dir:
        result = optimizer.optimize(
            p_real=dict(DEFAULT_PARAMS),
            p_candidate=dict(DEFAULT_PARAMS),
            param_ranges={
                "seq_page_cost": ParameterRange(
                    min_value=1.0,
                    max_value=3.0,
                    values=[1.0, 2.0, 3.0],
                    discrete_count=3,
                )
            },
            sql_set=None,
            max_iter=1,
            early_stop_threshold=1,
            candidate_top_k=10,
            candidate_gate={
                "enabled": False,
                "target_resource": "none",
                "db_qps_proxy_score_enabled": True,
                "directional_rescue": {
                    "enabled": True,
                    "min_topk": 2,
                    "min_other_up_util_pct": 0.0,
                },
                "soft_directional_fallback": {
                    "enabled": True,
                    "min_topk": 2,
                    "min_other_up_util_pct": 0.0,
                },
            },
            candidate_pair_probe={"enabled": False},
            log_path=str(Path(temp_dir) / "optimizer.jsonl"),
        )

    candidates = list(getattr(result, "candidate_rankings", []) or [])
    by_value = {
        float((item.get("params") or {}).get("seq_page_cost")): item
        for item in candidates
    }
    sweet = by_value[2.0]["directional_rescue"]
    all_up_but_not_sweet = by_value[3.0]["directional_rescue"]
    assert sweet["passed"] is True, sweet
    assert sweet["sweet_up_resources"] == ["cpu"], sweet
    assert all_up_but_not_sweet["passed"] is False, all_up_but_not_sweet
    assert all_up_but_not_sweet["sweet_up_resources"] == [], all_up_but_not_sweet
    soft_sweet = by_value[2.0]["soft_directional_fallback"]
    soft_all_up_but_not_sweet = by_value[3.0]["soft_directional_fallback"]
    assert soft_sweet["passed"] is True, soft_sweet
    assert soft_sweet["sweet_up_resources"] == ["cpu"], soft_sweet
    assert soft_all_up_but_not_sweet["passed"] is False, soft_all_up_but_not_sweet
    ordered_values = [
        float((item.get("params") or {}).get("seq_page_cost"))
        for item in candidates
    ]
    assert ordered_values.index(3.0) < ordered_values.index(2.0), ordered_values
    print("PASS Phase2 None rescue requires any resource up into sweet range", flush=True)


if __name__ == "__main__":
    main()
