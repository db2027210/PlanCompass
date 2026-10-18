#!/usr/bin/env python3
"""Regression tests for formal Algorithm C replay-anchor export and loading."""

from __future__ import annotations

import copy
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHELL_ROOT = PROJECT_ROOT / "shell"
for import_root in (PROJECT_ROOT, SHELL_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from build_algorithm_c_experience_table_from_replays import (  # noqa: E402
    RESOURCE_NAMES,
    build_artifact,
    load_samples,
)
from export_algorithm_c_formal_replay_anchors import (  # noqa: E402
    ANCHOR_CONTRACT,
    export_anchors,
)


def valid_validation(
    *,
    label: str,
    worker: str,
    parameter: float,
    resources: Dict[str, float],
    qps: float,
    raw_plan_scale: float,
    technical_failure: bool = False,
) -> Dict[str, Any]:
    calibration = {
        resource: {
            "raw_plan_log_ratio": raw_plan_scale * multiplier,
            # This deliberately differs from the raw value.  The exporter
            # must never feed an already calibrated prediction back into T.
            "predicted_log_ratio": 90.0 + multiplier,
        }
        for resource, multiplier in zip(RESOURCE_NAMES, (1.0, 2.0, 3.0))
    }
    return {
        "event": "candidate_validation",
        "label": label,
        "worker_key": worker,
        "status": "failed" if technical_failure else "ok",
        "technical_failure": technical_failure,
        "valid_replay_count": 0 if technical_failure else 1,
        "resource_stats": {"resource_monitor_ok": not technical_failure},
        "workload_completeness": {"passed": not technical_failure},
        "executor_telemetry": {
            "available": not technical_failure,
            "source": "synthetic_executor",
            "metrics": {"rows": 100.0},
            "derived": {"temp_bytes": 0.0},
        },
        "params": {"p": parameter},
        "resources": dict(resources),
        "db_qps": qps,
        "candidate_ranking": {
            "workload_plan_fingerprint": f"workload-sha256:{label}",
            "final_estimation_metadata": {
                "baseline_params": {"p": 1.0},
                "baseline_real": {"cpu": 5.0, "memory": 10.0, "io": 15.0},
                "resource_prediction_calibration_audit": calibration,
                "workload_plan_fingerprint": f"workload-sha256:{label}",
                "plan_fingerprint_coverage": {"complete": True},
            },
        },
    }


def authoritative_state() -> Dict[str, Any]:
    return {
        "state_version": 61,
        "technical_failures": 1,
        "baseline_by_worker": {
            "w1": {
                "label": "baseline_w1_aggregate",
                "resources": {"cpu": 10.0, "memory": 20.0, "io": 30.0},
                "db_qps": 100.0,
            },
            "w2": {
                "label": "baseline_w2_aggregate",
                "resources": {"cpu": 20.0, "memory": 40.0, "io": 60.0},
                "db_qps": 200.0,
            },
        },
        "canonical_baseline": {
            "label": "canonical_baseline",
            "resources": {"cpu": 5.0, "memory": 10.0, "io": 15.0},
            "db_qps": 50.0,
        },
    }


def replay_event(round_index: int, validation: Dict[str, Any], *, batch_kind: str = "base_top5") -> Dict[str, Any]:
    return {
        "event": "algorithm_c_candidate_replay",
        "round_index": round_index,
        "batch_kind": batch_kind,
        "validation": validation,
    }


def batch_event(round_index: int, candidate_count: int, *, batch_kind: str = "base_top5") -> Dict[str, Any]:
    return {
        "event": "algorithm_c_physical_batch_start",
        "round_index": round_index,
        "batch_kind": batch_kind,
        "candidate_count": candidate_count,
    }


def test_export_keeps_only_complete_valid_base_top5_rounds() -> None:
    complete_w1 = valid_validation(
        label="r1_c1_w1",
        worker="w1",
        parameter=1.10,
        resources={"cpu": 12.0, "memory": 18.0, "io": 33.0},
        qps=110.0,
        raw_plan_scale=0.01,
    )
    complete_w2 = valid_validation(
        label="r1_c2_w2",
        worker="w2",
        parameter=1.20,
        resources={"cpu": 36.0, "memory": 44.0, "io": 54.0},
        qps=180.0,
        raw_plan_scale=0.02,
    )
    partial = valid_validation(
        label="r2_partial_w1",
        worker="w1",
        parameter=1.30,
        resources={"cpu": 11.0, "memory": 21.0, "io": 31.0},
        qps=105.0,
        raw_plan_scale=0.03,
    )
    invalid = valid_validation(
        label="r3_technical_failure",
        worker="w2",
        parameter=1.40,
        resources={"cpu": 20.0, "memory": 40.0, "io": 60.0},
        qps=200.0,
        raw_plan_scale=0.04,
        technical_failure=True,
    )
    cached = {
        "event": "algorithm_c_cached_valid_replay_redecision_complete",
        "round_index": 1,
        "batch_kind": "base_top5",
        "validation": copy.deepcopy(complete_w1),
    }
    extension = valid_validation(
        label="r4_extension_w1",
        worker="w1",
        parameter=1.50,
        resources={"cpu": 15.0, "memory": 25.0, "io": 35.0},
        qps=120.0,
        raw_plan_scale=0.05,
    )
    events = [
        batch_event(1, 2),
        replay_event(1, complete_w1),
        cached,
        replay_event(1, complete_w2),
        batch_event(2, 2),
        replay_event(2, partial),
        batch_event(3, 1),
        replay_event(3, invalid),
        batch_event(4, 1, batch_kind="plan_tree_extension"),
        replay_event(4, extension, batch_kind="plan_tree_extension"),
    ]

    rows, manifest = export_anchors(
        state=authoritative_state(),
        events=events,
        round_offset=1_000,
    )

    assert [row["label"] for row in rows] == ["r1_c1_w1", "r1_c2_w2"]
    assert all(row["event"] == "candidate_validation" for row in rows)
    assert all(row["round_index"] == 1_001 for row in rows)
    assert all(row["formal_anchor"]["holdout_group"] == "formal_round_000001" for row in rows)
    assert manifest["accepted_complete_rounds"] == [1]
    assert manifest["anchor_count"] == 2
    assert manifest["cached_redecisions_exported"] == 0
    assert manifest["qps_used_for_resource_prediction"] is False
    assert {item["round_index"]: item["reason"] for item in manifest["excluded_rounds"]} == {
        2: "partial_round",
        3: "round_contains_invalid_physical_replay",
    }


def test_same_worker_normalization_and_raw_plan_ratio_survive_loading() -> None:
    validation = valid_validation(
        label="normalized_w1",
        worker="w1",
        parameter=1.10,
        resources={"cpu": 12.0, "memory": 18.0, "io": 33.0},
        qps=110.0,
        raw_plan_scale=0.01,
    )
    rows, _ = export_anchors(
        state=authoritative_state(),
        events=[batch_event(1, 1), replay_event(1, validation)],
        round_offset=1_000,
    )
    anchor = rows[0]["formal_anchor"]
    expected_resources = {"cpu": 6.0, "memory": 9.0, "io": 16.5}
    for resource, expected in expected_resources.items():
        assert math.isclose(anchor["normalized_actual_resources"][resource], expected)
    assert math.isclose(anchor["normalized_actual_db_qps"], 55.0)
    assert math.isclose(anchor["outcomes"]["cpu"], math.log(6.0 / 5.0))
    assert math.isclose(anchor["outcomes"]["memory"], math.log(9.0 / 10.0))
    assert math.isclose(anchor["outcomes"]["io"], math.log(16.5 / 15.0))
    assert math.isclose(anchor["outcomes"]["qps_outer_only"], math.log(1.10))
    assert anchor["raw_plan_log_ratios"] == {"cpu": 0.01, "memory": 0.02, "io": 0.03}
    assert anchor["old_calibrated_prediction_used_as_input"] is False

    with tempfile.TemporaryDirectory(prefix="algorithm-c-formal-anchor-") as temp_dir:
        path = Path(temp_dir) / "anchors.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        samples, audit = load_samples(
            [path],
            ["p"],
            source_algorithms={str(path): "algorithm_c"},
        )

    assert len(samples) == 1
    assert audit["technical_failures_excluded"] is True
    sample = samples[0]
    assert math.isclose(sample["x"][0], math.log(1.10))
    assert sample["plan_prediction"]["predicted_log_ratios"] == {
        "cpu": 0.01,
        "memory": 0.02,
        "io": 0.03,
    }
    assert sample["plan_prediction"]["actual_results_used"] is False
    assert sample["plan_prediction"]["qps_used"] is False


