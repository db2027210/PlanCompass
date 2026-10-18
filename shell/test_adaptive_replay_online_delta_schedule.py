#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adaptive_replay_per_batch_runner import run_adaptive_replay_batches


def candidates() -> list:
    return [
        {
            "params": {"seq_page_cost": 1.0 + index / 100.0},
            "predicted_resource": {"cpu": 40.0, "memory": 51.0, "io": 50.0},
            "predicted_db_qps_delta_pct": 20.0 - index / 10.0,
            "_adaptive_replay_rank_score": 100.0 - index,
        }
        for index in range(30)
    ]


def replay(candidate: dict) -> dict:
    return {
        "params": candidate["params"],
        "candidate_primary_target_status": {
            "db_qps_delta_pct": 10.0,
            "bottleneck_resource_delta_pp": -5.0,
        },
        "target_direction_ok": True,
        "phase2_long_term_goal_met": True,
        "performance_guard_ok": True,
    }


def summary(evaluations: list) -> dict:
    return {
        "pairwise_inversion_rate": 0.0,
        "pairwise_inversion_threshold": 0.10,
        "first_stage_ranking_validation_pass": len(evaluations) >= 12,
        "explainable_lane_hit_count": len(evaluations),
    }


def main() -> None:
    def pass_at_18(_candidates: list, evaluations: list, _summary: dict) -> dict:
        return {
            "available": len(evaluations) >= 12,
            "sample_count": len(evaluations),
            "training_gate_passed": len(evaluations) >= 18,
            "delta_calibration_requires_expansion": len(evaluations) < 18,
            "loo_abs_error_p90": {"db_qps_delta_pct": 1.0},
        }

    result_18 = run_adaptive_replay_batches(
        candidates=candidates(),
        replay_callback=replay,
        summary_callback=summary,
        post_batch_callback=pass_at_18,
        target_resource="cpu",
        baseline_resource={"cpu": 50.0, "memory": 50.0, "io": 50.0},
        capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        min_evidence=12,
        batch_size=6,
        max_budget=24,
        max_expansions=0,
    )
    assert len(result_18.replayed_evaluations) == 18, result_18
    assert result_18.final_decision == "PROCEED_TO_OUTER_LOOP", result_18

    def always_fail(_candidates: list, evaluations: list, _summary: dict) -> dict:
        return {
            "available": len(evaluations) >= 12,
            "sample_count": len(evaluations),
            "training_gate_passed": False,
            "delta_calibration_requires_expansion": True,
        }

    result_24 = run_adaptive_replay_batches(
        candidates=candidates(),
        replay_callback=replay,
        summary_callback=summary,
        post_batch_callback=always_fail,
        target_resource="cpu",
        baseline_resource={"cpu": 50.0, "memory": 50.0, "io": 50.0},
        capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        min_evidence=12,
        batch_size=6,
        max_budget=24,
        max_expansions=0,
    )
    assert len(result_24.replayed_evaluations) == 24, result_24
    assert result_24.final_decision == "STOP_FIX_RANKING", result_24
    print("adaptive replay online-delta 12->18->24 schedule tests passed", flush=True)


if __name__ == "__main__":
    main()
