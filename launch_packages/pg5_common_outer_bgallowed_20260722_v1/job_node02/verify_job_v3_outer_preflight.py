#!/usr/bin/env python3
"""Fail-closed preflight for the JOB v3 role-aware formal outer search."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
OUTER_ROOT = LANE_ROOT / "history/job/outer/attempt_1"
CONFIG_PATH = OUTER_ROOT / "generated_config/formal_job_outer_attempt1/memory/job.json"
AUTHORIZATION_PATH = OUTER_ROOT / "job_quality_calibration/v3_outer_authorization.json"
RESTART_AUTHORIZATION_PATH = (
    OUTER_ROOT
    / "job_quality_calibration/v3_outer_restart_authorization_after_startup.json"
)
GLOBAL_COUNTER_PATH = OUTER_ROOT / "replay_counter.txt"
REPLAY_COUNTER_PATH = OUTER_ROOT / "formal_search_replay_counter.txt"
ACCEPTED_COUNTER_PATH = OUTER_ROOT / "formal_search_accepted_counter.txt"
STARTUP_COUNTER_PATH = OUTER_ROOT / "formal_startup_baseline_counter.txt"


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    restart_mode = RESTART_AUTHORIZATION_PATH.is_file()
    authorization_path = (
        RESTART_AUTHORIZATION_PATH if restart_mode else AUTHORIZATION_PATH
    )
    authorization = read_json(authorization_path)
    config = read_json(CONFIG_PATH)
    if authorization.get("authorized") is not True:
        raise RuntimeError("v3 outer authorization is not true")
    if authorization.get("config_sha256") != sha256(CONFIG_PATH):
        raise RuntimeError("configured outer search changed after authorization")
    if authorization.get("target_resource") != "memory":
        raise RuntimeError("authorization target changed away from Memory")
    if float(authorization.get("min_test_r2")) != 0.0:
        raise RuntimeError("authorization relaxed the global min_test_r2")

    protocol = config.get("formal_outer_protocol") or {}
    accounting = authorization.get("replay_accounting") or {}
    global_ordinal = int(GLOBAL_COUNTER_PATH.read_text(encoding="utf-8").strip())
    replay_count = int(REPLAY_COUNTER_PATH.read_text(encoding="utf-8").strip())
    accepted_count = int(ACCEPTED_COUNTER_PATH.read_text(encoding="utf-8").strip())
    startup_count = int(STARTUP_COUNTER_PATH.read_text(encoding="utf-8").strip())
    authorized_global = (
        int(accounting.get("global"))
        if restart_mode
        else int(accounting.get("global_ordinal"))
    )
    if global_ordinal != authorized_global:
        raise RuntimeError("global ordinal differs from the authorized R0 binding")
    expected_startup_count = 1 if restart_mode else 0
    if (
        replay_count != 0
        or accepted_count != 0
        or startup_count != expected_startup_count
    ):
        raise RuntimeError("formal-search budgets no longer start at zero")
    expected_protocol = {
        "method_id": "experience_table_all_subset_sgd",
        "all_nonempty_subset_count": 127,
        "subset_parallel_workers": 8,
        "maximum_non_default_real_candidates": 10,
        "fresh_template_before_every_real_point": True,
        "historical_global_counter_reset": False,
        "initial_real_replay_count": 0,
        "maximum_cumulative_real_replay_count": 10,
        "formal_search_replay_count_start": 0,
        "formal_search_maximum_replay_count": 10,
        "formal_search_accepted_count_start": 0,
        "formal_search_accepted_target": 10,
        "formal_startup_baseline_count_start": 0,
        "formal_startup_baseline_maximum_count": 1,
        "formal_startup_baseline_excluded_from_candidate_replay_budget": True,
        "formal_search_initial_replay_top_k": 5,
        "formal_search_max_replay_top_k": 10,
        "quality_calibration_and_fresh_r0_excluded_from_formal_search_budget": True,
    }
    mismatches = {
        key: {"actual": protocol.get(key), "expected": expected}
        for key, expected in expected_protocol.items()
        if protocol.get(key) != expected
    }
    if mismatches:
        raise RuntimeError(f"formal protocol mismatch: {mismatches}")

    target = (
        (((config.get("optimizer") or {}).get("bruteforce") or {}).get(
            "candidate_gate"
        ) or {}).get("target_resource")
    )
    gradient_targets = (
        ((config.get("optimizer") or {}).get("gradient") or {}).get(
            "target_bottleneck_resources"
        )
    )
    active_params = (
        ((config.get("optimizer") or {}).get("gradient") or {}).get("active_params")
        or []
    )
    quality_gate = (
        (((config.get("metrics") or {}).get("plan_estimator") or {}).get(
            "model_retraining"
        ) or {}).get("quality_gate")
        or {}
    )
    if (
        (config.get("optimizer") or {}).get("mode") != "gradient"
        or target != "memory"
        or gradient_targets != ["memory"]
        or len(active_params) != 7
        or len(set(active_params)) != 7
    ):
        raise RuntimeError("JOB optimization target changed away from Memory")
    if float(quality_gate.get("min_test_r2")) != 0.0:
        raise RuntimeError("global min_test_r2 changed away from zero")
    fallback = quality_gate.get("r2_fallback") or {}
    if (
        quality_gate.get("target_resource") != "memory"
        or fallback.get("policy_id") != "pg5_job_role_aware_r2_fallback_v1"
        or fallback.get("target_resource") != "memory"
    ):
        raise RuntimeError("campaign-scoped role-aware fallback is not bound exactly")
    calibration = config.get("job_quality_calibration") or {}
    for field in (
        "bundle_manifest_path",
        "grouped_lovo_quality_path",
        "r2_fallback_policy_path",
    ):
        path = Path(str(calibration.get(field) or ""))
        expected_sha = calibration.get(field.replace("_path", "_sha256"))
        if not path.is_file() or sha256(path) != expected_sha:
            raise RuntimeError(f"authorized v3 artifact changed: {field}")
    print(
        json.dumps(
            {
                "status": "ready",
                "target_resource": target,
                "min_test_r2": quality_gate.get("min_test_r2"),
                "global_replay_ordinal": global_ordinal,
                "formal_search_replay_count": replay_count,
                "formal_search_accepted_count": accepted_count,
                "formal_startup_baseline_count": startup_count,
                "restart_mode": restart_mode,
                "authorization_path": str(authorization_path),
                "initial_replay_top_k": 5,
                "maximum_replay_top_k": 10,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
