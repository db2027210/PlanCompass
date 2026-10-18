#!/usr/bin/env python3
"""Prepare a fresh JOB T-SGD attempt with strict baseline resource gating."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "preflight/job_attempt6_source.json"
OUTPUT = ROOT / "preflight/job_attempt6_live_config.json"
OLD = (
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02/"
    "history/job/outer/attempt_5_formal_tsgd_v1"
)
NEW = (
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02/"
    "history/job/outer/attempt_6_live_target_relative_20260723"
)
CONTRACT = (
    ROOT
    / "config/formal/pg5_common_outer_bgallowed_20260722_v1/"
    "common_scoring_contract.json"
)
SEALED_MODEL_SHA256 = {
    "cpu_resource_models.json": (
        "23ccf93ec42b85925db8367957d9066dbe553129c69a5c60a3c2185981e7f306"
    ),
    "io_resource_models.json": (
        "4706567aeffd8e3d80935d1169d2c2807f38c31bfa615e07e0e0216655224257"
    ),
    "memory_resource_models.json": (
        "85188f9c936b3d989adab751e36bd3d8237a104d082199e2dedd05fb1812b23c"
    ),
    "resource_model_retrain_summary.json": (
        "63553471142b82bb1f6206618ac59d30eeef13ab41c0df5d6af0841178fe3dfa"
    ),
}


def replace(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: replace(item) for key, item in value.items()}
    if isinstance(value, list):
        return [replace(item) for item in value]
    if isinstance(value, str):
        return value.replace(OLD, NEW)
    return value


def main() -> int:
    data = replace(json.loads(SOURCE.read_text(encoding="utf-8")))
    plan_estimator = data.setdefault("metrics", {}).setdefault(
        "plan_estimator",
        {},
    )
    # The sealed learned artifacts are retained as audited uncertainty evidence,
    # while candidate direction comes from executor-physics deltas anchored at
    # the reused real baseline.  This also gives the direction-validity gate a
    # concrete target anchor instead of relying on retraining-only metadata.
    plan_estimator["physical_model"] = {
        "enabled": True,
        "error_bounds_calibrated": False,
        "capture_first_point_as_baseline": True,
        "require_complete_explain": True,
        "seq_miss_rate": 0.15,
        "random_miss_rate": 0.7,
        "delta_scale": {"memory": 1.0},
        "min_direction_relative_delta": {
            "cpu": 0.005,
            "memory": 0.01,
            "io": 0.01,
        },
    }
    plan_estimator["direction_validity_gate"] = {
        "enabled": True,
        "target_resource": "memory",
        "require_baseline_sanity": True,
        "require_calibrated_anchor": True,
        "require_baseline_correction_success": True,
        "reject_online_correction_saturation": True,
        "max_baseline_relative_error": 0.5,
        "threshold_epsilon": 1e-6,
    }
    data["scoring"] = {
        "overload_threshold": 0.8,
        "score_mode": "band_target",
        "resource_weights": {"cpu": 1, "memory": 1, "io": 3},
        "target_relative_resource_target_ranges": {
            "target_bottleneck": {
                "min_pct": 85,
                "max_pct": 90,
                "hard_overload_pct": 95,
            },
            "non_bottleneck": {
                "min_pct": 30,
                "max_pct": 50,
                "hard_overload_pct": 90,
            },
        },
    }
    data["baseline_resource_band_gate"] = {
        "enabled": True,
        "hard_fail": True,
        "stage": "after_stable_baseline_before_retrain_and_candidate_search",
        "target_resource": "memory",
        "ranges_pct": {
            "cpu": {"min_pct": 30, "max_pct": 50},
            "memory": {"min_pct": 85, "max_pct": 90},
            "io": {"min_pct": 30, "max_pct": 50},
        },
        "threshold_semantics": (
            "target inclusive 85-90%; non-target inclusive 30-50%; "
            "hard fail before candidate generation and real replay"
        ),
        "does_not_affect_outer_acceptance": True,
    }
    gradient = data.setdefault("optimizer", {}).setdefault("gradient", {})
    for key in (
        "target_bottleneck_flat_band_decrease_pressure",
        "target_score_non_decrease_acceptance_enabled",
        "min_predicted_target_util_decrease",
        "min_predicted_non_target_util_increase",
    ):
        gradient.pop(key, None)
    digest = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    remote_contract = f"{NEW}/code/config/formal/common_scoring_contract.json"
    data["scoring_contract"] = {"path": remote_contract, "sha256": digest}
    data["_common_scoring_contract"] = {
        "path": remote_contract,
        "sha256": digest,
        "config_scoring_equals_contract": True,
    }
    data["_job_attempt6_sealed_model_deployment"] = {
        "source": (
            "/mnt/nvme2n1/anonymous/resource_adaptive/"
            "pg5_common_outer_bgallowed_20260722_v1/job_node02/"
            "history/job/outer/attempt_5_formal_tsgd_v1/"
            "sealed_resource_models_p31_v1"
        ),
        "destination": f"{NEW}/sealed_resource_models_p31_v1",
        "sha256": SEALED_MODEL_SHA256,
        "launch_policy": "verify_destination_or_copy_verified_source_then_verify",
    }
    outer = data["formal_outer_protocol"]
    outer.update(
        {
            "formal_search_accepted_count_start": 0,
            "formal_search_replay_count_start": 1,
            "formal_search_accepted_target": 10,
            "accepted_shrink_target": 10,
            "global_replay_ordinal_at_binding": 0,
            "valid_initial_batch_count_start": 0,
        }
    )
    for key in list(outer):
        if "resume_checkpoint" in key or key.startswith("resume_"):
            outer.pop(key)
    data.pop("_attempt4_contract", None)
    OUTPUT.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
