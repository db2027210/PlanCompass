#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parameter_optimizer import COST_PARAM_KEYS, ParameterOptimizer, ParameterRange


DEFAULTS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


class CountingEstimator:
    def __init__(self) -> None:
        self.calls = 0
        self.last_stats = {}
        self.last_window = None
        self.last_metadata = {"plan_change_count": 0}

    def __call__(self, _sql_set: object, _params: dict[str, float]) -> dict[str, float]:
        self.calls += 1
        self.last_stats = {}
        self.last_window = None
        self.last_metadata = {"plan_change_count": 0}
        return {"cpu": 8.0, "memory": 2.0, "io": 1.0}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="bonect-precomputed-") as temp_dir:
        root = Path(temp_dir)
        candidates_path = root / "candidates.jsonl"
        changed = dict(DEFAULTS)
        changed["seq_page_cost"] = 2.0
        changed["random_page_cost"] = 5.0
        rows = [
            {
                "candidate_index": 0,
                "params": DEFAULTS,
                "estimated_resource": {"cpu": 8.0, "memory": 2.0, "io": 1.0},
                "plan_hash": "baseline",
                "estimation_metadata": {},
                "estimated_db_qps_delta_pct": 0.0,
                "estimated_db_qps_error_bound_pct": 0.0,
                "plan_delta_features": {"weighted_gather_count": 100.0},
            },
            {
                "candidate_index": 1,
                "params": changed,
                "estimated_resource": {"cpu": 7.0, "memory": 2.2, "io": 1.1},
                "plan_hash": "changed",
                "estimation_metadata": {},
                "resource_error_profile": {"cpu": {"absolute": 0.2}},
                "estimated_db_qps_delta_pct": 35.5,
                "estimated_db_qps_error_bound_pct": 8.25,
                "estimated_db_qps_conservative_delta_pct": 27.25,
                "plan_delta_features": {"weighted_gather_count": 258.0},
            },
        ]
        candidates_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        (root / "scan_summary.json").write_text(
            json.dumps(
                {
                    "status": "completed",
                    "completed_candidates": 2,
                    "failure_count": 0,
                }
            ),
            encoding="utf-8",
        )

        estimator = CountingEstimator()
        optimizer = ParameterOptimizer(
            resource_estimation_func=estimator,
            capability={"cpu": 10.0, "memory": 10.0, "io": 10.0},
            verbose=False,
        )
        ranges = {
            name: ParameterRange(
                min_value=DEFAULTS[name],
                max_value=(
                    2.0
                    if name == "seq_page_cost"
                    else 5.0
                    if name == "random_page_cost"
                    else DEFAULTS[name]
                ),
                values=(
                    [DEFAULTS[name], 2.0]
                    if name == "seq_page_cost"
                    else [DEFAULTS[name], 5.0]
                    if name == "random_page_cost"
                    else [DEFAULTS[name]]
                ),
                discrete_count=(2 if name in {"seq_page_cost", "random_page_cost"} else 1),
            )
            for name in COST_PARAM_KEYS
        }
        log_path = root / "optimizer.jsonl"
        result = optimizer.optimize(
            p_real=dict(DEFAULTS),
            p_candidate=dict(DEFAULTS),
            param_ranges=ranges,
            sql_set=None,
            max_iter=1,
            early_stop_threshold=1,
            candidate_top_k=4,
            candidate_gate={"enabled": False, "target_resource": "cpu"},
            candidate_pair_probe={
                "enabled": True,
                "only_if_no_gate_candidate": False,
                "values_per_param": 5,
                "full_factorial_grid": True,
                "max_candidates": 2,
                "precomputed_candidates_path": str(candidates_path),
                "precomputed_require_complete": True,
                "progress_interval": 1,
            },
            log_path=str(log_path),
        )
        imported = [
            item
            for item in (result.candidate_rankings or [])
            if item.get("precomputed_candidate_index") == 1
        ]
        assert imported, result.candidate_rankings
        assert (imported[0].get("metadata") or {}).get("plan_change_count") == 1
        # The calibrated QPS delta + error bound must reach the candidate
        # record so robust ranking cannot silently discard calibrated QPS.
        assert imported[0].get("estimated_db_qps_delta_pct") == 35.5, imported[0]
        assert imported[0].get("estimated_db_qps_error_bound_pct") == 8.25, imported[0]
        assert imported[0].get("estimated_db_qps_conservative_delta_pct") == 27.25, imported[0]
        plan_feats = (imported[0].get("metadata") or {}).get("plan_delta_features")
        assert plan_feats == {"weighted_gather_count": 258.0}, plan_feats
        surrogate = imported[0].get("surrogate_prediction") or {}
        assert surrogate.get("source") == "precomputed_rescore_anchored_forest", surrogate
        assert surrogate.get("predicted_db_qps_delta_pct") == 35.5, surrogate
        assert surrogate.get("uncertainty") == 8.25, surrogate
        # bonect_robust_ranking.robust_score_record must observe the calibrated
        # QPS fields on the record and propagate them into robust_ranking.
        robust = imported[0].get("robust_ranking") or {}
        assert robust.get("estimated_db_qps_delta_pct") == 35.5, robust
        assert robust.get("estimated_db_qps_error_bound_pct") == 8.25, robust
        assert robust.get("db_qps_surrogate_applied") is True, robust
        assert robust.get("db_qps_surrogate_adjustment") > 0.0, robust
        actual_logs = sorted(root.glob("optimizer*.jsonl"))
        assert actual_logs
        events = [json.loads(line) for line in actual_logs[-1].read_text().splitlines()]
        assert any(event.get("event") == "candidate_pair_probe_precomputed_start" for event in events)
        assert any(
            event.get("event") == "candidate_pair_probe_end" and event.get("precomputed")
            for event in events
        )
    print("bonect precomputed candidate import tests passed", flush=True)


if __name__ == "__main__":
    main()
