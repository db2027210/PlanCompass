#!/usr/bin/env python3
"""Verify single-parameter plateaus do not hide pair-only plan effects."""

from __future__ import annotations

import json
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


class PairOnlyPlanEstimator:
    """Changes its plan signature only when both tested parameters are high."""

    def __init__(self) -> None:
        self.last_stats = {}
        self.last_window = None
        self.last_metadata = {}

    def plan_changed(self, params: dict[str, float]) -> bool:
        return (
            float(params.get("seq_page_cost", 1.0)) >= 2.0
            and float(params.get("parallel_setup_cost", 1000.0)) >= 2000.0
        )

    def __call__(self, _sql_set: object, params: dict[str, float]) -> dict[str, float]:
        node_type = "Index Scan" if self.plan_changed(params) else "Seq Scan"
        self.last_stats = {}
        self.last_window = None
        self.last_metadata = {
            "plan_resource_estimation": {
                "resource_breakdown": {
                    "cpu": {
                        "execution_breakdown": {
                            "explained_query_count": 1,
                            "failed_query_count": 0,
                        },
                        "operator_breakdown": [
                            {
                                "node_type": node_type,
                                "relation_name": "transfer",
                                "depth": 0,
                                "queryid": "1",
                                "rows": 100.0,
                                "width": 16.0,
                                "self_cost": 10.0,
                                "memory_bytes": 1024.0,
                                "derived_memory_mb": 0.001,
                                "total_blocks": 8.0,
                                "cpu_work": 100.0,
                            }
                        ],
                        "query_breakdown": [
                            {
                                "queryid": "1",
                                "value": 10.0,
                                "memory_bytes": 1024.0,
                                "total_blocks": 8.0,
                                "cpu_work": 100.0,
                                "replay_count": 1,
                            }
                        ],
                    }
                }
            }
        }
        return {"cpu": 10.0, "memory": 20.0, "io": 30.0}


class PartnerOnlyPlanEstimator(PairOnlyPlanEstimator):
    """Only seq_page_cost changes the plan; setup must remain filtered."""

    def plan_changed(self, params: dict[str, float]) -> bool:
        return float(params.get("seq_page_cost", 1.0)) >= 2.0


def run_filter(estimator: PairOnlyPlanEstimator) -> dict:
    with tempfile.TemporaryDirectory(prefix="phase2-interaction-filter-") as temp_dir:
        root = Path(temp_dir)
        log_path = root / "optimizer.jsonl"
        optimizer = ParameterOptimizer(
            resource_estimation_func=estimator,
            capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
            verbose=False,
        )
        optimizer.optimize(
            p_real=dict(DEFAULT_PARAMS),
            p_candidate=dict(DEFAULT_PARAMS),
            param_ranges={
                "seq_page_cost": ParameterRange(
                    min_value=1.0,
                    max_value=2.0,
                    values=[1.0, 2.0],
                    discrete_count=2,
                ),
                "parallel_setup_cost": ParameterRange(
                    min_value=1000.0,
                    max_value=2000.0,
                    values=[1000.0, 2000.0],
                    discrete_count=2,
                ),
            },
            sql_set=None,
            max_iter=1,
            early_stop_threshold=1,
            candidate_top_k=4,
            candidate_gate={
                "enabled": False,
                "target_resource": "none",
                "preflight_param_feature_filter": {
                    "enabled": True,
                    "min_changed_endpoint_count": 1,
                    "min_active_params": 0,
                    "fallback_to_resource_values": False,
                    "drop_if_feature_unavailable": False,
                    "interaction_probe": {
                        "enabled": True,
                        "max_partner_params": 1,
                        "min_changed_pair_count": 1,
                    },
                },
            },
            candidate_pair_probe={"enabled": False},
            log_path=str(log_path),
        )

        actual_logs = sorted(root.glob("optimizer_*.jsonl"))
        assert len(actual_logs) == 1, actual_logs
        records = [
            json.loads(line)
            for line in actual_logs[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    return next(
        record
        for record in records
        if record.get("event") == "candidate_param_feature_filter"
    )


def main() -> None:
    event = run_filter(PairOnlyPlanEstimator())
    assert set(event["active_params_after"]) == {
        "seq_page_cost",
        "parallel_setup_cost",
    }, event
    assert set(event["interaction_restored_params"]) == {
        "seq_page_cost",
        "parallel_setup_cost",
    }, event
    for detail in event["details"]:
        assert detail["reason"] == "interaction_resource_features_changed", detail
        assert detail["interaction_probe"]["changed"], detail

    partner_only_event = run_filter(PartnerOnlyPlanEstimator())
    assert partner_only_event["active_params_after"] == ["seq_page_cost"], partner_only_event
    assert partner_only_event["interaction_restored_params"] == [], partner_only_event
    setup_detail = next(
        detail
        for detail in partner_only_event["details"]
        if detail["parameter"] == "parallel_setup_cost"
    )
    assert setup_detail["reason"] == "endpoint_resource_features_unchanged", setup_detail
    assert not setup_detail["interaction_probe"]["changed"], setup_detail
    print("PASS pair-only plan effects survive preflight filtering", flush=True)


if __name__ == "__main__":
    main()
