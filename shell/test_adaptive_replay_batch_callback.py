#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adaptive_replay_per_batch_runner import run_adaptive_replay_batches


def main() -> None:
    candidates = [
        {
            "params": {"seq_page_cost": 1.0 + index / 10.0},
            "predicted_resource": {"cpu": 40.0, "memory": 50.0, "io": 50.0},
            "predicted_db_qps_delta_pct": 20.0 - index,
            "_adaptive_replay_rank_score": 100.0 - index,
        }
        for index in range(8)
    ]
    batch_calls = []

    def serial_must_not_run(_candidate: dict) -> dict:
        raise AssertionError("serial callback must not run when batch callback is set")

    def replay_batch(batch: list) -> list:
        batch_calls.append([row["_candidate_index"] for row in batch])
        # Return in reverse order to prove matching uses _candidate_index, not position.
        return [
            {
                "_candidate_index": row["_candidate_index"],
                "params": row["params"],
                "candidate_primary_target_status": {
                    "db_qps_delta_pct": 10.0 + row["_candidate_index"],
                    "bottleneck_resource_delta_pp": -5.0,
                },
                "target_direction_ok": True,
                "phase2_long_term_goal_met": True,
                "performance_guard_ok": True,
            }
            for row in reversed(batch)
        ]

    def summary(evaluations: list) -> dict:
        return {
            "pairwise_inversion_rate": 0.0,
            "first_stage_ranking_validation_pass": True,
            "explainable_lane_hit_count": len(evaluations),
        }

    result = run_adaptive_replay_batches(
        candidates=candidates,
        replay_callback=serial_must_not_run,
        replay_batch_callback=replay_batch,
        summary_callback=summary,
        target_resource="cpu",
        baseline_resource={"cpu": 50.0, "memory": 50.0, "io": 50.0},
        capability={"cpu": 100.0, "memory": 100.0, "io": 100.0},
        batch_size=4,
        min_evidence=4,
        max_budget=4,
        max_expansions=0,
    )
    assert batch_calls == [[0, 1, 2, 3]], batch_calls
    assert result.replayed_indices == [0, 1, 2, 3], result.replayed_indices
    assert [
        row["candidate_primary_target_status"]["db_qps_delta_pct"]
        for row in result.replayed_evaluations
    ] == [10.0, 11.0, 12.0, 13.0]
    print("adaptive replay batch callback tests passed", flush=True)


if __name__ == "__main__":
    main()