def formal_sample(index: int, group: str, round_index: int, *, qps_scale: float = 1.0) -> Dict[str, Any]:
    x_value = -0.20 + 0.025 * index
    parameter = math.exp(x_value)
    outcomes = {
        "cpu": 0.40 * x_value,
        "memory": 0.60 * x_value,
        "io": -0.25 * x_value,
        "qps_outer_only": qps_scale * (0.015 * index - 0.05),
    }
    anchor = {
        "contract": ANCHOR_CONTRACT,
        "holdout_group": group,
        "parameter_log_deltas": {"p": x_value},
        "outcomes": outcomes,
        "raw_plan_log_ratios": {
            "cpu": 0.39 * x_value,
            "memory": 0.59 * x_value,
            "io": -0.24 * x_value,
        },
        "workload_plan_fingerprint": f"workload-sha256:{group}:{index}",
    }
    return {
        "source_path": "synthetic_formal_anchor",
        "worker": "w1" if index % 2 else "w2",
        "round_index": round_index,
        "holdout_group": group,
        "label": f"candidate_{index:02d}",
        "params": {"p": parameter},
        "x": [x_value],
        "outcomes": outcomes,
        "executor_telemetry": {"source": "synthetic_executor"},
        "plan_prediction": {
            "predicted_log_ratios": dict(anchor["raw_plan_log_ratios"]),
            "actual_results_used": False,
            "qps_used": False,
        },
        "formal_anchor": anchor,
    }


