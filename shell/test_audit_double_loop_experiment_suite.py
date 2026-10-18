#!/usr/bin/env python3
"""Focused tests for the double-loop suite auditor."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from audit_double_loop_experiment_suite import (
    choose_state,
    expected_tasks,
    progress_evidence,
    validate_artifact,
)


def test_choose_state_prefers_live_worker_over_root_local_attempt() -> None:
    chosen = choose_state(
        [
            {
                "status": "preparing",
                "attempt": 3,
                "worker_pid": 999999999,
                "state_mtime_ns": 10,
                "scheduler_root": "stale",
            },
            {
                "status": "preparing",
                "attempt": 1,
                "worker_pid": os.getpid(),
                "state_mtime_ns": 20,
                "scheduler_root": "active",
            },
        ]
    )
    assert chosen is not None
    assert chosen["scheduler_root"] == "active"

    reviewed = choose_state(
        [
            {
                "status": "preparing",
                "attempt": 3,
                "worker_pid": 999999999,
                "state_mtime_ns": 10,
                "scheduler_root": "stale",
            },
            {
                "status": "needs_review",
                "attempt": 1,
                "worker_pid": 999999998,
                "state_mtime_ns": 20,
                "scheduler_root": "current",
            },
        ]
    )
    assert reviewed is not None
    assert reviewed["scheduler_root"] == "current"

    canonical = choose_state(
        [
            {
                "status": "needs_review",
                "attempt": 1,
                "state_mtime_ns": 10,
                "scheduler_root": "canonical",
            },
            {
                "status": "held",
                "attempt": 0,
                "state_mtime_ns": 20,
                "scheduler_root": "newer_satellite",
            },
        ]
    )
    assert canonical is not None
    assert canonical["scheduler_root"] == "canonical"


def test_expected_tasks() -> None:
    matrix = {
        "experiments": [
            {"exp_id": "A1", "benchmark": "x", "bottleneck": "cpu", "runs": 1},
            {
                "exp_id": "B1",
                "benchmark": "x",
                "bottleneck": "cpu",
                "runs": 2,
                "repeat_ids": [2, 3],
            },
        ]
    }
    rows = expected_tasks(matrix)
    assert [row["task_id"] for row in rows] == ["a1_r1", "b1_r2", "b1_r3"]


def test_valid_artifact() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        raw = root / "final.json"
        raw.write_text("{}\n", encoding="utf-8")
        params = {
            "seq_page_cost": 1.0,
            "random_page_cost": 4.0,
            "cpu_tuple_cost": 0.01,
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0025,
            "parallel_setup_cost": 1000.0,
            "parallel_tuple_cost": 0.1,
        }
        artifact = root / "a1_r1.json"
        artifact.write_text(
            json.dumps(
                {
                "meta": {
                    "exp_id": "A1",
                    "benchmark": "x",
                    "db": "pg17",
                    "method": "coordinate_grid_resource_estimation_topk_replay",
                    "bottleneck": "cpu",
                    "bottleneck_target": 0.9,
                    "repeat_id": 1,
                    "machine_spec": {"cpu_cores": 2, "mem_gb": 8, "disk_bw_mb": 100},
                },
                "outer_loop": [
                    {
                        "iter": 1,
                        "params": params,
                        "qps": 120,
                        "latency_p99_ms": 10,
                        "latency_p95_ms": 8,
                        "resource": {"cpu": 0.84, "mem": 0.2, "io": 0.1},
                        "utility": 0.8,
                        "explain_samples": ["Plan"],
                    }
                ],
                "inner_loop_summary": {
                    "total_iterations": 10,
                    "wall_time_ms": 5,
                    "best_estimated_utility": 0.8,
                    "best_params": params,
                },
                "result": {
                    "final_params": params,
                    "delta_u_pct": 5,
                    "qps_before": 100,
                    "qps_after": 120,
                    "p99_before_ms": 12,
                    "p99_after_ms": 10,
                    "resource_before": {"cpu": 0.95, "mem": 0.2, "io": 0.1},
                    "resource_after": {"cpu": 0.855, "mem": 0.21, "io": 0.11},
                    "bad_config": False,
                    "converge_outer_iters": 1,
                    "total_wall_time_s": 30,
                },
                "audit": {
                    "independent_final_real_replay": True,
                    "final_raw_validation_path": str(raw),
                },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        ok, errors, metrics = validate_artifact(
            artifact,
            {
                "exp_id": "A1",
                "benchmark": "x",
                "bottleneck": "cpu",
                "repeat_id": 1,
            },
            {
                "bottleneck_min_qps_gain_ratio": 0.15,
                "bottleneck_min_target_drop_ratio": 0.05,
                "bottleneck_max_target_drop_ratio": 0.10,
            },
        )
        assert ok, errors
        assert metrics["qps_gain_ratio"] == 0.2


def test_progress_counts_only_replays_and_timestamped_optimizer() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        task_root = Path(temporary)
        scenario_dir = task_root / "formal_runs" / "run" / "cpu"
        scenario_dir.mkdir(parents=True)
        calibration_path = scenario_dir / "fresh_model_calibration.jsonl"
        calibration_path.write_text(
            "\n".join(
                [
                    json.dumps({"event": "fresh_model_calibration_replay"}),
                    json.dumps({"event": "fresh_model_calibration_replay"}),
                    json.dumps({"event": "fresh_model_quality_gate_attempt"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (scenario_dir / "fresh_model_quality_gate.json").write_text(
            json.dumps({"passed": True}) + "\n",
            encoding="utf-8",
        )
        optimizer_path = scenario_dir / "optimizer_2026-07-20_180739.jsonl"
        optimizer_path.write_text(
            "\n".join(
                [
                    json.dumps({"event": "optimizer_preflight_quality_gate"}),
                    json.dumps(
                        {
                            "event": "adaptive_topk_decision",
                            "replayed_k": 3,
                            "usable_real_replay_count": 2,
                            "failed_real_replay_count": 1,
                            "action": "expand_k",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        validation_dir = scenario_dir / "real_validations"
        validation_dir.mkdir()
        for index in range(7):
            (validation_dir / f"cpu_double_loop_{index:04d}.json").write_text(
                json.dumps({"double_loop_measurement_usable": True}) + "\n",
                encoding="utf-8",
            )
        (task_root / "guest_exit_diagnostics.json").write_text(
            json.dumps({"output": "kernel: invoked oom-killer; Out of memory: Killed process 42"})
            + "\n",
            encoding="utf-8",
        )

        progress = progress_evidence({"task_root": str(task_root)}, "cpu")

        assert progress["calibration_rows"] == 2
        assert progress["model_quality_gate_present"] is True
        assert progress["model_quality_gate_passed"] is True
        assert progress["phase"] == "optimizer"
        assert progress["optimizer_path"] == str(optimizer_path)
        assert progress["topk_replayed"] == 3
        assert progress["topk_usable_real_replays"] == 2
        assert progress["topk_failed_real_replays"] == 1
        assert progress["topk_last_action"] == "expand_k"
        assert progress["topk_physical_validation_files"] == 3
        assert progress["topk_real_replay_evidence_consistent"] is True
        assert progress["failure_class"] == "guest_oom"
        assert progress["guest_exit_diagnostics_path"] == str(
            task_root / "guest_exit_diagnostics.json"
        )


def test_progress_marks_fresh_baseline_after_warmup() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        task_root = Path(temporary)
        scenario_dir = task_root / "formal_runs" / "run" / "io"
        scenario_dir.mkdir(parents=True)
        (scenario_dir / "pre_baseline_warmup.json").write_text("{}\n", encoding="utf-8")

        progress = progress_evidence({"task_root": str(task_root)}, "io")

        assert progress["phase"] == "fresh_baseline_in_progress"


def test_progress_aggregates_topk_replays_across_outer_rounds() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        task_root = Path(temporary)
        scenario_dir = task_root / "formal_runs" / "run" / "cpu"
        scenario_dir.mkdir(parents=True)
        optimizer_path = scenario_dir / "optimizer_2026-07-20_180739.jsonl"
        optimizer_path.write_text(
            "\n".join(
                [
                    json.dumps({"event": "double_loop_grid_round", "round": 0}),
                    json.dumps(
                        {
                            "event": "adaptive_topk_decision",
                            "round": 0,
                            "replayed_k": 14,
                            "usable_real_replay_count": 13,
                            "failed_real_replay_count": 1,
                            "action": "expand_k",
                        }
                    ),
                    json.dumps(
                        {
                            "event": "adaptive_topk_decision",
                            "round": 0,
                            "replayed_k": 15,
                            "usable_real_replay_count": 14,
                            "failed_real_replay_count": 1,
                            "action": "stop_stable",
                        }
                    ),
                    json.dumps(
                        {
                            "event": "adaptive_topk_decision",
                            "round": 1,
                            "replayed_k": 1,
                            "usable_real_replay_count": 1,
                            "failed_real_replay_count": 0,
                            "action": "expand_k",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        validation_dir = scenario_dir / "real_validations"
        validation_dir.mkdir()
        # Two baselines plus 16 cumulative Top-K physical replays.
        for index in range(18):
            (validation_dir / f"cpu_double_loop_{index:04d}.json").write_text(
                json.dumps({"double_loop_measurement_usable": True}) + "\n",
                encoding="utf-8",
            )

        progress = progress_evidence({"task_root": str(task_root)}, "cpu")

        assert progress["outer_rounds"] == 1
        assert progress["topk_replayed"] == 16
        assert progress["topk_usable_real_replays"] == 15
        assert progress["topk_failed_real_replays"] == 1
        assert progress["topk_last_action"] == "expand_k"
        assert progress["topk_physical_validation_files"] == 16
        assert progress["topk_real_replay_evidence_consistent"] is True


if __name__ == "__main__":
    test_choose_state_prefers_live_worker_over_root_local_attempt()
    test_expected_tasks()
    test_valid_artifact()
    test_progress_counts_only_replays_and_timestamped_optimizer()
    test_progress_marks_fresh_baseline_after_warmup()
    test_progress_aggregates_topk_replays_across_outer_rounds()
    print("double-loop suite audit tests passed", flush=True)
