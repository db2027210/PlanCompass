#!/usr/bin/env python3
"""Prepare JOB continuation 13 using the cont10-validated ratio-anchor path.

This preparation-only tool never launches PostgreSQL or a workload.  It starts
from the cont12 prepared configuration, disables resource-model retraining,
and SHA-binds the two successful cont10 direction-gate records and their
ratio-scale resource anchors.
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
CONT10 = BASE / "formal10_after_round10_gate_cont10_reuse_r0_v1_20260729T1904"
CONT10_CONFIG = CONT10 / "config.json"
CONT10_HISTORY = CONT10 / "optimizer_history_2026-07-29_190026.jsonl"
CONT12 = (
    BASE
    / "formal10_after_round10_gate_cont12_ratio_anchor_fix_v1_20260729T1950"
)
OUTPUT = (
    BASE
    / "formal10_after_round10_gate_cont13_ratio_anchor_reuse_v1_20260729T2005"
)
OPTIMIZER = LANE / "workspace_attempt7/parameter_optimizer.py"
ACCEPTED = BASE / "formal10_accepted_counter.txt"
REPLAY = BASE / "formal_search_replay_counter.txt"
GLOBAL = BASE / "replay_counter.txt"
POINT214 = BASE / "formal_points/point_0214/point_result.json"
POINT215 = BASE / "formal_points/point_0215/point_result.json"
SEALED_SQL = BASE / "sealed_startup_baseline_sql_job8_literal_v2.json"

EXPECTED_SHA256 = {
    OPTIMIZER:
        "c54b9dba238bb03c5333c0dbbb780299f0ecaf09de2d8de3c590ba693c1c0ed2",
    CONT10_CONFIG:
        "250a029306b9bf29242f77d8a6471340866fd977e3a1779c15c14780e964b2cb",
    CONT10_HISTORY:
        "a8502fdfcd6429d41b005756d65520dbb272cd96d020f9868588f3e978cf4d6b",
    POINT214:
        "8e98ee2e7340511a113ff0f72b31fcafa9c957667ad1879b0066bdd55e330d57",
    POINT215:
        "efbe78a8d6c3fe56a7031303c268fc081f261c1fd0509516f89d30db895cbf0a",
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
POINT215_FINGERPRINT = (
    "three-level-sha256:41f2a85edf7d6a2fcb03ee0b286c224d"
    "f2d4e1680fb16cbfd61fd291f13156a8"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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


def _is_resume_binding(dotted: str, key: str) -> bool:
    normalized_dotted = dotted.strip().lower()
    normalized_key = key.strip().lower()
    return bool(
        "resume_checkpoint" in normalized_dotted
        and (
            normalized_key in {"path", "sha", "sha256"}
            or normalized_key.endswith("_path")
            or normalized_key.endswith("_sha")
            or normalized_key.endswith("_sha256")
        )
    )


def remove_resume_checkpoint_bindings(
    value: Any,
    *,
    prefix: str = "",
) -> list[dict[str, Any]]:
    removed: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key in list(value):
            dotted = f"{prefix}.{key}" if prefix else str(key)
            if _is_resume_binding(dotted, str(key)):
                removed.append({
                    "key": dotted,
                    "value": copy.deepcopy(value.pop(key)),
                })
                continue
            removed.extend(
                remove_resume_checkpoint_bindings(value[key], prefix=dotted)
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            removed.extend(
                remove_resume_checkpoint_bindings(
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
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            dotted = f"{prefix}.{key}" if prefix else str(key)
            if _is_resume_binding(dotted, str(key)):
                found.append(dotted)
            found.extend(
                find_resume_checkpoint_bindings(item, prefix=dotted)
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(
                find_resume_checkpoint_bindings(
                    item,
                    prefix=f"{prefix}[{index}]",
                )
            )
    return found


def validate_prepared_cont12() -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = CONT12 / "continuation_manifest.json"
    config_path = CONT12 / "config.json"
    manifest = load_json(manifest_path)
    if (
        manifest.get("schema_version")
        != "job_formal10_cont12_ratio_anchor_fix_manifest_v1"
        or manifest.get("preparation_only") is not True
        or manifest.get("workload_started") is not False
        or int(manifest.get("accepted_count_start")) != 0
        or int(manifest.get("formal_replay_count_start")) != 156
        or int(manifest.get("global_replay_ordinal_at_binding")) != 215
    ):
        raise RuntimeError("cont12 preparation manifest contract changed")
    actual_config_sha = sha256(config_path)
    if actual_config_sha != str(manifest.get("output_config_sha256") or ""):
        raise RuntimeError("cont12 config/manifest SHA mismatch")
    config = load_json(config_path)
    evidence_cfg = config.get("startup_baseline_reuse") or {}
    evidence_path = Path(
        str(evidence_cfg.get("reclassification_evidence_path") or "")
    )
    evidence_sha = str(
        evidence_cfg.get("reclassification_evidence_sha256") or ""
    )
    if not evidence_path.is_file() or sha256(evidence_path) != evidence_sha:
        raise RuntimeError("cont12 baseline evidence binding is invalid")
    evidence = load_json(evidence_path)
    if (
        evidence.get("classification") != "formal_startup_baseline"
        or evidence.get("point_result_sha256") != EXPECTED_SHA256[POINT214]
        or evidence.get("candidate_replay_budget_after") != 0
        or evidence.get("accepted_budget_after") != 0
        or evidence.get("startup_baseline_count_after") != 1
        or evidence.get("new_workload_calls") != 0
    ):
        raise RuntimeError("cont12 baseline evidence contract changed")
    return config, {
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "config_path": str(config_path),
        "config_sha256": actual_config_sha,
        "baseline_evidence_path": str(evidence_path),
        "baseline_evidence_sha256": evidence_sha,
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


def extract_cont10_direction_evidence() -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        CONT10_HISTORY.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"invalid cont10 history JSON at line {line_number}"
            ) from exc
        if not isinstance(record, dict):
            raise RuntimeError(
                f"invalid cont10 history record at line {line_number}"
            )
        if record.get("event") == "estimator_direction_valid":
            events.append(copy.deepcopy(record))
    if len(events) != 2:
        raise RuntimeError(
            f"expected two cont10 estimator_direction_valid events, got {len(events)}"
        )

    event_bindings: list[dict[str, Any]] = []
    all_anchors: list[dict[str, Any]] = []
    for event_index, event in enumerate(events, start=1):
        summaries = event.get("summaries") or []
        valid_anchors: list[dict[str, Any]] = []
        for summary_index, summary in enumerate(summaries, start=1):
            if (
                not isinstance(summary, dict)
                or summary.get("enabled") is not True
                or summary.get("valid") is not True
            ):
                continue
            calibrated_anchor = (
                (summary.get("checks") or {}).get("calibrated_anchor") or {}
            )
            ratio_anchor = calibrated_anchor.get("ratio_scale_anchor") or {}
            resources = ratio_anchor.get("resources") or {}
            if (
                not isinstance(ratio_anchor, dict)
                or calibrated_anchor.get("ratio_scale_anchor_valid") is not True
                or ratio_anchor.get("enabled") is not True
                or ratio_anchor.get("formal_pg_resource_complete") is not True
                or ratio_anchor.get("candidate_generation_before_calibration")
                is not False
                or ratio_anchor.get("memory_semantics")
                != "formal_working_set_then_fixed_dynamic_decomposition"
                or not isinstance(resources, dict)
                or set(resources) < {"cpu", "io", "memory"}
            ):
                continue
            for target in ("cpu", "io", "memory"):
                measured = float(resources[target])
                if not math.isfinite(measured) or measured <= 0.0:
                    raise RuntimeError(
                        f"cont10 ratio anchor has invalid {target} resource"
                    )
                anchor_record = {
                    "event_index": event_index,
                    "summary_index": summary_index,
                    "target_resource": target,
                    "measured_resource": measured,
                    "baseline_real": measured,
                    "formal_pg_resource_complete": True,
                    "memory_semantics": ratio_anchor.get("memory_semantics"),
                    "candidate_generation_before_calibration": False,
                }
                valid_anchors.append(anchor_record)
                all_anchors.append(copy.deepcopy(anchor_record))
        if not valid_anchors:
            raise RuntimeError(
                f"cont10 valid direction event {event_index} has no valid ratio anchor"
            )
        event_bindings.append({
            "event_index": event_index,
            "event_sha256": canonical_sha256(event),
            "valid_ratio_scale_anchors": valid_anchors,
            "record": event,
        })
    if "memory" not in {
        anchor["target_resource"] for anchor in all_anchors
    }:
        raise RuntimeError("cont10 evidence has no valid memory ratio anchor")
    return {
        "schema_version": "job_cont10_direction_valid_evidence_v1",
        "source_config_path": str(CONT10_CONFIG),
        "source_config_sha256": EXPECTED_SHA256[CONT10_CONFIG],
        "source_history_path": str(CONT10_HISTORY),
        "source_history_sha256": EXPECTED_SHA256[CONT10_HISTORY],
        "estimator_direction_valid_event_count": 2,
        "event_bindings": event_bindings,
        "ratio_scale_resource_anchors": all_anchors,
        "qps_used_for_inner_scoring": False,
    }


def validate_reverse_calibration(
    config: Mapping[str, Any],
) -> dict[str, Any]:
    binding = (
        (((config.get("optimizer") or {}).get("gradient") or {}).get(
            "replay_direction_calibration"
        ) or {})
    )
    path = Path(str(binding.get("artifact_path") or ""))
    expected = str(binding.get("artifact_sha256") or "")
    if (
        binding.get("enabled") is not True
        or not path.is_file()
        or sha256(path) != expected
    ):
        raise RuntimeError("cont12 reverse calibration binding is invalid")
    calibration = load_json(path)
    decisions = calibration.get("same_incumbent_scope_decisions") or []
    runtime_policy = (
        ((decisions[0].get("decision") or {}).get("runtime_policy") or {})
        if len(decisions) == 1 and isinstance(decisions[0], dict)
        else {}
    )
    if (
        calibration.get("qps_used_for_inner_scoring") is not False
        or len(decisions) != 1
        or decisions[0].get("scope_id")
        != "job-cont11-reverse-after-p209-p215"
        or runtime_policy.get("action") != "reverse_probe"
        or runtime_policy.get("probe_only") is not True
        or float(runtime_policy.get("eta_multiplier") or 0.0) != 0.25
    ):
        raise RuntimeError("reverse calibration contract changed")
    return calibration


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    for path, expected in EXPECTED_SHA256.items():
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"SHA mismatch: {path}: {actual} != {expected}"
            )
    if int((CONT10 / "driver_exit_code.txt").read_text(
        encoding="utf-8"
    ).strip()) != 0:
        raise RuntimeError("cont10 did not exit cleanly")
    counters = {
        "accepted": read_int(ACCEPTED),
        "formal_replay": read_int(REPLAY),
        "global_ordinal": read_int(GLOBAL),
    }
    if counters != EXPECTED_COUNTERS:
        raise RuntimeError(
            f"counter mismatch: {counters} != {EXPECTED_COUNTERS}"
        )
    if (BASE / "formal_points/point_0216").exists():
        raise RuntimeError("point216 exists; zero-call lineage changed")

    point214 = validate_point214()
    config, cont12_binding = validate_prepared_cont12()
    cont10_evidence = extract_cont10_direction_evidence()
    calibration = validate_reverse_calibration(config)
    if normalized_params(config.get("initial_params") or {}) != BASELINE_PARAMS:
        raise RuntimeError("cont12 initial parameters are not the JOB baseline")

    removed_resume_bindings = remove_resume_checkpoint_bindings(config)
    if find_resume_checkpoint_bindings(config):
        raise RuntimeError("resume checkpoint binding remains after cleanup")

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
    if POINT215_FINGERPRINT not in {
        str(item)
        for item in (
            protocol.get("excluded_prior_three_level_fingerprints") or []
        )
    }:
        raise RuntimeError("point215 fingerprint is not quarantined")

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
    model_retraining["enabled"] = False
    model_retraining["apply_to_runtime"] = False
    for stale_key in (
        "run_id",
        "output_dir",
        "strict_retrain_gate",
        "fallback_on_error",
    ):
        model_retraining.pop(stale_key, None)

    OUTPUT.mkdir(parents=True)
    atomic_text(OUTPUT / "state/formal_startup_baseline_counter.txt", "1\n")
    baseline_evidence = {
        "schema_version": 1,
        "event": "job_cont13_r0_zero_call_reclassification",
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

    direction_evidence_path = (
        OUTPUT / "cont10_estimator_direction_valid_evidence.json"
    )
    atomic_json(direction_evidence_path, cont10_evidence)
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
        "schema_version": "job_formal10_cont13_ratio_anchor_reuse_v1",
        "previous_preparation_dir": str(CONT12),
        "validated_ratio_anchor_source_dir": str(CONT10),
        "accepted_count_start": 0,
        "formal_replay_count_start": 156,
        "global_replay_ordinal_at_binding": 215,
        "absolute_replay_cap": 161,
        "batch_physical_cap": 5,
        "qps_used_for_inner_scoring": False,
        "startup_baseline_reused_point": 214,
        "stale_resume_checkpoint_bindings_removed": removed_resume_bindings,
        "model_retraining_enabled": False,
        "ratio_scale_enabled": True,
        "cont10_direction_evidence_path": str(direction_evidence_path),
        "cont10_direction_evidence_sha256": sha256(
            direction_evidence_path
        ),
        "boundary_threshold_pct": 0.5,
        "reverse_probe_only_until_actual_acceptance": True,
        "required_environment_contract": {
            "RESOURCE_MODEL_RETRAIN_ENABLED": "0",
        },
    }

    if (
        model_retraining.get("enabled") is not False
        or model_retraining.get("apply_to_runtime") is not False
        or ratio_scale.get("enabled") is not True
        or boundary_scan.get("enabled") is not True
        or int(boundary_scan.get("max_distinct_clusters")) != 5
        or float(boundary_scan.get("min_target_improvement_pct")) != 0.5
        or find_resume_checkpoint_bindings(config)
    ):
        raise RuntimeError("generated cont13 estimator contract changed")

    config_path = OUTPUT / "config.json"
    atomic_json(config_path, config)
    event_bindings = cont10_evidence["event_bindings"]
    manifest = {
        "schema_version": "job_formal10_cont13_ratio_anchor_reuse_manifest_v1",
        "preparation_only": True,
        "workload_started": False,
        "output_config": str(config_path),
        "output_config_sha256": sha256(config_path),
        "parameter_optimizer_path": str(OPTIMIZER),
        "parameter_optimizer_sha256": EXPECTED_SHA256[OPTIMIZER],
        "cont12_input_binding": cont12_binding,
        "cont10_config_path": str(CONT10_CONFIG),
        "cont10_config_sha256": EXPECTED_SHA256[CONT10_CONFIG],
        "cont10_history_path": str(CONT10_HISTORY),
        "cont10_history_sha256": EXPECTED_SHA256[CONT10_HISTORY],
        "cont10_estimator_direction_valid_event_count": 2,
        "cont10_estimator_direction_valid_event_sha256": [
            item["event_sha256"] for item in event_bindings
        ],
        "cont10_ratio_scale_resource_anchors": copy.deepcopy(
            cont10_evidence["ratio_scale_resource_anchors"]
        ),
        "cont10_direction_evidence_path": str(direction_evidence_path),
        "cont10_direction_evidence_sha256": sha256(
            direction_evidence_path
        ),
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
        "model_retraining_enabled": False,
        "model_retraining_apply_to_runtime": False,
        "direction_calibration_sha256": sha256(calibration_path),
        "boundary_threshold_pct": 0.5,
        "point215_fingerprint_excluded": True,
        "qps_used_for_inner_scoring": False,
        "required_environment_contract": {
            "RESOURCE_MODEL_RETRAIN_ENABLED": "0",
        },
    }
    manifest_path = OUTPUT / "continuation_manifest.json"
    atomic_json(manifest_path, manifest)
    print(json.dumps({
        "output": str(OUTPUT),
        "config_sha256": sha256(config_path),
        "manifest_sha256": sha256(manifest_path),
        "direction_evidence_sha256": sha256(direction_evidence_path),
        "preparation_only": True,
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
