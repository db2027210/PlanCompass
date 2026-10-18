#!/usr/bin/env python3
"""Bind a fresh JOB restart to the immutable role-aware v3 model bundle."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
OUTER_ROOT = LANE_ROOT / "history/job/outer/attempt_1"
CONFIG_PATH = OUTER_ROOT / "generated_config/formal_job_outer_attempt1/memory/job.json"
BUNDLE_DIR = (
    OUTER_ROOT
    / "job_quality_calibration/job_resource_model_bundle_v3_role_aware"
)
QUALITY_PATH = BUNDLE_DIR / "grouped_lovo_quality.json"
MANIFEST_PATH = BUNDLE_DIR / "bundle_manifest.json"
POLICY_PATH = BUNDLE_DIR / "r2_fallback_policy.json"
FRESH_R0_POINTER = OUTER_ROOT / "fresh_restart_r0/latest.json"
GLOBAL_COUNTER_PATH = OUTER_ROOT / "replay_counter.txt"
SEARCH_REPLAY_COUNTER_PATH = OUTER_ROOT / "formal_search_replay_counter.txt"
SEARCH_ACCEPTED_COUNTER_PATH = OUTER_ROOT / "formal_search_accepted_counter.txt"
STARTUP_BASELINE_COUNTER_PATH = OUTER_ROOT / "formal_startup_baseline_counter.txt"
AUTHORIZATION_PATH = OUTER_ROOT / "job_quality_calibration/v3_outer_authorization.json"
MEMORY_BYTES = 3124756480
EXPECTED_POLICY_ID = "pg5_job_role_aware_r2_fallback_v1"
EXPECTED_GLOBAL_ORDINAL_BEFORE_FRESH_R0 = 13


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def initialize_zero_counter(path: Path) -> None:
    if path.exists() or path.is_symlink():
        if int(path.read_text(encoding="utf-8").strip()) != 0:
            raise RuntimeError(f"refusing to reset nonzero formal counter: {path}")
        return
    with path.open("x", encoding="utf-8") as handle:
        handle.write("0\n")
        handle.flush()
        os.fsync(handle.fileno())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    manifest = read_json(MANIFEST_PATH)
    manifest_files = manifest.get("files") or {}
    if not isinstance(manifest_files, dict) or not manifest_files:
        raise RuntimeError("v3 bundle manifest has no files")
    for name, expected in manifest_files.items():
        path = BUNDLE_DIR / str(name)
        if not path.is_file() or sha256(path) != str(expected):
            raise RuntimeError(f"v3 bundle file hash mismatch: {name}")
    content_sha = hashlib.sha256(
        json.dumps(manifest_files, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    if content_sha != manifest.get("bundle_content_sha256"):
        raise RuntimeError("v3 bundle content SHA mismatch")
    quality = read_json(QUALITY_PATH)
    if quality.get("passed") is not True:
        raise RuntimeError(f"grouped LOVO quality did not pass: {quality}")
    policy = read_json(POLICY_PATH)
    if (
        policy.get("policy_id") != EXPECTED_POLICY_ID
        or policy.get("campaign_id") != "pg5_common_outer_bgallowed_20260722_v1"
        or policy.get("workload") != "job"
        or policy.get("target_resource") != "memory"
    ):
        raise RuntimeError("v3 role-aware policy scope is invalid")
    if manifest.get("r2_fallback_policy_sha256") != sha256(POLICY_PATH):
        raise RuntimeError("manifest does not bind the v3 R2 fallback policy")
    if quality.get("r2_fallback_policy") != policy:
        raise RuntimeError("v3 quality evidence does not embed the bound policy")
    exact_gate = quality.get("exact_parameter_optimizer_quality_gate") or {}
    if (
        exact_gate.get("passed") is not True
        or float((exact_gate.get("thresholds") or {}).get("min_test_r2")) != 0.0
        or (exact_gate.get("thresholds") or {}).get("target_resource") != "memory"
    ):
        raise RuntimeError("v3 quality gate did not preserve min R2=0 and Memory target")
    negative_r2_checks = [
        check
        for check in exact_gate.get("checks") or []
        if check.get("resource") in {"cpu", "io", "memory"}
        and isinstance(check.get("test_r2"), (int, float))
        and float(check.get("test_r2")) < 0.0
    ]
    if {check.get("resource") for check in negative_r2_checks} != {
        "cpu",
        "io",
        "memory",
    } or any(
        ((check.get("r2_fallback") or {}).get("eligible")) is not True
        for check in negative_r2_checks
    ):
        raise RuntimeError("v3 negative-R2 resources were not admitted by the exact fallback")

    fresh_pointer = read_json(FRESH_R0_POINTER)
    if fresh_pointer.get("global_ordinal_before_fresh_r0") != (
        EXPECTED_GLOBAL_ORDINAL_BEFORE_FRESH_R0
    ):
        raise RuntimeError("fresh R0 pointer is not chained from global ordinal 13")
    if (
        fresh_pointer.get("global_replay_ordinal") != 14
        or fresh_pointer.get("global_counter_reset") is not False
        or fresh_pointer.get("formal_search_replay_count") != 0
        or fresh_pointer.get("formal_search_accepted_count") != 0
    ):
        raise RuntimeError("fresh R0 pointer violates separated replay accounting")
    baseline_result = Path(str(fresh_pointer.get("point_result_path") or ""))
    baseline_marker = Path(str(fresh_pointer.get("validity_marker_path") or ""))
    if (
        not baseline_result.is_file()
        or sha256(baseline_result) != fresh_pointer.get("point_result_sha256")
        or not baseline_marker.is_file()
        or sha256(baseline_marker) != fresh_pointer.get("validity_marker_sha256")
    ):
        raise RuntimeError("fresh R0 pointer evidence hash mismatch")
    marker = read_json(baseline_marker)
    if marker.get("reuse_as_restart_baseline") is not True:
        raise RuntimeError("fresh R0 is not authorized as the restart baseline")
    if ((marker.get("profile_gate") or {}).get("passed")) is not True:
        raise RuntimeError("fresh R0 did not pass the requested resource profile")
    cfg = read_json(CONFIG_PATH)
    candidate_target = str(
        (((cfg.get("optimizer") or {}).get("bruteforce") or {}).get(
            "candidate_gate"
        ) or {}).get("target_resource")
        or ""
    ).strip().lower()
    gradient_targets = list(
        (((cfg.get("optimizer") or {}).get("gradient") or {}).get(
            "target_bottleneck_resources"
        ) or [])
    )
    if candidate_target != "memory" or gradient_targets != ["memory"]:
        raise RuntimeError("JOB optimization target changed away from Memory")
    cfg["capability"]["memory"] = MEMORY_BYTES / (1024.0 ** 3)
    cfg["baseline_r0_evidence"] = {
        "accepted_shrink_count_before_method": 0,
        "attempt": fresh_pointer.get("fresh_r0_id"),
        "baseline_gate_decision_path": str(baseline_marker),
        "baseline_gate_decision_sha256": sha256(baseline_marker),
        "outer_allowed": True,
        "point_result_path": str(baseline_result),
        "point_result_sha256": sha256(baseline_result),
        "resource_profile": {
            "cpu_quota_cores": 3.0,
            "memory_bytes": MEMORY_BYTES,
            "io_read_bps": 134217728,
            "io_write_bps": 134217728,
            "clients": 2,
        },
        "profile_gate": marker.get("profile_gate"),
    }
    estimator = (cfg.get("metrics") or {}).get("plan_estimator") or {}
    for resource in ("cpu", "io", "memory"):
        learned = estimator.setdefault(resource, {}).setdefault("learned_model", {})
        learned.update(
            {
                "enabled": True,
                "required": True,
                "model_set": "JOB",
            }
        )
    retraining = estimator.setdefault("model_retraining", {})
    quality_gate = retraining.setdefault("quality_gate", {})
    if float(quality_gate.get("min_test_r2")) != 0.0:
        raise RuntimeError("refusing to relax global min_test_r2")
    quality_gate["target_resource"] = "memory"
    quality_gate["r2_fallback"] = policy
    quality_gate["runtime_selected_model_sets"] = {
        "cpu": "JOB",
        "io": "JOB",
        "memory": "JOB",
    }
    retraining.update(
        {
            "enabled": True,
            "apply_to_runtime": True,
            "pretrained_bundle_dir": str(BUNDLE_DIR),
            "output_dir": str(
                OUTER_ROOT
                / "generated_config/formal_job_outer_attempt1/memory/"
                "job_resource_models_fresh_2980mib"
            ),
            "run_id": "formal_job_outer_attempt1_memory_job_v3_role_aware",
            "require_all_resources": True,
            "strict_retrain_gate": True,
            "fallback_on_error": False,
        }
    )
    global_counter = int(GLOBAL_COUNTER_PATH.read_text(encoding="utf-8").strip())
    if global_counter != int(fresh_pointer["global_replay_ordinal"]):
        raise RuntimeError("global ordinal moved after the sealed fresh R0")
    for counter_path in (
        SEARCH_REPLAY_COUNTER_PATH,
        SEARCH_ACCEPTED_COUNTER_PATH,
        STARTUP_BASELINE_COUNTER_PATH,
    ):
        initialize_zero_counter(counter_path)
    protocol = cfg.setdefault("formal_outer_protocol", {})
    protocol.update(
        {
            "global_replay_counter_path": str(GLOBAL_COUNTER_PATH),
            "global_replay_ordinal_at_binding": global_counter,
            "global_replay_ordinal_before_fresh_r0": (
                EXPECTED_GLOBAL_ORDINAL_BEFORE_FRESH_R0
            ),
            "historical_global_counter_reset": False,
            "initial_real_replay_count": 0,
            "maximum_cumulative_real_replay_count": 10,
            "formal_search_replay_counter_path": str(SEARCH_REPLAY_COUNTER_PATH),
            "formal_search_replay_count_start": 0,
            "formal_search_maximum_replay_count": 10,
            "formal_search_accepted_counter_path": str(SEARCH_ACCEPTED_COUNTER_PATH),
            "formal_search_accepted_count_start": 0,
            "formal_search_accepted_target": 10,
            "formal_startup_baseline_counter_path": str(
                STARTUP_BASELINE_COUNTER_PATH
            ),
            "formal_startup_baseline_count_start": 0,
            "formal_startup_baseline_maximum_count": 1,
            "formal_startup_baseline_excluded_from_candidate_replay_budget": True,
            "formal_search_initial_replay_top_k": 5,
            "formal_search_max_replay_top_k": 10,
            "quality_calibration_and_fresh_r0_excluded_from_formal_search_budget": True,
        }
    )
    cfg["job_quality_calibration"] = {
        "selection_paths": [
            str(OUTER_ROOT / "job_quality_calibration/calibration_selection.json"),
            str(
                OUTER_ROOT
                / "job_quality_calibration/response_span_batch2/"
                "response_span_selection.json"
            ),
        ],
        "dataset_path": str(BUNDLE_DIR / "job_unique_vector_dataset.json"),
        "resource_model_bundle": str(BUNDLE_DIR),
        "bundle_manifest_path": str(MANIFEST_PATH),
        "bundle_manifest_sha256": sha256(MANIFEST_PATH),
        "bundle_content_sha256": content_sha,
        "grouped_lovo_quality_path": str(QUALITY_PATH),
        "grouped_lovo_quality_sha256": sha256(QUALITY_PATH),
        "r2_fallback_policy_path": str(POLICY_PATH),
        "r2_fallback_policy_sha256": sha256(POLICY_PATH),
        "new_cap_profile_id": "job_quality_profile_2980mib_v1",
    }
    write_json(CONFIG_PATH, cfg)
    authorization = {
        "schema_version": 1,
        "authorization_id": "pg5_job_v3_role_aware_outer_authorization_v1",
        "authorized": True,
        "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "workload": "job",
        "target_resource": "memory",
        "min_test_r2": 0.0,
        "bundle_content_sha256": content_sha,
        "bundle_manifest_sha256": sha256(MANIFEST_PATH),
        "quality_sha256": sha256(QUALITY_PATH),
        "policy_sha256": sha256(POLICY_PATH),
        "fresh_r0_pointer_sha256": sha256(FRESH_R0_POINTER),
        "fresh_r0_point_result_sha256": sha256(baseline_result),
        "config_sha256": sha256(CONFIG_PATH),
        "replay_accounting": {
            "global_ordinal": global_counter,
            "global_counter_reset": False,
            "formal_search_replay_count": 0,
            "formal_search_accepted_count": 0,
            "formal_startup_baseline_count": 0,
            "formal_search_maximum_replay_count": 10,
            "formal_search_accepted_target": 10,
        },
    }
    if AUTHORIZATION_PATH.exists() or AUTHORIZATION_PATH.is_symlink():
        raise RuntimeError("refusing to overwrite immutable v3 outer authorization")
    write_json(AUTHORIZATION_PATH, authorization)
    print(
        json.dumps(
            {
                "status": "configured",
                "config": str(CONFIG_PATH),
                "config_sha256": sha256(CONFIG_PATH),
                "memory_gib": cfg["capability"]["memory"],
                "pretrained_bundle_dir": str(BUNDLE_DIR),
                "global_replay_ordinal": global_counter,
                "formal_search_replay_count": 0,
                "formal_search_accepted_count": 0,
                "authorization_path": str(AUTHORIZATION_PATH),
                "authorization_sha256": sha256(AUTHORIZATION_PATH),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
