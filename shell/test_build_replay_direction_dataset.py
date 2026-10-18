#!/usr/bin/env python3
"""Regression tests for real-replay direction dataset extraction."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.build_replay_direction_dataset import (  # noqa: E402
    build_dataset_document,
    extract_replay_records,
)


def validation(qps: float, cpu_pct: float) -> dict:
    return {
        "formal_qps": qps,
        "utilization_pct": {"cpu": cpu_pct, "memory": 40.0, "io": 40.0},
        "resource_stats": {
            "candidate_measurement_protocol": {"stable": True},
            "formal_pg_resource": {
                "caps": {
                    "cpu_quota_cores": 4.0,
                    "memory_bytes": 4 * 1024**3,
                    "io_total_cap_bps": 50 * 1024**2,
                }
            },
        },
    }


def test_extracts_real_stable_replays_and_reverse_probe() -> None:
    baseline = {"a": 1.0, "b": 1.0}
    rows = []
    for index in range(5):
        rows.append(
            {
                "candidate_index": index + 1,
                "candidate_optimized_params": {"a": 1.2, "b": 0.8},
                "candidate_final_validation": validation(99.0 - index, 91.0),
                "common_outer_acceptance": {
                    "accepted": False,
                    "target_resource": "cpu",
                },
            }
        )
    event = {
        "event": "final_feedback_round_complete",
        "round_index": 2,
        "round_start_params": baseline,
        "final_validation": validation(100.0, 90.0),
        "candidate_evaluations": rows,
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        history = Path(temp_dir) / "tpcc_optimizer_history.jsonl"
        history.write_text(json.dumps(event) + "\n", encoding="utf-8")
        records, audit = extract_replay_records(
            [history],
            workload_filter="tpcc",
            target_filter="cpu",
        )
        document = build_dataset_document(
            records,
            audit,
            inputs=[history],
            workload_filter="tpcc",
            target_filter="cpu",
        )

    assert len(records) == 5
    assert all(record.stable_measurement for record in records)
    assert records[0].resource_profile["cpu"] == 4.0
    assert document["workload_target_groups"][0]["joint_success_count"] == 0
    decision = document["same_incumbent_scope_decisions"][0]["decision"]
    assert decision["vector_failure_recovery"]["action"] == "reverse_probe"
    assert decision["runtime_policy"]["probe_only"] is True
    assert decision["runtime_policy"]["outer_shrink_forbidden_until_real_acceptance"] is True


def test_rejects_explicitly_unstable_candidate() -> None:
    candidate = validation(101.0, 89.0)
    candidate["resource_stats"]["candidate_measurement_protocol"]["stable"] = False
    event = {
        "round_start_params": {"a": 1.0},
        "final_validation": validation(100.0, 90.0),
        "candidate_evaluations": [
            {
                "candidate_optimized_params": {"a": 1.2},
                "candidate_final_validation": candidate,
                "common_outer_acceptance": {"target_resource": "cpu"},
            }
        ],
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        history = Path(temp_dir) / "tpcc_optimizer_history.jsonl"
        history.write_text(json.dumps(event) + "\n", encoding="utf-8")
        records, _ = extract_replay_records([history])
    assert records == []


def fresh_raw(params: dict, qps: float, cpu: float) -> dict:
    return {
        "event": "real_validation",
        "status": "ok",
        "workload": "tpcc",
        "target": "cpu",
        "params": params,
        "db_qps": qps,
        "resources": {"cpu": cpu, "memory": 1.0, "io": 1.0},
        "runtime_capability": {"cpu": 4.0, "memory": 8.0, "io": 100.0},
        "actual_utilization_pct": {"cpu": cpu / 4.0 * 100.0, "memory": 12.5, "io": 1.0},
        "double_loop_measurement_usable": True,
    }


def test_fresh_coordinate_is_attributable_but_factorial_is_vector_only() -> None:
    baseline_params = {"a": 1.0, "b": 1.0}
    baseline = {
        "event": "fresh_baseline",
        "scenario": "cpu",
        "params": baseline_params,
        "measurement": {"db_qps": 100.0, "cpu": 3.6},
        "raw": fresh_raw(baseline_params, 100.0, 3.6),
    }
    coordinate_params = {"a": 2.0, "b": 1.0}
    factorial_params = {"a": 2.0, "b": 0.5}
    rows = [
        {
            "event": "fresh_model_calibration_replay",
            "calibration_id": "axis_1",
            "phase": "coordinate_grid_calibration",
            "parameter": "a",
            "params": coordinate_params,
            "status": "usable",
            "raw_result": fresh_raw(coordinate_params, 110.0, 3.4),
        },
        {
            "event": "fresh_model_calibration_replay",
            "calibration_id": "factorial_1",
            "phase": "balanced_fractional_factorial_calibration",
            "parameter": None,
            "params": factorial_params,
            "status": "usable",
            "raw_result": fresh_raw(factorial_params, 120.0, 3.3),
        },
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        run_dir = Path(temp_dir) / "tpcc" / "cpu"
        run_dir.mkdir(parents=True)
        (run_dir / "fresh_baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
        calibration = run_dir / "fresh_model_calibration.jsonl"
        calibration.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        records, audit = extract_replay_records([run_dir])
        document = build_dataset_document(
            records,
            audit,
            inputs=[run_dir],
            workload_filter=None,
            target_filter=None,
        )

    assert len(records) == 2
    assert records[0].parameter_attribution_eligible is True
    assert records[1].parameter_attribution_eligible is False
    group = document["workload_target_scenario_groups"][0]
    assert group["parameter_attribution_sample_count"] == 1
    assert group["vector_only_sample_count"] == 1
    assert group["parameter_direction_statistics"]["a"]["positive_joint_success_count"] == 1
    assert all(
        item["counts_as_real_sample"] is False
        and item["eligible_for_confidence_or_runtime_direction"] is False
        for item in document["semantic_plan_resource_prior_audit"]
    )


def test_pair_result_with_seven_changes_is_vector_only() -> None:
    baseline_params = {f"p{index}": 1.0 for index in range(7)}
    candidate_params = {f"p{index}": 2.0 for index in range(7)}
    pair = {
        "scope": "preflight_only_not_formal",
        "points": {
            "baseline": {
                "params": baseline_params,
                "benchmark": {"xp_qps": 100.0},
                "cpu_utilization": 0.9,
                "resource_avg": {"cpu": 3.6},
            },
            "candidate": {
                "params": candidate_params,
                "benchmark": {"xp_qps": 110.0},
                "cpu_utilization": 0.8,
                "resource_avg": {"cpu": 3.2},
            },
        },
        "comparison": {"baseline_cpu_utilization": 0.9, "candidate_cpu_utilization": 0.8},
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        pair_path = Path(temp_dir) / "tpcc_sensitive" / "pair_result.json"
        pair_path.parent.mkdir()
        pair_path.write_text(json.dumps(pair), encoding="utf-8")
        records, audit = extract_replay_records([pair_path])
        document = build_dataset_document(
            records,
            audit,
            inputs=[pair_path],
            workload_filter=None,
            target_filter=None,
        )

    assert len(records) == 1
    assert records[0].changed_parameter_count == 7
    assert records[0].parameter_attribution_eligible is False
    assert document["workload_target_scenario_groups"][0]["parameter_direction_statistics"] == {}


def test_failed_attempt_tree_is_rejected_fail_closed() -> None:
    baseline_params = {"a": 1.0}
    baseline = {
        "scenario": "cpu",
        "params": baseline_params,
        "raw": fresh_raw(baseline_params, 100.0, 3.6),
    }
    candidate_params = {"a": 2.0}
    row = {
        "event": "fresh_model_calibration_replay",
        "calibration_id": "axis_1",
        "phase": "coordinate_grid_calibration",
        "parameter": "a",
        "params": candidate_params,
        "status": "usable",
        "raw_result": fresh_raw(candidate_params, 110.0, 3.4),
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        run_dir = Path(temp_dir) / "failed_attempts" / "tpcc" / "cpu"
        run_dir.mkdir(parents=True)
        (run_dir / "fresh_baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
        calibration = run_dir / "fresh_model_calibration.jsonl"
        calibration.write_text(json.dumps(row) + "\n", encoding="utf-8")
        records, audit = extract_replay_records([run_dir])
    assert records == []
    assert audit["input_files_rejected_invalidated_or_failed_attempt"] == 1


def test_same_named_calibration_files_have_unique_sample_ids() -> None:
    baseline_params = {"a": 1.0}
    baseline = {
        "scenario": "cpu",
        "params": baseline_params,
        "raw": fresh_raw(baseline_params, 100.0, 3.6),
    }

    def calibration_row(value: float) -> dict:
        params = {"a": value}
        return {
            "event": "fresh_model_calibration_replay",
            "calibration_id": "axis_1",
            "phase": "coordinate_grid_calibration",
            "parameter": "a",
            "params": params,
            "status": "usable",
            "raw_result": fresh_raw(params, 100.0 + value, 3.6 - value / 10.0),
        }

    with tempfile.TemporaryDirectory() as temp_dir:
        roots = []
        for name, value in (("initial", 2.0), ("endpoint", 3.0)):
            run_dir = Path(temp_dir) / name / "tpcc" / "cpu"
            run_dir.mkdir(parents=True)
            (run_dir / "fresh_baseline.json").write_text(
                json.dumps(baseline),
                encoding="utf-8",
            )
            (run_dir / "fresh_model_calibration.jsonl").write_text(
                json.dumps(calibration_row(value)) + "\n",
                encoding="utf-8",
            )
            roots.append(run_dir)
        records, _ = extract_replay_records(roots)

    assert len(records) == 2
    assert len({record.sample_id for record in records}) == 2
    assert all("fresh_model_calibration.jsonl:" in record.sample_id for record in records)


def main() -> None:
    test_extracts_real_stable_replays_and_reverse_probe()
    test_rejects_explicitly_unstable_candidate()
    test_fresh_coordinate_is_attributable_but_factorial_is_vector_only()
    test_pair_result_with_seven_changes_is_vector_only()
    test_failed_attempt_tree_is_rejected_fail_closed()
    test_same_named_calibration_files_have_unique_sample_ids()
    print("replay direction dataset tests passed", flush=True)


if __name__ == "__main__":
    main()
