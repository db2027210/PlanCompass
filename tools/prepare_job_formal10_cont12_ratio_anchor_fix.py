#!/usr/bin/env python3
"""Prepare JOB continuation 12 with a fresh, current-run ratio anchor.

This is a preparation-only tool.  It does not launch PostgreSQL or a workload.
The generated continuation reuses the already-accounted point 214 baseline,
removes stale partial-round resume bindings, and requires the current process
to rebuild estimator evidence from the sealed SQL snapshot.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


BASE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
)
LANE = BASE.parents[3]
PREVIOUS = (
    BASE
    / "formal10_after_round10_gate_cont11_reverse_override_v1_20260729T1921"
)
OUTPUT = (
    BASE
    / "formal10_after_round10_gate_cont12_ratio_anchor_fix_v1_20260729T1950"
)
OPTIMIZER = LANE / "workspace_attempt7/parameter_optimizer.py"
ACCEPTED = BASE / "formal10_accepted_counter.txt"
REPLAY = BASE / "formal_search_replay_counter.txt"
GLOBAL = BASE / "replay_counter.txt"
POINT214 = BASE / "formal_points/point_0214/point_result.json"
POINT215 = BASE / "formal_points/point_0215/point_result.json"
BASELINE_READBACK = PREVIOUS / "rejected_baseline_restore_readback.txt"
SEALED_SQL = BASE / "sealed_startup_baseline_sql_job8_literal_v2.json"

EXPECTED_SHA256 = {
    OPTIMIZER:
        "c54b9dba238bb03c5333c0dbbb780299f0ecaf09de2d8de3c590ba693c1c0ed2",
    POINT214:
        "8e98ee2e7340511a113ff0f72b31fcafa9c957667ad1879b0066bdd55e330d57",
    POINT215:
        "efbe78a8d6c3fe56a7031303c268fc081f261c1fd0509516f89d30db895cbf0a",
    BASELINE_READBACK:
        "a62d0e5450185391d221362a79eee35223e8700f9cb7438b13ba2b51c738e532",
    SEALED_SQL:
        "842413fda8fa783bc8750f6b438a6b6d2aa41d7adef629cf7becbba9f38ab7fb",
}
EXPECTED_COUNTERS = {
    "accepted": 0,
    "formal_replay": 156,
    "global_ordinal": 215,
}
BASELINE_PARAMS = {
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 7000.0,
    "parallel_tuple_cost": 0.001,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}
REQUIRED_DIRECTION_FAILURES = {
    "target_baseline_sanity_missing_or_disabled",
    "target_baseline_relative_error_missing_or_nonfinite",
    "baseline_correction_failed",
    "target_anchor_calibration_missing_or_invalid",
}
CONT12_RETRAIN_RUN_ID = "job_cont12_ratio_anchor_fix_20260729T1950"
POINT215_FINGERPRINT = (
    "three-level-sha256:41f2a85edf7d6a2fcb03ee0b286c224d"
    "f2d4e1680fb16cbfd61fd291f13156a8"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_int(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=True,
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def normalized_params(value: Mapping[str, Any]) -> dict[str, float]:
    result = {str(name): float(item) for name, item in value.items()}
    if (
        set(result) != set(BASELINE_PARAMS)
        or not all(math.isfinite(item) for item in result.values())
    ):
        raise RuntimeError("invalid JOB cost-parameter signature")
    return result


def recursive_remove_resume_checkpoint_bindings(
    value: Any,
    *,
    prefix: str = "",
) -> list[dict[str, Any]]:
    """Remove every inherited resume-checkpoint path/SHA binding."""

    removed: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key in list(value):
            normalized_key = str(key).strip().lower()
            dotted = f"{prefix}.{key}" if prefix else str(key)
            normalized_dotted = dotted.strip().lower()
            if (
                "resume_checkpoint" in normalized_dotted
                and (
                    normalized_key == "path"
                    or normalized_key == "sha"
                    or normalized_key == "sha256"
                    or normalized_key.endswith("_path")
                    or normalized_key.endswith("_sha")
                    or normalized_key.endswith("_sha256")
                )
            ):
                removed.append({
                    "key": dotted,
                    "value": copy.deepcopy(value.pop(key)),
                })
                continue
            removed.extend(
                recursive_remove_resume_checkpoint_bindings(
                    value[key],
                    prefix=dotted,
                )
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            removed.extend(
                recursive_remove_resume_checkpoint_bindings(
                    item,
                    prefix=f"{prefix}[{index}]",
                )
            )
    return removed


def find_resume_checkpoint_bindings(
    value: Any,
    *,
    prefix: str = "",
) -> list[str]:
    remaining: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            dotted = f"{prefix}.{key}" if prefix else str(key)
            normalized_dotted = dotted.strip().lower()
            if (
                "resume_checkpoint" in normalized_dotted
                and (
                    normalized_key == "path"
                    or normalized_key == "sha"
                    or normalized_key == "sha256"
                    or normalized_key.endswith("_path")
                    or normalized_key.endswith("_sha")
                    or normalized_key.endswith("_sha256")
                )
            ):
                remaining.append(dotted)
            remaining.extend(
                find_resume_checkpoint_bindings(item, prefix=dotted)
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            remaining.extend(
                find_resume_checkpoint_bindings(
                    item,
                    prefix=f"{prefix}[{index}]",
                )
            )
    return remaining


def history_files() -> list[Path]:
    return sorted(
        path
        for path in PREVIOUS.glob("optimizer_history*.jsonl")
        if path.is_file() and path.stat().st_size > 0
    )


def audit_previous_failure() -> dict[str, Any]:
    paths = history_files()
    if len(paths) != 1:
        raise RuntimeError(
            f"expected one nonempty cont11 optimizer history, found: {paths}"
        )
    direction_events: list[dict[str, Any]] = []
    observed_events: list[str] = []
    for line_number, line in enumerate(
        paths[0].read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"invalid cont11 history JSON at line {line_number}"
            ) from exc
        if not isinstance(record, dict):
            raise RuntimeError(
                f"invalid cont11 history record at line {line_number}"
            )
        event = str(record.get("event") or "")
        if event:
            observed_events.append(event)
        if event == "estimator_direction_invalid":
            direction_events.append(record)

    if len(direction_events) != 1:
        raise RuntimeError(
            "cont11 must contain exactly one estimator_direction_invalid event"
        )
    reasons = {
        str(reason)
        for summary in (direction_events[0].get("summaries") or [])
        if isinstance(summary, dict)
        for reason in (summary.get("reasons") or [])
    }
    if not REQUIRED_DIRECTION_FAILURES.issubset(reasons):
        raise RuntimeError(
            "cont11 estimator failure signature changed: "
            f"{sorted(reasons)}"
        )
    forbidden_event_fragments = (
        "formal_candidate_reserved",
        "final_feedback_real_replay",
        "candidate_real_replay_started",
    )
    forbidden = sorted({
        event
        for event in observed_events
        if any(fragment in event for fragment in forbidden_event_fragments)
    })
    if forbidden:
        raise RuntimeError(
            f"cont11 unexpectedly reached a real replay event: {forbidden}"
        )
    return {
        "history_path": str(paths[0]),
        "history_sha256": sha256(paths[0]),
        "direction_failure_reasons": sorted(reasons),
        "event_count": len(observed_events),
    }


def validate_point214() -> dict[str, Any]:
    point = load_json(POINT214)
    measurement = point.get("measurement") or {}
    if (
        point.get("status") != "complete"
        or point.get("point_id")
        != "job_experience_table_outer_attempt1_0214"
        or normalized_params(point.get("cost_parameters") or {})
        != BASELINE_PARAMS
        or measurement.get("correctness_passed") is not True
        or (measurement.get("resource_stability") or {}).get("passed")
        is not True
        or point.get("oom_killed") is not False
        or int(point.get("database_restart_count") or 0) != 0
    ):
        raise RuntimeError("point214 is not reusable exact-baseline evidence")
    return point


def validate_previous_config_binding() -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = PREVIOUS / "continuation_manifest.json"
    config_path = PREVIOUS / "config.json"
    manifest = load_json(manifest_path)
    expected_config_sha = str(manifest.get("output_config_sha256") or "")
    actual_config_sha = sha256(config_path)
    if actual_config_sha != expected_config_sha:
        raise RuntimeError(
            "cont11 config/manifest SHA mismatch: "
            f"{actual_config_sha} != {expected_config_sha}"
        )
    return load_json(config_path), {
        "config_path": str(config_path),
        "config_sha256": actual_config_sha,
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
    }


def validate_reverse_calibration(
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    gradient = (
        ((config.get("optimizer") or {}).get("gradient") or {})
        if isinstance(config.get("optimizer"), dict)
        else {}
    )
    binding = gradient.get("replay_direction_calibration") or {}
    artifact_path = Path(str(binding.get("artifact_path") or ""))
    artifact_sha = str(binding.get("artifact_sha256") or "")
    if (
        binding.get("enabled") is not True
        or not artifact_path.is_file()
        or sha256(artifact_path) != artifact_sha
    ):
        raise RuntimeError("cont11 reverse calibration binding is invalid")
    calibration = load_json(artifact_path)
    decisions = calibration.get("same_incumbent_scope_decisions") or []
    if (
        calibration.get("artifact_type")
        != "pg_real_replay_direction_training_dataset_v1"
        or calibration.get("qps_used_for_inner_scoring") is not False
        or len(decisions) != 1
        or decisions[0].get("scope_id")
        != "job-cont11-reverse-after-p209-p215"
        or decisions[0].get("target_resource") != "memory"
    ):
        raise RuntimeError("cont11 reverse calibration contract changed")
    runtime_policy = (
        ((decisions[0].get("decision") or {}).get("runtime_policy") or {})
    )
    if (
        runtime_policy.get("action") != "reverse_probe"
        or runtime_policy.get("probe_only") is not True
        or runtime_policy.get("outer_acceptance_unchanged") is not True
        or runtime_policy.get("outer_shrink_forbidden_until_real_acceptance")
        is not True
        or float(runtime_policy.get("eta_multiplier") or 0.0) != 0.25
    ):
        raise RuntimeError("cont11 reverse-probe runtime policy changed")
    return calibration, artifact_sha


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    for path, expected in EXPECTED_SHA256.items():
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"SHA mismatch: {path}: {actual} != {expected}"
            )
    if not PREVIOUS.is_dir():
        raise FileNotFoundError(f"cont11 output is missing: {PREVIOUS}")
    exit_code_path = PREVIOUS / "driver_exit_code.txt"
    previous_exit_code = int(exit_code_path.read_text(encoding="utf-8").strip())
    if previous_exit_code == 0:
        raise RuntimeError(
            "cont11 unexpectedly exited successfully; refusing failure repair"
        )
    if (BASE / "formal_points/point_0216").exists():
        raise RuntimeError("point216 exists; cont11 zero-call assumption is false")

    counters = {
        "accepted": read_int(ACCEPTED),
        "formal_replay": read_int(REPLAY),
        "global_ordinal": read_int(GLOBAL),
    }
    if counters != EXPECTED_COUNTERS:
        raise RuntimeError(
            f"counter mismatch: {counters} != {EXPECTED_COUNTERS}"
        )
    point214 = validate_point214()
    previous_config, previous_binding = validate_previous_config_binding()
    failure_audit = audit_previous_failure()
    calibration, source_calibration_sha = validate_reverse_calibration(
        previous_config
    )
    if normalized_params(previous_config.get("initial_params") or {}) != BASELINE_PARAMS:
        raise RuntimeError("cont11 initial parameters are not the JOB baseline")

    config = copy.deepcopy(previous_config)
    removed_resume_bindings = recursive_remove_resume_checkpoint_bindings(config)
    remaining_resume_bindings = find_resume_checkpoint_bindings(config)
    if remaining_resume_bindings:
        raise RuntimeError(
            "resume checkpoint bindings remain after cleanup: "
            f"{remaining_resume_bindings}"
        )

    protocol = config["formal_outer_protocol"]
    protocol.update({
        "formal_search_accepted_count_start": 0,
        "formal_search_accepted_counter_path": str(ACCEPTED),
        "formal_search_replay_count_start": 156,
        "initial_real_replay_count": 156,
        "formal_search_replay_counter_path": str(REPLAY),
        "global_replay_ordinal_at_binding": 215,
        "global_replay_counter_path": str(GLOBAL),
        "formal_startup_baseline_count_start": 1,
        "formal_startup_baseline_counter_path": str(
            OUTPUT / "state/formal_startup_baseline_counter.txt"
        ),
        "formal_search_attempt_safety_cap": 161,
        "formal_search_maximum_replay_count": 161,
        "maximum_cumulative_real_replay_count": 161,
        "maximum_non_default_real_candidates": 5,
        "maximum_resource_qualified_real_replays_per_batch": 5,
        "global_real_replay_top_k_hard_cap": 5,
    })
    excluded_fingerprints = {
        str(item)
        for item in (
            protocol.get("excluded_prior_three_level_fingerprints") or []
        )
    }
    if POINT215_FINGERPRINT not in excluded_fingerprints:
        raise RuntimeError("point215 fingerprint is not quarantined")

    OUTPUT.mkdir(parents=True)
    atomic_text(OUTPUT / "state/formal_startup_baseline_counter.txt", "1\n")
    baseline_evidence = {
        "schema_version": 1,
        "event": "job_cont12_r0_zero_call_reclassification",
        "point_id": point214["point_id"],
        "classification": "formal_startup_baseline",
        "point_result_path": str(POINT214),
        "point_result_sha256": EXPECTED_SHA256[POINT214],
        "candidate_replay_budget_after": 0,
        "accepted_budget_after": 0,
        "startup_baseline_count_after": 1,
        "physical_call_already_counted": True,
        "formal_search_replay_counter_preserved": 156,
        "global_ordinal_preserved": 215,
        "new_workload_calls": 0,
    }
    baseline_evidence_path = (
        OUTPUT / "startup_baseline_reclassification_evidence.json"
    )
    atomic_json(baseline_evidence_path, baseline_evidence)
    config["startup_baseline_reuse"] = {
        "enabled": True,
        "point_result_path": str(POINT214),
        "point_result_sha256": EXPECTED_SHA256[POINT214],
        "reclassification_evidence_path": str(baseline_evidence_path),
        "reclassification_evidence_sha256": sha256(baseline_evidence_path),
        "startup_baseline_count_before_restart": 1,
        "db_qps_source": "formal_benchmark_db_qps",
        "baseline_sql_snapshot_path": str(SEALED_SQL),
        "baseline_sql_snapshot_sha256": EXPECTED_SHA256[SEALED_SQL],
        "baseline_sql_expected_query_count": 8,
    }

    plan_estimator = config["metrics"]["plan_estimator"]
    ratio_scale = plan_estimator.setdefault("ratio_scale", {})
    ratio_scale["enabled"] = True
    boundary_scan = ratio_scale.setdefault("boundary_scan", {})
    boundary_scan.update({
        "enabled": True,
        "max_distinct_clusters": 5,
        "min_target_improvement_pct": 0.5,
    })
    model_retraining = plan_estimator.setdefault("model_retraining", {})
    model_retraining.update({
        "enabled": True,
        "apply_to_runtime": True,
        "run_id": CONT12_RETRAIN_RUN_ID,
        "output_dir": str(OUTPUT / "resource_model_retraining"),
        "baseline_error_bound_enabled": True,
        "require_all_resources": True,
        "strict_retrain_gate": True,
        "fallback_on_error": False,
    })

    calibration_path = OUTPUT / "reverse_direction_calibration.json"
    atomic_json(calibration_path, calibration)
    config["optimizer"]["gradient"]["replay_direction_calibration"] = {
        "enabled": True,
        "artifact_path": str(calibration_path),
        "artifact_sha256": sha256(calibration_path),
        "scope_id": "job-cont11-reverse-after-p209-p215",
    }
    config["optimizer"]["gradient"]["real_evidence_exploration_anchor"] = {
        "enabled": False,
    }
    config["optimizer"]["gradient"]["subset_log_dir"] = str(
        OUTPUT / "subset_logs"
    )
    config["output"]["directory"] = str(OUTPUT)
    config["logging"]["history_path"] = str(
        OUTPUT / "optimizer_history.jsonl"
    )
    config["_job_formal_continuation"] = {
        "schema_version": "job_formal10_cont12_ratio_anchor_fix_v1",
        "previous_formal_dir": str(PREVIOUS),
        "accepted_count_start": 0,
        "formal_replay_count_start": 156,
        "global_replay_ordinal_at_binding": 215,
        "absolute_replay_cap": 161,
        "batch_physical_cap": 5,
        "qps_used_for_inner_scoring": False,
        "startup_baseline_reused_point": 214,
        "stale_resume_checkpoint_bindings_removed": copy.deepcopy(
            removed_resume_bindings
        ),
        "current_run_ratio_anchor_required": True,
        "boundary_threshold_pct": 0.5,
        "reverse_probe_only_until_actual_acceptance": True,
        "required_environment_contract": {
            "RESOURCE_MODEL_RETRAIN_ENABLED": "1",
            "BONECT_STRICT_RESOURCE_RETRAIN_GATE": "1",
            "BONECT_ALLOW_V5_RETRAIN_FALLBACK": "0",
        },
    }

    if config["startup_baseline_reuse"][
        "reclassification_evidence_sha256"
    ] != sha256(baseline_evidence_path):
        raise RuntimeError("generated baseline evidence binding changed")
    if ratio_scale.get("enabled") is not True:
        raise RuntimeError("ratio-scale is not enabled")
    if (
        boundary_scan.get("enabled") is not True
        or float(boundary_scan.get("min_target_improvement_pct")) != 0.5
        or int(boundary_scan.get("max_distinct_clusters")) != 5
    ):
        raise RuntimeError("ratio boundary contract changed")
    if (
        model_retraining.get("enabled") is not True
        or model_retraining.get("apply_to_runtime") is not True
        or model_retraining.get("run_id") != CONT12_RETRAIN_RUN_ID
        or Path(str(model_retraining.get("output_dir"))).resolve()
        != (OUTPUT / "resource_model_retraining").resolve()
    ):
        raise RuntimeError("current-run model retraining contract changed")
    if find_resume_checkpoint_bindings(config):
        raise RuntimeError("generated config contains a resume checkpoint binding")

    config_path = OUTPUT / "config.json"
    atomic_json(config_path, config)
    manifest = {
        "schema_version": "job_formal10_cont12_ratio_anchor_fix_manifest_v1",
        "preparation_only": True,
        "workload_started": False,
        "output_config": str(config_path),
        "output_config_sha256": sha256(config_path),
        "parameter_optimizer_path": str(OPTIMIZER),
        "parameter_optimizer_sha256": EXPECTED_SHA256[OPTIMIZER],
        "previous_binding": previous_binding,
        "previous_driver_exit_code": previous_exit_code,
        "previous_failure_audit": failure_audit,
        "accepted_count_start": 0,
        "formal_replay_count_start": 156,
        "global_replay_ordinal_at_binding": 215,
        "absolute_replay_cap": 161,
        "maximum_physical_calls_this_batch": 5,
        "startup_baseline_reused_point": 214,
        "startup_baseline_point_sha256": EXPECTED_SHA256[POINT214],
        "startup_baseline_evidence_sha256": sha256(
            baseline_evidence_path
        ),
        "sealed_sql_sha256": EXPECTED_SHA256[SEALED_SQL],
        "removed_resume_checkpoint_bindings": removed_resume_bindings,
        "remaining_resume_checkpoint_bindings": [],
        "ratio_scale_enabled": True,
        "current_run_ratio_anchor_required": True,
        "model_retraining_run_id": CONT12_RETRAIN_RUN_ID,
        "model_retraining_output_dir": str(
            OUTPUT / "resource_model_retraining"
        ),
        "direction_calibration_sha256": sha256(calibration_path),
        "source_direction_calibration_sha256": source_calibration_sha,
        "boundary_threshold_pct": 0.5,
        "qps_used_for_inner_scoring": False,
        "required_environment_contract": {
            "RESOURCE_MODEL_RETRAIN_ENABLED": "1",
            "BONECT_STRICT_RESOURCE_RETRAIN_GATE": "1",
            "BONECT_ALLOW_V5_RETRAIN_FALLBACK": "0",
        },
    }
    manifest_path = OUTPUT / "continuation_manifest.json"
    atomic_json(manifest_path, manifest)
    print(json.dumps({
        "output": str(OUTPUT),
        "config_sha256": sha256(config_path),
        "manifest_sha256": sha256(manifest_path),
        "preparation_only": True,
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
