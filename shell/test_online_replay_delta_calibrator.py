#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from online_replay_delta_calibrator import OnlineReplayDeltaCalibrator


BASELINE_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
BASELINE_RESOURCE = {"cpu": 1.6, "memory": 1.2, "io": 136.0}
CAPABILITY = {"cpu": 2.0, "memory": 8.0, "io": 800.0}


def build_candidate(index: int) -> dict:
    fraction = (index - 8.5) / 8.5
    params = dict(BASELINE_PARAMS)
    params["seq_page_cost"] *= math.exp(0.35 * fraction)
    params["random_page_cost"] *= math.exp(-0.20 * fraction)
    params["cpu_tuple_cost"] *= math.exp(0.25 * fraction)
    predicted_pp = {
        "cpu": -2.2 * fraction,
        "memory": 0.8 * fraction,
        "io": 1.4 * fraction,
    }
    resource = {
        name: BASELINE_RESOURCE[name] + predicted_pp[name] * CAPABILITY[name] / 100.0
        for name in BASELINE_RESOURCE
    }
    return {
        "params": params,
        "resource": resource,
        "score": 1.0 + fraction,
        "metadata": {
            "resource_breakdown": {
                "cpu": {
                    "calibration_breakdown": {
                        "learned_model": {
                            "features": {
                                "F_scan_tuple": 1000.0 * (1.0 + 0.4 * fraction),
                                "F_sort_cmp": 5000.0 * (1.0 - 0.3 * fraction),
                            }
                        }
                    }
                }
            }
        },
    }


def build_evaluation(index: int, candidate: dict) -> dict:
    fraction = (index - 8.5) / 8.5
    actual_pp = {
        "cpu": -3.0 * fraction,
        "memory": 1.2 * fraction,
        "io": 2.0 * fraction,
    }
    actual = {
        name: BASELINE_RESOURCE[name] + actual_pp[name] * CAPABILITY[name] / 100.0
        for name in BASELINE_RESOURCE
    }
    return {
        "_candidate_index": index,
        "candidate_primary_target_status": {"db_qps_delta_pct": 8.0 * fraction},
        "candidate_final_validation": {"resource_normalized": actual},
        "params": candidate["params"],
    }


def main() -> None:
    candidates = [build_candidate(index) for index in range(18)]
    calibrator = OnlineReplayDeltaCalibrator(
        baseline_params=BASELINE_PARAMS,
        baseline_resource=BASELINE_RESOURCE,
        capability=CAPABILITY,
        target_resource="cpu",
        min_samples=12,
    )
    insufficient = calibrator.fit_and_apply(
        candidates=candidates,
        evaluations=[build_evaluation(index, candidates[index]) for index in range(6)],
    )
    assert insufficient["available"] is False
    assert insufficient["delta_calibration_requires_expansion"] is True

    result = calibrator.fit_and_apply(
        candidates=candidates,
        evaluations=[build_evaluation(index, candidates[index]) for index in range(12)],
    )
    assert result["available"] is True
    assert result["sample_count"] == 12
    assert result["training_gate_passed"] is True, result
    assert result["loo_db_qps_pairwise_inversion_rate"] <= 0.10
    assert all("predicted_resource" in candidate for candidate in candidates)
    assert all("_adaptive_replay_rank_score" in candidate for candidate in candidates)
    assert candidates[-1]["predicted_db_qps_delta_pct"] > candidates[0]["predicted_db_qps_delta_pct"]
    print("online replay delta calibrator tests passed", flush=True)


if __name__ == "__main__":
    main()
