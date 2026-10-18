#!/usr/bin/env python3
"""Build the formal PG5 TPC-C experience-table all-subset SGD configuration."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = "pg5_common_outer_bgallowed_20260722_v1"
SOURCE = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/attempt11/configs/"
    "experience_table_all_subset_sgd.safe.json"
)
OLD_TPCC = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_attempt2/experience_sgd/generated_configs/"
    "pg5v1_tpcc_exp_sgd_a1/cpu/tpcc.json"
)
OUTPUT_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt6"
)
OUTPUT = OUTPUT_ROOT / "config/tpcc_experience_table_all_subset_sgd.json"
TABLE_PATH = ROOT / (
    "config/formal/pg5_common_outer_bgallowed_20260722_v1/"
    "experience_table_T_tpcc_cpu_v3_semantic_prior.json"
)
CONTRACT_ROOT = ROOT / "config/formal/pg5_common_outer_bgallowed_20260722_v1"
ATTEMPT5_BASELINE = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt5/candidate_measurements/"
    "point_20260722_195530_4169942/summary.json"
)


@dataclass(frozen=True)
class CapabilityProfile:
    """Explicit physical profile bound to generated configuration artifacts."""

    cpu_cores: float
    memory_limit_bytes: int
    io_total_limit_mib_s: float
    io_read_cap_mib_s: float
    io_write_cap_mib_s: float

    def __post_init__(self) -> None:
        values = (
            self.cpu_cores,
            self.memory_limit_bytes,
            self.io_total_limit_mib_s,
            self.io_read_cap_mib_s,
            self.io_write_cap_mib_s,
        )
        if any(not math.isfinite(float(value)) or float(value) <= 0 for value in values):
            raise ValueError("CPU, memory, and I/O profile values must be finite and positive")
        io_sum = self.io_read_cap_mib_s + self.io_write_cap_mib_s
        if not math.isclose(io_sum, self.io_total_limit_mib_s, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("I/O read and write caps must sum to the total I/O cap")

    @property
    def memory_gib(self) -> float:
        return self.memory_limit_bytes / 1024 ** 3

    def capability(self) -> dict[str, float]:
        return {
            "cpu": self.cpu_cores,
            "memory": self.memory_gib,
            "io": self.io_total_limit_mib_s,
        }

    def io_bps(self, value_mib_s: float) -> int:
        value = value_mib_s * 1024 ** 2
        rounded = round(value)
        if not math.isclose(value, rounded, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError("I/O MiB/s caps must convert to an integral byte/s value")
        return int(rounded)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capability-manifest", type=Path, required=True)
    parser.add_argument("--direction-ranker", type=Path, required=True)
    parser.add_argument("--cpu-cap-cores", type=float, required=True)
    parser.add_argument("--memory-limit-bytes", type=int, required=True)
    parser.add_argument("--io-total-limit-mib-s", type=float, required=True)
    parser.add_argument("--io-read-cap-mib-s", type=float, required=True)
    parser.add_argument("--io-write-cap-mib-s", type=float, required=True)
    return parser.parse_args(argv)


def profile_from_mapping(
    value: Mapping[str, Any],
    *,
    source: str,
    require_physical_limits: bool = False,
) -> dict[str, float]:
    required = ["cpu", "memory", "io"]
    if require_physical_limits:
        required.extend(
            ("memory_limit_bytes", "io_read_cap_mib_s", "io_write_cap_mib_s")
        )
    missing = [key for key in required if key not in value]
    if missing:
        raise ValueError(f"{source} capability profile missing keys: {missing}")
    try:
        profile = {key: float(value[key]) for key in required}
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source} capability profile values must be numeric") from exc
    if any(not math.isfinite(item) or item <= 0 for item in profile.values()):
        raise ValueError(f"{source} capability profile values must be finite and positive")
    return profile


def load_manifest_profile(
    path: Path,
    *,
    source: str,
    require_physical_limits: bool = False,
) -> dict[str, float]:
    if not path.is_file():
        raise ValueError(f"{source} does not exist: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{source} is not a valid UTF-8 JSON document: {path}") from exc
    if not isinstance(document, Mapping):
        raise ValueError(f"{source} must contain a JSON object")

    profiles: Any = None
    training_contract = document.get("training_contract")
    if isinstance(training_contract, Mapping):
        profiles = training_contract.get("capability_profiles")
    if profiles is not None:
        if not isinstance(profiles, list) or len(profiles) != 1:
            raise ValueError(f"{source} must bind exactly one capability profile")
        value = profiles[0]
    else:
        value = document.get("capability_profile", document.get("capability"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{source} has no capability profile")
    return profile_from_mapping(
        value,
        source=source,
        require_physical_limits=require_physical_limits,
    )


def require_matching_profile(
    explicit: CapabilityProfile,
    actual: Mapping[str, float],
    *,
    source: str,
) -> None:
    expected = {
        **explicit.capability(),
        "memory_limit_bytes": float(explicit.memory_limit_bytes),
        "io_read_cap_mib_s": explicit.io_read_cap_mib_s,
        "io_write_cap_mib_s": explicit.io_write_cap_mib_s,
    }
    compared_keys = set(actual)
    mismatches = {
        key: {"expected": expected[key], "actual": actual.get(key)}
        for key in expected.keys() & compared_keys
        if key not in actual
        or not math.isclose(
            expected[key],
            float(actual[key]),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    }
    if mismatches:
        raise ValueError(f"{source} capability profile mismatch: {mismatches}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_direction_ranker_contract(
    direction_ranker_path: Path,
    profile: CapabilityProfile,
) -> dict[str, Any]:
    return {
        "required": True,
        "enabled": True,
        "artifact_path": str(direction_ranker_path),
        "artifact_sha256": sha256(direction_ranker_path),
        "expected_artifact_type": "real_replay_direction_ranker_group_holdout_v1",
        "expected_schema_version": 1,
        "expected_workload": "tpcc_sensitive",
        "expected_target_resource": "cpu",
        "expected_scenario": "cpu",
        "expected_capability_profile": profile.capability(),
        "admission": {
            "max_joint_pairwise_inversion_rate": 0.10,
            "min_joint_pair_count": 10,
            "min_evaluable_ranking_scopes": 2,
            "min_direction_accuracy_wilson_lower": 0.50,
            "min_joint_top1_success_wilson_lower": 0.50,
        },
        "policy": {
            "ranking_score": "joint_direction_success_probability",
            "block_estimated_ranking_when_not_ready": True,
            "block_real_replay_when_not_ready": True,
            "block_online_t_update_when_not_ready": True,
            "online_t_mutation": False,
        },
    }


def build_workload_command(
    wrapper: Path,
    measurement_root: Path,
    profile: CapabilityProfile,
) -> str:
    return (
        f"/home/anonymous/miniconda3/bin/python3 -u {wrapper} --output-root {measurement_root} "
        "--container pg5_tpcc_d2_20260722 --host 127.0.0.1 --port 59640 "
        "--database fullgrid_tpcc --user postgres --password postgres "
        f"--cpu-limit-cores {profile.cpu_cores} "
        f"--memory-limit-bytes {profile.memory_limit_bytes} "
        f"--io-total-limit-mib-s {profile.io_total_limit_mib_s} "
        f"--io-read-cap-mib-s {profile.io_read_cap_mib_s} "
        f"--io-write-cap-mib-s {profile.io_write_cap_mib_s}"
    )


def replace_strings(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: replace_strings(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_strings(item, old, new) for item in value]
    if isinstance(value, str):
        return value.replace(old, new)
    return value


def retarget(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"target_resource", "bottleneck_resource"} and item == "io":
                value[key] = "cpu"
            elif key in {"target_resources", "target_bottleneck_resources"} and item == ["io"]:
                value[key] = ["cpu"]
            else:
                retarget(item)
    elif isinstance(value, list):
        for item in value:
            retarget(item)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    profile = CapabilityProfile(
        cpu_cores=args.cpu_cap_cores,
        memory_limit_bytes=args.memory_limit_bytes,
        io_total_limit_mib_s=args.io_total_limit_mib_s,
        io_read_cap_mib_s=args.io_read_cap_mib_s,
        io_write_cap_mib_s=args.io_write_cap_mib_s,
    )
    capability_manifest = args.capability_manifest.resolve()
    direction_ranker_path = args.direction_ranker.resolve()
    require_matching_profile(
        profile,
        load_manifest_profile(
            capability_manifest,
            source="capability manifest",
            require_physical_limits=True,
        ),
        source="capability manifest",
    )
    require_matching_profile(
        profile,
        load_manifest_profile(direction_ranker_path, source="direction ranker"),
        source="direction ranker",
    )

    cfg = json.loads(SOURCE.read_text(encoding="utf-8"))
    old_tpcc = json.loads(OLD_TPCC.read_text(encoding="utf-8"))
    table_artifact = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
    lane_root = str(OUTPUT_ROOT)
    old_lane_root = (
        "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
        "hybench_xp_compute00_59630/experience_sgd/attempt11"
    )
    cfg = replace_strings(cfg, old_lane_root, lane_root)
    cfg = replace_strings(cfg, "pg5v1_hyxp_exp_sgd_attempt11", "pg5v1_tpcc_exp_sgd_attempt6")
    cfg = replace_strings(cfg, "hybench_sf10x", "fullgrid_tpcc")
    cfg = replace_strings(cfg, "hybench_xp", "tpcc")
    retarget(cfg)

    cfg["capability"] = profile.capability()
    # TPC-C uses the broad, plan-boundary-validated PostgreSQL cost ranges.
    # The previous attempt accidentally inherited a narrow cross-workload
    # range that excluded the successful TPC-C plan family.
    cfg["param_ranges"] = {
        "seq_page_cost": {"min_value": 0.1, "max_value": 100.0, "priority": 1, "discrete_count": 20},
        "random_page_cost": {"min_value": 0.1, "max_value": 100.0, "priority": 2, "discrete_count": 20},
        "cpu_tuple_cost": {"min_value": 0.001, "max_value": 0.1, "priority": 3, "discrete_count": 20},
        "cpu_index_tuple_cost": {
            "min_value": 0.0005, "max_value": 0.05, "priority": 4, "discrete_count": 20,
        },
        "cpu_operator_cost": {
            "min_value": 0.00025, "max_value": 0.025, "priority": 5, "discrete_count": 20,
        },
        "parallel_setup_cost": {
            "min_value": 0.0, "max_value": 10000.0, "priority": 6, "discrete_count": 20,
        },
        "parallel_tuple_cost": {
            "min_value": 0.001, "max_value": 1.0, "priority": 7, "discrete_count": 20,
        },
    }
    cfg["scoring"] = json.loads(
        (CONTRACT_ROOT / "common_scoring_contract.json").read_text(encoding="utf-8")
    )
    cfg["sensitivity_table"] = copy.deepcopy(table_artifact["table"])
    cfg["experience_table_contract"] = {
        "required": True,
        "disable_fallback": True,
        "fallback_enabled": False,
        "artifact_path": str(TABLE_PATH),
        "artifact_sha256": sha256(TABLE_PATH),
        "table_id": table_artifact["table_id"],
        "version": table_artifact["version"],
        "table_sha256": table_artifact["table_sha256"],
        "active_params": copy.deepcopy(table_artifact["active_params"]),
        "finite_difference_direction_enabled": False,
        "random_direction_enabled": False,
    }
    gradient = cfg["optimizer"]["gradient"]
    gradient["require_explicit_sensitivity_table"] = True
    gradient["target_bottleneck_resources"] = ["cpu"]
    gradient["subset_max_workers"] = 16
    gradient["subset_log_dir"] = str(OUTPUT_ROOT / "subset_logs")
    # a4 used eta=0.2.  Its first step landed near the useful TPC-C plan
    # family, but repeated fixed-baseline acceptances pushed cpu_tuple_cost
    # and cpu_operator_cost to roughly 8x their defaults and all three real
    # replays lost DB QPS.  Keep the requested ten-step inner loop and reduce
    # eta by 10x so the same experience direction is traversed gradually.
    gradient["eta"] = 0.02
    gradient["max_eta"] = 0.02
    gradient["max_iter"] = 10
    # A nearly flat target-resource estimate must not accept ten numerical
    # score wiggles and compound the same parameter movement.
    # The construction score is flat inside 85--90% for the target and
    # 30--50% for non-targets.  Keep T directional in that flat region, but
    # allow an equal-score inner move only when the model predicts both the
    # target bottleneck decreasing and at least one non-target increasing.
    for key in (
        "target_bottleneck_flat_band_decrease_pressure",
        "target_score_non_decrease_acceptance_enabled",
        "min_predicted_target_util_decrease",
        "min_predicted_non_target_util_increase",
    ):
        gradient.pop(key, None)
    gradient["post_convergence_explore_iter"] = 0
    gradient["adaptive_rejection_enabled"] = False
    gradient["adaptive_rejection_stop_after"] = 0
    gradient["real_replay_direction_ranker"] = build_direction_ranker_contract(
        direction_ranker_path,
        profile,
    )

    brute = cfg["optimizer"]["bruteforce"]
    brute["force_best_replay_anchor"] = False
    brute["force_best_replay_anchor_seed_records"] = []
    brute["candidate_gate"]["enabled"] = False
    brute["candidate_gate"]["target_resource"] = "cpu"
    brute["candidate_gate"]["require_predicted_bottleneck_decrease"] = False
    brute["candidate_replay_top_k"] = 10
    brute["small_loop_min_validated_candidate_count"] = 5

    metrics = copy.deepcopy(old_tpcc["metrics"])
    metrics["docker_runtime"].update(
        {
            "container": "pg5_tpcc_d2_20260722",
            "interval_sec": 2,
            "warmup_sec": 0,
            "memory_mode": "working_set",
            "io_from_prometheus": False,
            "io_scope": "cgroup",
        }
    )
    retraining = metrics["plan_estimator"]["model_retraining"]
    retraining["run_id"] = "pg5v1_tpcc_exp_sgd_attempt6"
    retraining["output_dir"] = str(OUTPUT_ROOT / "resource_models")
    # A static single-query plan formula is not an admissible CPU direction
    # model for the 32-terminal TPC-C workload.  New attempts must retrain and
    # pass held-out evidence before all 127 subsets or any real replay starts.
    retraining["quality_gate"].update(
        {
            "enabled": True,
            "required_resources": ["cpu"],
            "optional_resources": [],
            "fail_on_optional_resource": True,
            "min_test_samples": 5,
            "selected_model_only": True,
            "enforce_test_relative_error_p95": True,
            "enforce_test_relative_error_max": True,
            "enforce_test_r2": False,
            "enforce_test_pairwise_ranking": True,
            "require_grouped_holdout": True,
            "max_test_pairwise_inversion_rate": 0.10,
            "min_test_pairwise_pair_count": 10,
        }
    )
    metrics["plan_estimator"]["direction_validity_gate"] = {
        "enabled": True,
        "target_resource": "cpu",
        "require_baseline_sanity": True,
        "require_calibrated_anchor": True,
        "require_baseline_correction_success": True,
        "reject_online_correction_saturation": True,
        "max_baseline_relative_error": 0.50,
        "threshold_epsilon": 1e-6,
    }
    cfg["metrics"] = metrics

    cfg["postgres"] = {
        "host": "127.0.0.1",
        "port": 59640,
        "user": "postgres",
        "dbname": "fullgrid_tpcc",
    }
    measurement_root = OUTPUT_ROOT / "candidate_measurements"
    wrapper = ROOT / "shell/run_tpcc_sensitive_stable_point.py"
    cfg["benchmark"] = {
        "workload_cmd": build_workload_command(wrapper, measurement_root, profile),
        "workload_timeout_sec": 720,
        "hybench_statement_timeout_ms": 300000,
        "baseline_only": False,
        "warmup_cmd": "true",
        "warmup_enabled": False,
        "warmup_timeout_sec": 100,
        "validation_warmup_cmd": "true",
        "validation_warmup_enabled": False,
        "validation_warmup_required": False,
        "validation_warmup_timeout_sec": 100,
        "validation_warmup_drain_timeout_sec": 90,
        "validation_warmup_drain_poll_sec": 2,
        "validation_warmup_drain_max_app_connections": 0,
        "validation_warmup_drain_application_name": "resource-adaptive-pg5-tpcc",
        "hybench_statement_sql_files": [],
        "hybench_setup_sql_files": [],
    }
    cfg["pre_benchmark_recovery"] = {
        "enabled": True,
        "required": True,
        "target_db": "fullgrid_tpcc",
        "sql": [],
        "snapshot_restore": {
            "enabled": True,
            "required": True,
            "only_for_tp_workloads": False,
            "runner": "local",
            "command": str(ROOT / "shell/restore_pg5_tpcc_from_template.sh"),
            "timeout_sec": 1800,
            "tags": ["baseline", "final", "feedback"],
            "template_db": "template_fullgrid_tpcc_pg5",
            "policy": "Restore the owned immutable TPC-C template before every real point.",
        },
        "restart_db": {"enabled": False, "tags": ["baseline", "final", "feedback"]},
        "vm_drop_cache": {"enabled": False, "required": False, "tags": []},
    }
    cfg["post_benchmark_cleanup"].update(
        {
            "target_db": "fullgrid_tpcc",
            "ready_db": "fullgrid_tpcc",
            "ready_host": "127.0.0.1",
            "ready_port": "59640",
        }
    )

    feedback = cfg["final_evaluation"]["feedback_loop"]
    feedback["target_resources"] = ["cpu"]
    target_relative_ranges = cfg["scoring"][
        "target_relative_resource_target_ranges"
    ]
    feedback["resource_target_ranges"] = {
        resource_name: {
            **copy.deepcopy(
                target_relative_ranges[
                    "target_bottleneck"
                    if resource_name == "cpu"
                    else "non_bottleneck"
                ]
            ),
            "target_pct": 87.5 if resource_name == "cpu" else 40.0,
        }
        for resource_name in ("cpu", "memory", "io")
    }
    feedback["target_util_pct"] = 87.5
    feedback["tolerance_pct"] = 2.5
    feedback["overload_util_pct"] = 95.0
    feedback["hard_overload_util_pct"] = 95.0
    feedback["non_target_target_util_pct"] = 40.0
    feedback["non_target_overload_util_pct"] = 90.0
    feedback["single_bottleneck_protection"] = False
    feedback["performance_guard_enabled"] = False
    feedback["replay_candidate_pruning"] = {
        "enabled": False,
        "apply_to_initial_topk": False,
        "apply_to_feedback_topk": False,
        "db_qps_proxy_pruning_enabled": False,
        "description": "Fail-open until a calibrated one-sided error bound exists.",
    }
    feedback["resource_iteration_goal"]["enabled"] = False
    feedback["resource_iteration_goal"]["bottleneck_resource"] = "cpu"
    cfg["baseline_resource_band_gate"] = {
        "enabled": True,
        "hard_fail": True,
        "target_resource": "cpu",
        "ranges_pct": {
            "cpu": {"min_pct": 85.0, "max_pct": 95.0},
            "memory": {"min_pct": 30.0, "max_pct": 50.0},
            "io": {"min_pct": 30.0, "max_pct": 50.0},
        },
        "threshold_semantics": "target inclusive 85-95%; non-target inclusive 30-50%",
        "stage": "after_stable_baseline_before_retrain_and_candidate_search",
        "does_not_affect_outer_acceptance": True,
    }
    cfg["bottleneck_calibration"].update(
        {"enabled": False, "resources": "cpu", "min_util_pct": 85.0, "target_util_pct": 90.0}
    )
    cfg["formal_pg_resource_semantics"] = {
        "enabled": True,
        "semantics": "pg5_io_total_throughput_utilization_v1",
        "io_formula": "(read_bps + write_bps) / (read_cap_bps + write_cap_bps)",
        "raw_sample_range": "finite_and_nonnegative; values above one are overload; preserve without clipping",
        "baseline_candidate_final_same_adapter": True,
        "persist_per_sample": [
            "read_bps", "write_bps", "read_cap_bps", "write_cap_bps",
            "io_total_cap_bps", "io_utilization",
        ],
    }
    cfg["logging"]["history_path"] = str(OUTPUT_ROOT / "history/tpcc_optimizer_history.jsonl")
    cfg["resource_iteration_reports"]["output_dir"] = str(OUTPUT_ROOT / "resource_reports")
    cfg["plan_capture"]["output_dir"] = str(OUTPUT_ROOT / "plan_captures")
    cfg["auto_explain"]["output_dir"] = str(OUTPUT_ROOT / "auto_explain")

    stability = json.loads((CONTRACT_ROOT / "stability_gate_contract_v5.json").read_text())
    cfg["_campaign"] = {
        "campaign_id": CAMPAIGN,
        "lane_id": "tpcc_local_disk2_59640",
        "method": "experience_table_all_subset_sgd",
        "attempt_tag": "attempt6",
        "background_load_allowed": True,
        "formal_start_requires_shared_baseline_stable": True,
        "shared_baseline": str(
            Path("/disk2/resource_adaptive") / CAMPAIGN / "tpcc_attempt7/"
            "baseline_r0_online_attempt7/points/point_20260722_175019_3035025/"
            "stability_reassessment_v2.json"
        ),
        "candidate_protocol": {
            "warmup_seconds": 60,
            "measurement_seconds_min": 180,
            "initial_probe_seconds": 180,
            "measurement_seconds_max": 600,
            "window_seconds": 30,
            "online_consecutive_windows": 6,
            "coverage_min": 0.9,
            "cross_window_cv_max": 0.1,
            "throughput_fields_required_for_stability": False,
            "stability_fields": ["cpu_utilization"],
        },
        "built_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    cfg["_candidate_measurement_protocol"] = {
        "wrapper": str(wrapper),
        "wrapper_sha256": sha256(wrapper),
        "measurement_evidence_root": str(measurement_root),
        "warmup_is_outside_db_and_resource_measurement": True,
        "warmup_seconds": 60,
        "measurement_seconds_min": 180,
        "initial_probe_seconds": 180,
        "measurement_seconds_max": 600,
        "window_seconds": 30,
        "online_last_n_windows": 6,
        "stability": stability,
        "continue_after_probe_when_unstable": True,
    }
    cfg["_physical_resource_contract"] = {
        "container": "pg5_tpcc_d2_20260722",
        "cpu_quota_cores": profile.cpu_cores,
        "memory_bytes": profile.memory_limit_bytes,
        "io_device": "/dev/nvme3n1",
        "io_device_major_minor": "259:6",
        "io_read_bps": profile.io_bps(profile.io_read_cap_mib_s),
        "io_write_bps": profile.io_bps(profile.io_write_cap_mib_s),
        "io_total_bps": profile.io_bps(profile.io_total_limit_mib_s),
        "io_semantics": "(read_bps+write_bps)/(io_read_bps+io_write_bps)",
        "snapshot_restore_wrapper": str(ROOT / "shell/restore_pg5_tpcc_from_template.sh"),
        "snapshot_restore_wrapper_sha256": sha256(ROOT / "shell/restore_pg5_tpcc_from_template.sh"),
    }
    cfg["_tpcc_attempt6_contract"] = {
        "experience_table_only": True,
        "tpcc_specific_experience_table": "pg5_tpcc_cpu_experience_table_T_v3_semantic_prior",
        "tpcc_experience_table_formal_admission": False,
        "semantic_prior_path": str(
            TABLE_PATH
        ),
        "launch_blocked_until_ranker_and_promoted_table_ready": True,
        "range_source": "pg_tpcc_sensitive_frozen_bounds_validation_20260722/plan_scan.json",
        "historical_candidate_coordinates_injected": False,
        "all_nonempty_subsets": 127,
        "subset_parallelism": 16,
        "eta": 0.02,
        "max_eta": 0.02,
        "eta_change_from_attempt4": {
            "old": 0.2,
            "new": 0.02,
            "reason": "a4 three stable real replays all reduced db_qps after ten-step parameter overshoot",
            "coordinates_injected": False,
        },
        "real_replay_policy": "first5_then_expand_to10",
        "accepted_interval_shrink_target": 10,
        "finite_difference_sgd": False,
        "random_direction_sgd": False,
        "replay_pruning": "disabled_fail_open_without_calibrated_one_sided_bound",
        "r0_recalibration_from_attempt5": {
            "attempt5_cpu_utilization": 0.8419321588985061,
            "attempt5_cpu_quota_cores": 4.0,
            "attempt5_raw_cpu_cores": 3.3677286355940244,
            "target_cpu_utilization": 0.875,
            "calculated_cpu_quota_cores": 3.8488327263931707,
            "selected_cpu_quota_cores": profile.cpu_cores,
            "reason": "attempt5 was stable but below the formal 85% CPU construction floor",
        },
    }
    cfg["_experience_table_step_envelope"] = {
        "contract_id": "pg5_experience_table_first_step_envelope_v1",
        "required_before_launch": True,
        "resource_evidence": str(ATTEMPT5_BASELINE),
        "resource_evidence_sha256": sha256(ATTEMPT5_BASELINE),
        "resource_evidence_capability": {"cpu": 4.0, "memory": 5.0, "io": 54.0},
        "eta": 0.02,
        "max_allowed_relative_first_step": 0.2,
        "maximum_absolute_direction": 2.951199392976048,
        "maximum_relative_first_step": 0.05902398785952096,
        "all_first_step_values_inside_ranges": True,
        "expected_directions": {
            "seq_page_cost": -1.4768475318878445,
            "random_page_cost": -2.9131988558923507,
            "cpu_tuple_cost": 2.9195322787396334,
            "cpu_index_tuple_cost": -0.7676894552288773,
            "cpu_operator_cost": 2.9195322787396334,
            "parallel_setup_cost": -1.4743518610882038,
            "parallel_tuple_cost": -2.951199392976048,
        },
        "expected_unclipped_first_step": {
            "seq_page_cost": 0.9704630493622431,
            "random_page_cost": 3.766944091528612,
            "cpu_tuple_cost": 0.010583906455747928,
            "cpu_index_tuple_cost": 0.004923231054477112,
            "cpu_operator_cost": 0.002645976613936982,
            "parallel_setup_cost": 970.512962778236,
            "parallel_tuple_cost": 0.09409760121404791,
        },
    }
    cfg["_capability_profile_binding"] = {
        "required_before_launch": True,
        "manifest_path": str(capability_manifest),
        "manifest_sha256": sha256(capability_manifest),
        "explicit_profile": profile.capability(),
        "memory_limit_bytes": profile.memory_limit_bytes,
        "io_read_cap_mib_s": profile.io_read_cap_mib_s,
        "io_write_cap_mib_s": profile.io_write_cap_mib_s,
        "direction_ranker_path": str(direction_ranker_path),
        "direction_ranker_sha256": sha256(direction_ranker_path),
        "direction_ranker_required": True,
    }
    cfg.pop("_attempt11_contract", None)
    cfg.pop("_tpcc_attempt1_contract", None)
    cfg.pop("_tpcc_attempt2_contract", None)
    cfg.pop("_tpcc_attempt3_contract", None)
    cfg.pop("_tpcc_attempt4_contract", None)
    cfg.pop("_tpcc_attempt5_contract", None)
    cfg.pop("_generated_from", None)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    for path in (
        OUTPUT_ROOT / "history", OUTPUT_ROOT / "subset_logs", measurement_root,
        OUTPUT_ROOT / "resource_reports", OUTPUT_ROOT / "plan_captures",
        OUTPUT_ROOT / "auto_explain", OUTPUT_ROOT / "logs",
    ):
        path.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT}")
    print(f"sha256={sha256(OUTPUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