def build(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    return build_artifact(
        samples=samples,
        active_params=["p"],
        benchmark="job",
        target_resource="memory",
        workload_contract={"scene": "job_memory", "synthetic_test": True},
        source_audit={"synthetic": True},
        alpha=0.05,
        scored_resources=RESOURCE_NAMES,
    )


def test_qps_is_outer_only_and_does_not_change_resource_prediction() -> None:
    samples = [
        formal_sample(index, f"formal_round_{(index - 1) // 5 + 1:06d}", (index - 1) // 5 + 1)
        for index in range(1, 81)
    ]
    changed_qps = copy.deepcopy(samples)
    for index, sample in enumerate(changed_qps, start=1):
        sample["outcomes"]["qps_outer_only"] = 50.0 - 3.0 * index
        sample["formal_anchor"]["outcomes"]["qps_outer_only"] = 50.0 - 3.0 * index

    original = build(samples)
    changed = build(changed_qps)

    assert original["table"] == changed["table"]
    assert (
        original["resource_prediction_calibration"]
        == changed["resource_prediction_calibration"]
    )
    for artifact in (original, changed):
        assert artifact["qps_used_for_inner_scoring"] is False
        assert artifact["resource_prediction_calibration"]["qps_used"] is False
        assert all(
            model["qps_used"] is False
            for model in artifact["resource_prediction_calibration"]["resources"].values()
        )
    assert original == changed
    assert set(original["holdout"]["metrics"]) == set(RESOURCE_NAMES)
    assert original["training_boundary"][
        "qps_present_in_resource_training_rows"
    ] is False


def test_formal_holdout_group_is_never_split_across_train_and_holdout() -> None:
    samples: List[Dict[str, Any]] = []
    for index in range(1, 31):
        samples.append(formal_sample(index, "formal_round_000001", 1))
    for index in range(31, 61):
        samples.append(formal_sample(index, "formal_round_000002", 2))
    # A damaged/inconsistent numeric round tag must not split the authoritative
    # physical holdout group.  Either select all thirty members or fail closed.
    for index in range(61, 91):
        numeric_round = 2 if index < 75 else 3
        samples.append(formal_sample(index, "formal_round_000003", numeric_round))

    artifact = build(samples)
    holdout_labels = set(artifact["fit"]["holdout_labels"])
    expected_group_labels = {f"candidate_{index:02d}" for index in range(61, 91)}
    assert holdout_labels == expected_group_labels
    assert artifact["fit"]["holdout_count"] == 30


def main() -> int:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}", flush=True)
    print(f"PASS {len(tests)} formal replay anchor tests", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
