#!/usr/bin/env python3
"""Bind the isolated JOB T-SGD retry to target-only stability contract v5."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Dict


OLD_FORMAL_CONTRACTS_SHA256 = (
    "71ce3d498cfc51c0bfeb2e9ec83fa6433bf169cc0422826e8f20bc70449b6f73"
)
NEW_FORMAL_CONTRACTS_SHA256 = (
    "245374b95e37a8676016ad19a80336c90e93258a5b410b536fc5325fa8be02cc"
)
OLD_FORMAL_MEASUREMENT_SHA256 = (
    "4c469e7ef7ffe53ace75cca7a8cbdc62d2bf8d1ff741e6b8bc2f0c72c59388ba"
)
NEW_FORMAL_MEASUREMENT_SHA256 = (
    "6bcffc34ab47c0f774ed6bdcf56eb5d82a3c752e16bb4dd8c509555e4b1fd592"
)
OLD_FROZEN_STABILITY_SHA256 = (
    "ef368b7e40ebf13e3ee75fef4144bb8d8ce8893ac9510c42823e16f854b488e2"
)
STABILITY_V5_SHA256 = (
    "4b90d0126806ac16031fafbfe905d3aa3d693ddf28b64717eb348fb0911d4692"
)
OLD_MEASUREMENT_PROTOCOL_SHA256 = (
    "eafaaa50adbf4799ee60b5754bf9c69d5f285aa83fd3311e6d3a46ea76908622"
)
SOURCE_CONFIG_SHA256 = (
    "8436f6381f88ff3970ca10b24227942c6aebbe1177e5b43b02b3a5ded340a0da"
)
GENERATION_MANIFEST_SHA256 = (
    "40811927a11d52381343e7a23168b7d9c28c45a3628a7e5235ef975d3c0fb37f"
)
RELEASE_MANIFEST_SHA256 = (
    "263df822345f9a510d7c88527d53d637edb14033017b4a4cb6ab240c73520fa5"
)
P43_LOG_SHA256 = (
    "84909b272b5fedcf8d7517364b0f5c69c3aa20cb221d76012c69c2bb8d219c1e"
)
P43_EXIT_SHA256 = (
    "2245b44782682fb10402b932c5c142c6fcd5236e22469e9e089bbb12dfc5f68c"
)
P43_REPLICATE_RESULT_SHA256 = (
    "ca2cba360d14e6e7e1ff42deffc6ca74c6b23eaa058726d3467929b355c11cc2"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_sha(path: Path, expected: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"required file is missing: {path}")
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"SHA mismatch: {path}: {actual} != {expected}")


def write_json_new(path: Path, value: Any, *, mode: int = 0o444) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite sealed output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    path.chmod(mode)


def archive_new(source: Path, destination: Path, expected: str) -> None:
    require_sha(source, expected)
    if destination.exists() or destination.is_symlink():
        raise RuntimeError(f"refusing to overwrite archive: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(0o444)
    require_sha(destination, expected)


def deploy(source: Path, destination: Path, expected: str, *, mode: int) -> None:
    require_sha(source, expected)
    temporary = destination.with_name(destination.name + ".v5.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError(f"stale deployment temporary exists: {temporary}")
    shutil.copyfile(source, temporary)
    temporary.chmod(mode)
    temporary.replace(destination)
    require_sha(destination, expected)


def make_measurement_protocol(old: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(old)
    result["config_id"] = "measurement_protocol_20260723_v17_job_target_memory_v5"
    result["immutable"] = True
    result["public_stability_gate_contract_sha256"] = STABILITY_V5_SHA256
    timing = result.setdefault("timing", {})
    timing.update(
        {
            "sample_interval_seconds": 1,
            "warmup_seconds": 60,
            "minimum_measurement_seconds": 180,
            "initial_probe_seconds": 180,
            "decision_step_seconds": 30,
            "maximum_measurement_seconds": 600,
            "stability_window_seconds": 30,
            "required_consecutive_stability_windows": 6,
            "sample_coverage_min": 0.9,
            "continue_after_probe_when_unstable": True,
        }
    )
    stability = result.setdefault("stability", {})
    stability.update(
        {
            "calculation": (
                "coefficient_of_variation_across_six_target_resource_window_means"
            ),
            "target_resource": "memory",
            "target_resource_field": "memory_utilization",
            "target_cv_max": 0.1,
            "diagnostic_cv_max": 0.1,
            "all_cpu_memory_io_resources_must_be_stable": False,
            "throughput_fields_required_for_stability": False,
            "throughput_role": "outer_acceptance_only",
            "candidate_profile_band_gate_enabled": False,
            "normal_throughput_calculation": "outer_acceptance_only",
            "normal_throughput_cv_max": 0.1,
            "xp_xap_qps_cv_max": 0.1,
            "xp_xtp_tps_cv_max": 0.1,
            "low_utilization_ratio_threshold": 0.1,
            "low_utilization_absolute_range_max": 0.05,
            "raw_one_second_samples_retained": True,
            "recent_complete_window_count": 6,
            "resource_window_aggregation": "arithmetic_mean",
            "within_window_one_second_cv_required": False,
        }
    )
    revision = {
        "scope": "JOB T-SGD p44 and later points launched from this freeze only",
        "target_resource": "memory",
        "retained_resource_fields": [
            "cpu_utilization",
            "memory_utilization",
            "io_utilization",
        ],
        "stability_metric_fields": ["memory_utilization"],
        "stability_gate_contract_file": "stability_gate_contract.json",
        "stability_gate_contract_sha256": STABILITY_V5_SHA256,
        "warmup_seconds": 60,
        "minimum_measurement_seconds": 180,
        "decision_step_seconds": 30,
        "maximum_measurement_seconds": 600,
        "window_seconds": 30,
        "rolling_window_count": 6,
        "coefficient_of_variation_max": 0.1,
        "throughput_stability_required": False,
        "candidate_profile_band_gate_enabled": False,
        "unstable_point_repeat_policy": {
            "maximum_total_replicates": 3,
            "aggregation": "arithmetic_mean_of_valid_replicates",
        },
        "retroactive_acceptance": False,
    }
    result["job_tsgd_v5_revision"] = revision
    attempt8 = result.setdefault("attempt8_revision", {})
    attempt8["superseded_for_p44_and_later_by"] = "job_tsgd_v5_revision"
    baseline = result.setdefault("baseline_gates", {})
    baseline["scope"] = "baseline_profile_construction_only"
    baseline["affects_candidate_stability"] = False
    baseline["affects_outer_acceptance"] = False
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-root", type=Path, required=True)
    parser.add_argument("--staged-formal-contracts", type=Path, required=True)
    parser.add_argument("--staged-formal-measurement", type=Path, required=True)
    parser.add_argument("--staged-stability-v5", type=Path, required=True)
    args = parser.parse_args()

    lane = args.lane_root.resolve()
    attempt = lane / "history/job/outer/attempt_5_formal_tsgd_v1"
    workspace = lane / "workspace_outer_attempt4_formal_dbqps_v1_retry3"
    frozen = lane / "bundle/attempt8_frozen"
    source_config = (
        attempt
        / "generated_config/job_tsgd_fresh_v1_model_reuse_literal_sql/memory/job.json"
    )
    generation_manifest = attempt / "fresh_tsgd_generation_manifest_v1.json"
    release_manifest = attempt / "initial_batch_release_manifest_40811927.json"
    log_path = attempt / "launcher_tsgd_formal_initial_batch.log"
    exit_path = attempt / "launcher_tsgd_formal_initial_batch_exit.json"
    p43_result = (
        attempt
        / "formal_points/point_0043/replicates/replicate_01/point_result.json"
    )
    old_protocol_path = frozen / "measurement_protocol.json"
    old_stability_path = frozen / "stability_gate_contract.json"
    workspace_contracts = workspace / "formal_contracts.py"
    workspace_measurement = workspace / "formal_measurement.py"

    require_sha(args.staged_formal_contracts.resolve(), NEW_FORMAL_CONTRACTS_SHA256)
    require_sha(args.staged_formal_measurement.resolve(), NEW_FORMAL_MEASUREMENT_SHA256)
    require_sha(args.staged_stability_v5.resolve(), STABILITY_V5_SHA256)
    require_sha(source_config, SOURCE_CONFIG_SHA256)
    require_sha(generation_manifest, GENERATION_MANIFEST_SHA256)
    require_sha(release_manifest, RELEASE_MANIFEST_SHA256)
    require_sha(log_path, P43_LOG_SHA256)
    require_sha(exit_path, P43_EXIT_SHA256)
    require_sha(p43_result, P43_REPLICATE_RESULT_SHA256)
    require_sha(workspace_contracts, OLD_FORMAL_CONTRACTS_SHA256)
    require_sha(workspace_measurement, OLD_FORMAL_MEASUREMENT_SHA256)
    require_sha(old_protocol_path, OLD_MEASUREMENT_PROTOCOL_SHA256)
    require_sha(old_stability_path, OLD_FROZEN_STABILITY_SHA256)
    for name, expected in {
        "replay_counter.txt": 43,
        "formal_search_replay_counter.txt": 1,
        "formal_search_accepted_counter.txt": 0,
        "formal_startup_baseline_counter.txt": 1,
    }.items():
        actual = int((attempt / name).read_text(encoding="utf-8").strip())
        if actual != expected:
            raise RuntimeError(f"counter drift: {name}: {actual} != {expected}")
    if (attempt / "formal_points/point_0044").exists():
        raise RuntimeError("p44 already exists before v5 transition")
    p43_payload = json.loads(p43_result.read_text(encoding="utf-8"))
    if (
        p43_payload.get("status") != "failed"
        or p43_payload.get("error")
        != "public stability contract checksum mismatch"
        or p43_payload.get("measurement") is not None
    ):
        raise RuntimeError("p43 is not the expected pre-measurement technical failure")

    archive = attempt / "v5_transition_archive"
    archive_new(
        old_stability_path,
        archive / "stability_gate_contract_pre_v5_ef368b7e.json",
        OLD_FROZEN_STABILITY_SHA256,
    )
    archive_new(
        old_protocol_path,
        archive / "measurement_protocol_pre_v5_eafaaa50.json",
        OLD_MEASUREMENT_PROTOCOL_SHA256,
    )
    archive_new(
        workspace_contracts,
        archive / "formal_contracts_pre_v5_71ce3d49.py",
        OLD_FORMAL_CONTRACTS_SHA256,
    )
    archive_new(
        workspace_measurement,
        archive / "formal_measurement_pre_v5_4c469e7e.py",
        OLD_FORMAL_MEASUREMENT_SHA256,
    )

    measurement_protocol = make_measurement_protocol(
        json.loads(old_protocol_path.read_text(encoding="utf-8"))
    )
    new_protocol_path = attempt / "measurement_protocol_v17_job_target_memory_v5.json"
    write_json_new(new_protocol_path, measurement_protocol)
    new_protocol_sha = sha256(new_protocol_path)

    p43_audit_path = attempt / "p43_technical_failure_audit_v1.json"
    p43_audit = {
        "schema_version": 1,
        "status": "sealed_technical_failure_excluded_from_valid_batch",
        "point_ordinal": 43,
        "candidate_rank_attempted": 1,
        "valid_initial_batch_count_after": 0,
        "counts_toward_valid_initial_batch": False,
        "counts_toward_acceptance": False,
        "physical_and_formal_claim_counters_consumed": True,
        "counter_state_after": {
            "global": 43,
            "formal_search": 1,
            "accepted": 0,
            "startup_baselines": 1,
        },
        "retry": {"candidate_rank": 1, "next_physical_ordinal": 44},
        "failure_stage": "before_measurement_workload",
        "failure": "public stability contract checksum mismatch",
        "evidence": {
            "replicate_result_path": str(p43_result),
            "replicate_result_sha256": P43_REPLICATE_RESULT_SHA256,
            "launcher_log_path": str(log_path),
            "launcher_log_sha256": P43_LOG_SHA256,
            "launcher_exit_path": str(exit_path),
            "launcher_exit_sha256": P43_EXIT_SHA256,
        },
        "old_frozen_stability_sha256": OLD_FROZEN_STABILITY_SHA256,
        "replacement_stability_v5_sha256": STABILITY_V5_SHA256,
        "retroactive_validity": False,
    }
    write_json_new(p43_audit_path, p43_audit)
    p43_audit_sha = sha256(p43_audit_path)

    config = json.loads(source_config.read_text(encoding="utf-8"))
    protocol = config.setdefault("formal_outer_protocol", {})
    protocol.update(
        {
            "global_replay_ordinal_at_binding": 43,
            "formal_search_replay_count_start": 1,
            "formal_search_accepted_count_start": 0,
            "formal_startup_baseline_count_start": 1,
            "valid_initial_batch_count_start": 0,
            "technical_failure_ordinal_43_excluded_from_valid_batch": True,
            "rank1_retry_physical_ordinal": 44,
            "initial_batch_valid_replay_barrier": 5,
            "single_candidate_early_accept_forbidden": True,
            "technical_failure_audit_path": str(p43_audit_path),
            "technical_failure_audit_sha256": p43_audit_sha,
        }
    )
    generation = config.setdefault("_tsgd_generation", {})
    generation["candidate_replay_release_state"] = (
        "released_for_p44_after_v5_binding_and_p43_technical_failure"
    )
    generation.setdefault("deployed_core_sha256", {})[
        "formal_contracts.py"
    ] = NEW_FORMAL_CONTRACTS_SHA256
    generation["formal_measurement_v5"] = {
        "target_resource": "memory",
        "target_resource_field": "memory_utilization",
        "retained_resource_fields": [
            "cpu_utilization",
            "memory_utilization",
            "io_utilization",
        ],
        "stability_gate_contract_path": str(old_stability_path),
        "stability_gate_contract_sha256": STABILITY_V5_SHA256,
        "measurement_protocol_path": str(old_protocol_path),
        "measurement_protocol_sha256": new_protocol_sha,
        "formal_contracts_path": str(workspace_contracts),
        "formal_contracts_sha256": NEW_FORMAL_CONTRACTS_SHA256,
        "formal_measurement_path": str(workspace_measurement),
        "formal_measurement_sha256": NEW_FORMAL_MEASUREMENT_SHA256,
        "minimum_measurement_seconds": 180,
        "decision_step_seconds": 30,
        "maximum_measurement_seconds": 600,
        "candidate_profile_band_gate_enabled": False,
        "throughput_stability_required": False,
    }
    # The common campaign file validator still has a separate rolling-v4 pin.
    # The actual frozen formal measurement contract is bound above to v5.
    generation["campaign_rolling_contract_remains_separate"] = {
        "config_key": "stability_gate_contract",
        "sha256": (
            "ba2810b36fd1caf36b8f6679e380e5e97f13ff5d357d3227fbfd7d0219393853"
        ),
        "role": "common_campaign_file_validation_only",
    }
    config_output = (
        attempt
        / "generated_config/job_tsgd_fresh_v1_v5_after_p43/memory/job.json"
    )
    write_json_new(config_output, config)
    config_sha = sha256(config_output)

    manifest_path = attempt / "job_tsgd_v5_resume_after_p43_manifest_v1.json"
    manifest = {
        "schema_version": 1,
        "status": "sealed_ready_to_retry_rank1_at_p44_under_target_only_v5",
        "original_generation_manifest_path": str(generation_manifest),
        "original_generation_manifest_sha256": GENERATION_MANIFEST_SHA256,
        "initial_batch_release_manifest_path": str(release_manifest),
        "initial_batch_release_manifest_sha256": RELEASE_MANIFEST_SHA256,
        "resume_config_path": str(config_output),
        "resume_config_sha256": config_sha,
        "p43_technical_failure_audit_path": str(p43_audit_path),
        "p43_technical_failure_audit_sha256": p43_audit_sha,
        "frozen_measurement": {
            "target_resource": "memory",
            "target_resource_field": "memory_utilization",
            "stability_gate_contract_path": str(old_stability_path),
            "stability_gate_contract_sha256": STABILITY_V5_SHA256,
            "measurement_protocol_path": str(old_protocol_path),
            "measurement_protocol_sha256": new_protocol_sha,
            "formal_contracts_path": str(workspace_contracts),
            "formal_contracts_sha256": NEW_FORMAL_CONTRACTS_SHA256,
            "formal_measurement_path": str(workspace_measurement),
            "formal_measurement_sha256": NEW_FORMAL_MEASUREMENT_SHA256,
            "retained_resource_fields": [
                "cpu_utilization",
                "memory_utilization",
                "io_utilization",
            ],
            "stability_metric_fields": ["memory_utilization"],
            "warmup_seconds": 60,
            "minimum_measurement_seconds": 180,
            "decision_step_seconds": 30,
            "maximum_measurement_seconds": 600,
            "window_seconds": 30,
            "rolling_window_count": 6,
            "coefficient_of_variation_max": 0.1,
            "throughput_stability_required": False,
            "candidate_profile_band_gate_enabled": False,
        },
        "resume_counters": {
            "global": 43,
            "formal_search": 1,
            "accepted": 0,
            "startup_baselines": 1,
            "valid_initial_batch": 0,
        },
        "next_replay": {"physical_ordinal": 44, "candidate_rank": 1},
        "staged_replay_semantics": {
            "cumulative_batch_sizes": [5, 10, 16],
            "initial_batch_requires_five_valid_replays": True,
            "p43_technical_failure_is_not_valid": True,
            "acceptance_before_five_valid_replays_forbidden": True,
            "expand_to_10_only_if_all_first_5_unacceptable": True,
            "expand_to_16_only_if_all_first_10_unacceptable": True,
        },
        "archive_path": str(archive),
    }
    write_json_new(manifest_path, manifest)
    manifest_sha = sha256(manifest_path)

    workspace_v5 = (
        workspace
        / "config/formal/pg5_common_outer_bgallowed_20260722_v1"
        / "stability_gate_contract_v5.json"
    )
    if workspace_v5.exists() or workspace_v5.is_symlink():
        require_sha(workspace_v5, STABILITY_V5_SHA256)
    else:
        workspace_v5.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.staged_stability_v5.resolve(), workspace_v5)
        workspace_v5.chmod(0o444)
        require_sha(workspace_v5, STABILITY_V5_SHA256)

    deploy(
        args.staged_stability_v5.resolve(),
        old_stability_path,
        STABILITY_V5_SHA256,
        mode=0o600,
    )
    deploy(new_protocol_path, old_protocol_path, new_protocol_sha, mode=0o600)
    deploy(
        args.staged_formal_contracts.resolve(),
        workspace_contracts,
        NEW_FORMAL_CONTRACTS_SHA256,
        mode=0o644,
    )
    deploy(
        args.staged_formal_measurement.resolve(),
        workspace_measurement,
        NEW_FORMAL_MEASUREMENT_SHA256,
        mode=0o644,
    )

    print(
        json.dumps(
            {
                "status": manifest["status"],
                "config_path": str(config_output),
                "config_sha256": config_sha,
                "manifest_path": str(manifest_path),
                "manifest_sha256": manifest_sha,
                "measurement_protocol_sha256": new_protocol_sha,
                "p43_audit_sha256": p43_audit_sha,
                "formal_contracts_sha256": NEW_FORMAL_CONTRACTS_SHA256,
                "formal_measurement_sha256": NEW_FORMAL_MEASUREMENT_SHA256,
                "stability_v5_sha256": STABILITY_V5_SHA256,
                "next_point": 44,
                "valid_initial_batch_count": 0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
