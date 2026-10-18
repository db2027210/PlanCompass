#!/usr/bin/env python3
"""Fail-closed JOB/TPC-H debug-to-formal10 promotion.

The default operation is a pure audit.  ``--execute`` publishes a
content-addressed formal10 runtime only after the exact fresh debug evidence
chain passes.  The tool never launches a workload or contacts MySQL.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
BUILDER = Path(__file__).resolve()
CONTRACT = ROOT / "preflight/mysql_algorithm_c_formal10_contract_20260729.json"
CONTRACT_SHA256 = (
    "2f3d7775e005a31dc5b950277ec5153c2d4b76b67ca7bb42a4f52b5026ae5641"
)
REQUIRED_GRADIENT_FIELDS = (
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
)
EXPECTED_RANKS = [1, 2, 3, 4, 5]

SCENARIOS: dict[str, dict[str, Any]] = {
    "job": {
        "debug_id":
            "b888870bc6ef136932c26676c797823d2b99858cf6b2b3891f7afe1ef42d3190",
        "debug_root": ROOT / "artifacts/mysql_job_v30_debug",
        "formal_root": ROOT / "artifacts/mysql_job_formal10_v1",
        "scenario": "job_memory",
        "benchmark": "job",
        "target_resource": "memory",
        "service_factory": "shell.mysql_algorithm_c_local_services_v28:build",
        "driver": "shell/debug_mysql_gradient_round_v25.py",
    },
    "tpch": {
        "debug_id":
            "1332ab2e571e981761fc957cd65c36ff6e782663eb70ba4c8952ddc815f14dc4",
        "debug_root": ROOT / "artifacts/mysql_tpch_v36_debug",
        "formal_root": ROOT / "artifacts/mysql_tpch_formal10_v1",
        "scenario": "tpch_memory",
        "benchmark": "tpch",
        "target_resource": "memory",
        "service_factory":
            "shell.mysql_tpch_algorithm_c_local_services_v27:build",
        "driver": "shell/debug_mysql_gradient_round_v25.py",
    },
}


class PromotionError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def compact_canonical(value: Any, *, newline: bool = False) -> bytes:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return payload + (b"\n" if newline else b"")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_absolute():
        raise PromotionError(f"{label} path must be absolute: {path}")
    if not path.is_file() or path.is_symlink():
        raise PromotionError(f"{label} is absent or symlinked: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PromotionError(f"{label} is invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PromotionError(f"{label} root is not an object: {path}")
    return value


def resolve_inside(path: Path, root: Path, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise PromotionError(f"{label} must be absolute and non-symlink")
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise PromotionError(f"{label} escapes frozen root: {resolved}") from exc
    return resolved


def verify_sidecar(path: Path, expected: str, label: str) -> str:
    if len(expected) != 64 or sha(path) != expected:
        raise PromotionError(f"{label} pointer checksum differs")
    sidecar = path.with_name(path.name + ".sha256")
    if (
        not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii")
        != f"{expected}  {path.name}\n"
    ):
        raise PromotionError(f"{label} exact SHA-256 sidecar differs")
    return expected


def pointer_path(
    pointer: Any, root: Path, label: str, *, sidecar: bool = True,
) -> Path:
    if not isinstance(pointer, Mapping):
        raise PromotionError(f"{label} pointer is absent")
    path = resolve_inside(Path(str(pointer.get("path") or "")), root, label)
    if not path.is_file():
        raise PromotionError(f"{label} file is absent")
    expected = str(pointer.get("sha256") or "").lower()
    if sidecar:
        verify_sidecar(path, expected, label)
    elif len(expected) != 64 or sha(path) != expected:
        raise PromotionError(f"{label} pointer checksum differs")
    return path


def finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PromotionError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise PromotionError(f"{label} must be finite")
    return result


def _close(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12)


def expected_debug_run_config(
    base_config: Mapping[str, Any], output_root: Path,
) -> dict[str, Any]:
    """Reconstruct the exact config emitted by debug_mysql_gradient_round_v25."""
    output = output_root.resolve()
    base_output = Path(
        str((base_config.get("output") or {}).get("directory") or "")
    )
    if (
        not base_output.is_absolute()
        or output.parent != base_output.resolve()
        or not re.fullmatch(r"\d{8}T\d{6}Z", output.name)
    ):
        raise PromotionError("debug run directory is outside its frozen root")
    debug = copy.deepcopy(dict(base_config))
    debug["measurement_seconds"] = 600
    debug["minimum_measurement_seconds"] = 180
    debug.setdefault("output", {})["directory"] = str(output)
    optimizer = debug.setdefault("optimizer", {})
    optimizer["original_gradient_only"] = True
    optimizer["round_index"] = 1
    optimizer["boundary_aware_eta_enabled"] = True
    optimizer["boundary_eta_growth_factor"] = 2.0
    optimizer["plateau_exploration_enabled"] = False
    optimizer["min_param_delta_rel_pct"] = 10.0
    optimizer["max_param_delta_rel_pct"] = 32.0
    optimizer["plateau_score_epsilon"] = 1e-12
    outer = debug.setdefault("formal_outer_protocol", {})
    outer["max_outer_attempts"] = 1
    outer["accepted_shrink_count"] = 0
    outer["rejected_count"] = 0
    outer["formal_checkpoint_path"] = str(
        output / "formal_outer_checkpoint.json"
    )
    outer["formal_real_r0_checkpoint_path"] = str(
        output / "formal_real_r0.json"
    )
    outer["formal_point_journal_root"] = str(output / "point_journals")
    debug["exp_id"] = (
        f"{base_config.get('exp_id', 'mysql_algorithm_c')}"
        f".gradient_debug.{output.name}"
    )
    debug["debug_contract"] = {
        "schema_version": "mysql_original_gradient_one_outer_round_v2",
        "outer_attempt_cap": 1,
        "all_nonempty_subsets_required": True,
        "fixed_real_replay_top_k": 5,
        "real_replay_timing": {
            "warmup_seconds": 60,
            "minimum_measurement_seconds": 180,
            "decision_step_seconds": 30,
            "maximum_measurement_seconds": 600,
            "stop_policy":
                "latest_six_30_second_resource_windows_stable",
        },
        "original_gradient_only": True,
        "qps_role": "outer_acceptance_only",
    }
    return debug


def validate_debug_run_config(
    base_config: Mapping[str, Any],
    run_config: Mapping[str, Any],
    output_root: Path,
) -> None:
    if dict(run_config) != expected_debug_run_config(
        base_config, output_root
    ):
        raise PromotionError("debug run config is not the frozen derived config")


def _confidence_for(
    confidence: Any, parameter: str, resource: str, label: str,
) -> float:
    if isinstance(confidence, (int, float)) and not isinstance(
        confidence, bool
    ):
        return finite(confidence, label)
    if not isinstance(confidence, Mapping):
        raise PromotionError(f"{label} is absent")
    parameter_value = confidence.get(parameter)
    if isinstance(parameter_value, Mapping):
        return finite(parameter_value.get(resource), label)
    if parameter_value is not None:
        return finite(parameter_value, label)
    return finite(confidence.get(resource), label)


def _dscore_for(
    dscore: Mapping[str, Any], parameter: str, resource: str, label: str,
) -> float:
    parameter_value = dscore.get(parameter)
    if isinstance(parameter_value, Mapping):
        return finite(parameter_value.get(resource), label)
    return finite(dscore.get(resource), label)


def verify_gradient_event_numerics(
    gradient: Mapping[str, Any], label: str,
) -> None:
    t_matrix = gradient.get("T")
    dscore = gradient.get("dScore/dU")
    confidence = gradient.get("confidence")
    gradients = gradient.get("g_j")
    deltas = gradient.get("delta_param")
    if (
        not isinstance(t_matrix, Mapping)
        or not isinstance(dscore, Mapping)
        or not isinstance(gradients, Mapping)
        or not gradients
        or not isinstance(deltas, Mapping)
    ):
        raise PromotionError(f"{label} gradient maps are incomplete")
    eta = finite(gradient.get("eta"), f"{label}.eta")
    if eta <= 0.0:
        raise PromotionError(f"{label}.eta must be positive")
    if set(gradients) != set(t_matrix) or set(deltas) != set(gradients):
        raise PromotionError(f"{label} gradient parameter sets differ")
    candidate_params = gradient.get("candidate_params")
    if candidate_params is not None and not isinstance(
        candidate_params, Mapping
    ):
        raise PromotionError(f"{label}.candidate_params is malformed")
    for parameter in sorted(gradients):
        sensitivities = t_matrix.get(parameter)
        if not isinstance(sensitivities, Mapping) or not sensitivities:
            raise PromotionError(f"{label}.T.{parameter} is incomplete")
        expected_gradient = 0.0
        for resource, sensitivity_value in sorted(
            sensitivities.items()
        ):
            sensitivity = finite(
                sensitivity_value, f"{label}.T.{parameter}.{resource}"
            )
            score_slope = _dscore_for(
                dscore,
                str(parameter),
                str(resource),
                f"{label}.dScore/dU.{parameter}.{resource}",
            )
            confidence_value = _confidence_for(
                confidence,
                str(parameter),
                str(resource),
                f"{label}.confidence.{parameter}.{resource}",
            )
            expected_gradient += (
                score_slope * sensitivity * confidence_value
            )
        observed_gradient = finite(
            gradients.get(parameter), f"{label}.g_j.{parameter}"
        )
        if not _close(observed_gradient, expected_gradient):
            raise PromotionError(
                f"{label}.g_j.{parameter} is not "
                "sum(dScore/dU*T*confidence)"
            )
        delta = deltas.get(parameter)
        if not isinstance(delta, Mapping):
            raise PromotionError(
                f"{label}.delta_param.{parameter} is not auditable"
            )
        before = finite(
            delta.get("before"), f"{label}.delta_param.{parameter}.before"
        )
        proposed = finite(
            delta.get("proposed_before_clip"),
            f"{label}.delta_param.{parameter}.proposed_before_clip",
        )
        after = finite(
            delta.get("after_clip"),
            f"{label}.delta_param.{parameter}.after_clip",
        )
        relative = finite(
            delta.get("relative"),
            f"{label}.delta_param.{parameter}.relative",
        )
        absolute = finite(
            delta.get("absolute"),
            f"{label}.delta_param.{parameter}.absolute",
        )
        expected_relative = eta * observed_gradient
        expected_proposed = before * (1.0 + expected_relative)
        if (
            not _close(relative, expected_relative)
            or not _close(proposed, expected_proposed)
            or not _close(absolute, after - before)
        ):
            raise PromotionError(
                f"{label}.delta_param.{parameter} is not derived "
                "from eta and g_j"
            )
        clipped = delta.get("clipped")
        if type(clipped) is not bool:
            raise PromotionError(
                f"{label}.delta_param.{parameter}.clipped is not boolean"
            )
        if not clipped and not _close(after, proposed):
            raise PromotionError(
                f"{label}.delta_param.{parameter} changed without clipping"
            )
        if isinstance(candidate_params, Mapping) and not _close(
            finite(
                candidate_params.get(parameter),
                f"{label}.candidate_params.{parameter}",
            ),
            after,
        ):
            raise PromotionError(
                f"{label}.candidate_params.{parameter} differs from delta"
            )


def verify_runtime_journal(
    journal_root: Path, token: str, label: str,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    if (
        not token
        or "/" in token
        or token in {".", ".."}
    ):
        raise PromotionError(f"{label} token is invalid")
    attempt = resolve_inside(journal_root / token, journal_root, label)
    if not attempt.is_dir() or attempt.is_symlink():
        raise PromotionError(f"{label} directory is absent")
    histories = sorted(attempt.glob("[0-9][0-9][0-9][0-9]_*.json"))
    if not histories:
        raise PromotionError(f"{label} checkpoint history is absent")
    previous: str | None = None
    initial: dict[str, Any] | None = None
    last: dict[str, Any] | None = None
    for path in histories:
        if path.is_symlink():
            raise PromotionError(f"{label} history is symlinked")
        payload = path.read_bytes()
        digest = sha_bytes(payload)
        sidecar = path.with_name(path.name + ".sha256")
        if (
            not sidecar.is_file()
            or sidecar.is_symlink()
            or sidecar.read_text(encoding="ascii") != f"{digest}\n"
        ):
            raise PromotionError(f"{label} history checksum differs")
        document = json.loads(payload)
        if (
            not isinstance(document, dict)
            or document.get("resume_token") != token
            or document.get("accounting_token") != token
            or document.get("previous_sha256") != previous
        ):
            raise PromotionError(f"{label} history chain differs")
        if initial is None:
            initial = document
        last = document
        previous = digest
    current_path = attempt / "current.json"
    current_payload = current_path.read_bytes()
    current_digest = sha_bytes(current_payload)
    current_sidecar = attempt / "current.json.sha256"
    if (
        current_path.is_symlink()
        or current_digest != previous
        or not current_sidecar.is_file()
        or current_sidecar.is_symlink()
        or current_sidecar.read_text(encoding="ascii")
        != f"{current_digest}\n"
        or json.loads(current_payload) != last
        or initial is None
        or last is None
    ):
        raise PromotionError(f"{label} current checkpoint differs")
    return current_path.resolve(), initial, last


def verify_promotion_seal(evidence_path: Path) -> tuple[Path, str]:
    seal_root = evidence_path.resolve().parent
    closure_path = seal_root / "seal_closure.json"
    closure = load_object(closure_path.resolve(), "promotion seal closure")
    if (
        closure.get("schema_version")
        != "mysql_algorithm_c_promotion_seal_closure_v1"
        or closure.get("seal_id") != seal_root.name
        or closure.get("transactional_publication") is not True
        or closure.get("idempotent") is not True
        or closure.get("immutable") is not True
        or not isinstance(closure.get("source_authority"), Mapping)
        or not isinstance(closure.get("files"), Mapping)
    ):
        raise PromotionError("promotion seal closure authority differs")
    observed = {
        str(path.relative_to(seal_root)): sha(path)
        for path in seal_root.rglob("*")
        if path.is_file() and path != closure_path
    }
    if observed != closure["files"]:
        raise PromotionError("promotion seal closure files differ")
    if (
        closure["files"].get("promotion_evidence_manifest.json")
        != sha(evidence_path)
    ):
        raise PromotionError("promotion evidence is outside its seal closure")
    return seal_root, sha(closure_path)


def verify_contract() -> dict[str, Any]:
    contract = load_object(CONTRACT, "formal10 contract")
    if sha(CONTRACT) != CONTRACT_SHA256:
        raise PromotionError("formal10 contract checksum differs")
    expected = {
        ("schema_version",):
            "mysql_algorithm_c_formal10_promotion_contract_v1",
        ("formal_scope",): "mysql_only",
        ("cross_engine_comparison_prohibited",): True,
        ("current_evidence_may_not_authorize_formal10",): True,
        ("algorithm", "original_gradient_only"): True,
        ("algorithm", "method"): "experience_table_all_subset_sgd",
        ("algorithm", "gradient_equation"):
            "g_j = sum_r(dScore_dU_r * T_j_r * confidence)",
        ("algorithm", "candidate_score_qps_input_prohibited"): True,
        ("algorithm", "qps_usage"):
            "outer_acceptance_after_real_replay_only",
        ("candidate_space", "enumeration"): "all_nonempty_subsets",
        ("candidate_space", "ranking"): "global_final_candidate_score",
        ("candidate_space", "sealed_replay_top_k"): 5,
        ("candidate_space", "replay_set_must_remain_fixed"): True,
        ("formal10", "target_accepted_rounds"): 10,
        ("formal10", "initial_accepted_rounds"): 0,
        ("measurement", "warmup_seconds"): 60,
        ("measurement", "minimum_measurement_seconds"): 180,
        ("measurement", "maximum_measurement_seconds"): 600,
        ("measurement", "decision_step_seconds"): 30,
        ("measurement", "stability_decision_window_seconds"): 180,
        ("measurement", "stop_at_first_stable_valid_boundary"): True,
        ("measurement", "database_counter_minimum_coverage_ratio"): 0.9,
        ("measurement", "resource_sample_minimum_coverage_ratio"): 0.9,
        ("measurement", "missing_or_timed_out_sampling_action"):
            "FAIL_CLOSED",
        ("debug_to_formal10_promotion", "fresh_in_band_r0_status"):
            "PASS",
        ("debug_to_formal10_promotion",
         "historical_or_capture_r0_prohibited"): True,
        ("debug_to_formal10_promotion", "all_subsets_completed"): True,
        ("debug_to_formal10_promotion",
         "all_fixed_top5_real_replays_completed"): True,
        ("debug_to_formal10_promotion",
         "prediction_vs_real_ranking_audit_status"): "PASS",
        ("debug_to_formal10_promotion",
         "started_without_point_journal_action"): "FAIL_CLOSED_NO_RERUN",
        ("debug_to_formal10_promotion",
         "attempt_token_mismatch_action"): "FAIL_CLOSED",
        ("debug_to_formal10_promotion",
         "bundle_or_device_authority_mismatch_action"): "FAIL_CLOSED",
        ("fingerprint", "missing_any_level_action"):
            "KEEP_AS_INDEPENDENT_CANDIDATE",
        ("publication", "content_addressed"): True,
        ("publication", "transactional"): True,
        ("publication", "idempotent"): True,
        ("publication", "immutable_after_publish"): True,
        ("publication", "all_executable_inputs_manifest_bound"): True,
    }
    for keys, wanted in expected.items():
        current: Any = contract
        for key in keys:
            current = current.get(key) if isinstance(current, Mapping) else None
        if current != wanted:
            raise PromotionError(
                f"formal10 contract differs at {'.'.join(keys)}"
            )
    required_increment = contract["formal10"]["accepted_increment_requires"]
    if required_increment != [
        "fresh_runtime_gate_pass",
        "complete_fixed_top5_real_replay",
        "outer_qps_acceptance_pass",
        "complete_l1_l2_l3_evidence",
        "atomic_checkpoint_commit",
    ]:
        raise PromotionError("formal10 accepted increment boundary differs")
    exact_lists = {
        ("algorithm", "parameter_update_must_be_derived_from"):
            ["g_j", "eta"],
        ("algorithm", "required_gradient_event_fields"):
            list(REQUIRED_GRADIENT_FIELDS),
        ("fingerprint", "l1_required"): [
            "operator_types", "join_order", "scan_types",
            "parallel_structure",
        ],
        ("fingerprint", "l2_required"): [
            "workers", "hash_batches", "sort_or_spill",
            "bitmap_lossy_pages", "intermediate_result_sizes",
        ],
        ("fingerprint", "l3_required_by_scenario", "job"): [
            "memory_peak", "dynamic_execution_memory", "spill",
        ],
        ("fingerprint", "l3_required_by_scenario", "tpch"): [
            "memory_peak", "dynamic_execution_memory", "spill",
        ],
        ("fingerprint", "duplicate_requires"): [
            "identical_parameter_signature", "identical_l1",
            "l2_within_frozen_tolerance", "l3_within_frozen_tolerance",
        ],
        ("debug_to_formal10_promotion", "sealed_ranks"): EXPECTED_RANKS,
        ("debug_to_formal10_promotion", "per_rank_authority_required"): [
            "sealed_candidate_identity", "point_journal", "completion",
            "measurement", "attempt_token",
        ],
        ("formal10", "never_count_as_accepted"): [
            "capture", "r0", "candidate_generation", "failed_replay",
            "preparation", "debug_round", "failed_gate",
        ],
    }
    for keys, wanted in exact_lists.items():
        current: Any = contract
        for key in keys:
            current = current.get(key) if isinstance(current, Mapping) else None
        if current != wanted:
            raise PromotionError(
                f"formal10 contract differs at {'.'.join(keys)}"
            )
    for scenario in ("job", "tpch"):
        if contract["candidate_space"].get(scenario) != {
            "parameter_count": 4,
            "nonempty_subset_count": 15,
        }:
            raise PromotionError(
                f"formal10 contract candidate space differs for {scenario}"
            )
    return contract


def validate_debug_bundle(
    name: str,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    spec = SCENARIOS[name]
    bundle = Path(spec["debug_root"]) / str(spec["debug_id"])
    manifest = load_object(bundle / "bundle_manifest.json", "debug manifest")
    if (
        manifest.get("bundle_id") != spec["debug_id"]
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("formal10_contract_sha256") != CONTRACT_SHA256
        or manifest.get("candidate_count") != 15
        or manifest.get("parameter_count") != 4
        or manifest.get("fixed_top_k_real_replay") != 5
        or manifest.get("original_gradient_only") is not True
        or manifest.get("sampling_protocol", {}).get(
            "minimum_effective_coverage_ratio"
        )
        != 0.9
    ):
        raise PromotionError("debug successor authority differs")
    for relative, expected in manifest.get("files", {}).items():
        path = bundle / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or sha(path) != expected
        ):
            raise PromotionError(
                f"debug successor file differs: {relative}"
            )
    config = load_object(bundle / "config.json", "debug config")
    if (
        config.get("accepted_round_authorized") is not False
        or config.get("formal_outer_protocol", {}).get("max_outer_attempts")
        != 1
        or config.get("execution_resource_authority")
        != manifest.get("execution_resource_authority")
        or config.get("execution_storage_authority")
        != manifest.get("execution_storage_authority")
    ):
        raise PromotionError("debug config authority differs")
    readiness_paths = list(bundle.glob("readiness_manifest-*.json"))
    if len(readiness_paths) != 1:
        raise PromotionError("debug readiness cardinality differs")
    source = Path(str(manifest.get("source_directory") or ""))
    source_manifest = load_object(
        source / "source_manifest.json", "debug source manifest"
    )
    if (
        source_manifest.get("bundle_id") != manifest.get("source_bundle_id")
        or sha(source / "source_manifest.json")
        != manifest.get("source_manifest_sha256")
    ):
        raise PromotionError("debug source authority differs")
    source_files = source_manifest.get("files")
    if not isinstance(source_files, Mapping) or not source_files:
        raise PromotionError("debug source closure is absent")
    for relative, expected in source_files.items():
        path = resolve_inside(
            source / str(relative), source, f"debug source {relative}"
        )
        if (
            not path.is_file()
            or path.is_symlink()
            or sha(path) != expected
        ):
            raise PromotionError(
                f"debug source closure differs: {relative}"
            )
    return manifest, config, readiness_paths[0], source


def candidate_identity(candidate: Mapping[str, Any]) -> dict[str, Any]:
    rank = candidate.get("rank")
    params = candidate.get("optimized_params")
    if (
        isinstance(rank, bool)
        or not isinstance(rank, int)
        or not isinstance(params, Mapping)
        or not params
    ):
        raise PromotionError("started candidate identity is incomplete")
    encoded = {
        str(name): finite(value, f"candidate param {name}").hex()
        for name, value in sorted(params.items())
    }
    return {
        "rank": rank,
        "subset_id": candidate.get("subset_id"),
        "strict_plan_cluster_key": candidate.get(
            "strict_plan_cluster_key"
        ),
        "canonical_plan_bundle_sha256": (
            (candidate.get("canonical_plan_bundle") or {}).get(
                "canonical_plan_bundle_sha256"
            )
            if isinstance(candidate.get("canonical_plan_bundle"), Mapping)
            else (
                ((candidate.get("final_estimation_metadata") or {}).get(
                    "canonical_plan_bundle"
                ) or {}).get("canonical_plan_bundle_sha256")
                if isinstance(
                    candidate.get("final_estimation_metadata"), Mapping
                )
                else None
            )
        ),
        "optimized_params_float_hex": encoded,
    }


def verify_gradient_events(checkpoint: Mapping[str, Any]) -> int:
    results = checkpoint.get("completed_subset_results")
    completed = checkpoint.get("completed_subset_ids")
    if not isinstance(results, Mapping) or not isinstance(completed, list):
        raise PromotionError("completed subset results are absent")
    event_count = 0
    for subset_id in completed:
        result = results.get(subset_id)
        history = (
            ((result or {}).get("optimization_result") or {}).get("history")
            if isinstance(result, Mapping)
            else None
        )
        if not isinstance(history, list) or not history:
            raise PromotionError(
                f"subset {subset_id} lacks gradient history"
            )
        for row in history:
            gradient = (
                row.get("gradient_debug")
                if isinstance(row, Mapping)
                else None
            )
            if not isinstance(gradient, Mapping):
                raise PromotionError(
                    f"subset {subset_id} lacks gradient_debug"
                )
            missing = [
                key for key in REQUIRED_GRADIENT_FIELDS
                if key not in gradient
            ]
            if missing:
                raise PromotionError(
                    f"subset {subset_id} gradient event misses {missing}"
                )
            if (
                gradient.get("gradient_source")
                != "original_dScore_dU_times_T_times_confidence"
                or not isinstance(gradient.get("g_j"), Mapping)
                or not gradient["g_j"]
            ):
                raise PromotionError(
                    f"subset {subset_id} uses non-original gradient"
                )
            verify_gradient_event_numerics(
                gradient,
                f"{subset_id}.gradient_event_{event_count + 1}",
            )
            finite(
                gradient["candidate_score"],
                f"{subset_id}.candidate_score",
            )
            event_count += 1
    return event_count


def verify_global_sealed_ranking(
    checkpoint: Mapping[str, Any],
) -> dict[str, float]:
    completed = checkpoint["completed_subset_ids"]
    results = checkpoint["completed_subset_results"]
    sealed = checkpoint["sealed_candidate_order"]
    scores: dict[str, float] = {}
    signatures: dict[str, tuple[tuple[str, str], ...]] = {}
    for subset_id in completed:
        optimization = (results[subset_id] or {}).get(
            "optimization_result"
        )
        if not isinstance(optimization, Mapping):
            raise PromotionError(
                f"subset {subset_id} optimization result is absent"
            )
        scores[subset_id] = finite(
            optimization.get("final_score"),
            f"{subset_id}.final_score",
        )
        params = optimization.get("optimized_params")
        if not isinstance(params, Mapping) or not params:
            raise PromotionError(
                f"subset {subset_id} optimized params are absent"
            )
        signatures[subset_id] = tuple(
            (str(name), finite(
                value, f"{subset_id}.optimized_params.{name}"
            ).hex())
            for name, value in sorted(params.items())
        )
    expected = sorted(
        completed,
        key=lambda subset_id: (
            -scores[subset_id],
            str(subset_id),
            signatures[subset_id],
        ),
    )
    observed = [row.get("subset_id") for row in sealed]
    if observed != expected or len(set(observed)) != 15:
        raise PromotionError(
            "sealed candidate order is not global final candidate_score order"
        )
    for row in sealed:
        subset_id = row["subset_id"]
        sealed_signature = tuple(
            sorted(
                (str(name), str(encoded))
                for name, encoded in (
                    row.get("optimized_params_float_hex") or {}
                ).items()
            )
        )
        if sealed_signature != signatures[subset_id]:
            raise PromotionError(
                f"sealed parameter identity differs for {subset_id}"
            )
    return scores


def verify_r0(
    r0: Mapping[str, Any],
    config: Mapping[str, Any],
    readiness_path: Path,
    spec: Mapping[str, Any],
) -> None:
    measurement = r0.get("measurement")
    identity = r0.get("campaign_identity")
    if (
        r0.get("schema_version")
        != "mysql_sgd_production_real_r0_checkpoint_v1"
        or r0.get("measurement_kind")
        != "production_real_database_r0"
        or not isinstance(measurement, Mapping)
        or not isinstance(identity, Mapping)
        or identity.get("benchmark") != spec["benchmark"]
        or identity.get("config_sha256")
        != sha_bytes(compact_canonical(config))
        or identity.get("readiness_manifest_sha256")
        != sha(readiness_path)
        or identity.get("service_factory_spec")
        != spec["service_factory"]
        or r0.get("measurement_sha256")
        != sha_bytes(compact_canonical(measurement, newline=True))
    ):
        raise PromotionError("fresh in-band R0 identity differs")
    utilization = measurement.get("utilization_pct")
    sample_coverage = finite(
        measurement.get("sample_coverage"), "R0 sample coverage"
    )
    if (
        measurement.get("stable") is not True
        or measurement.get("correctness_passed") is not True
        or measurement.get("oom") is not False
        or measurement.get("mysql_restart") is not False
        or measurement.get("container_restart") is not False
        or not 0.9 <= sample_coverage <= 1.0
        or measurement.get("db_qps_source") != "database_status_counter"
        or finite(measurement.get("db_qps"), "R0 db_qps") <= 0.0
        or not isinstance(utilization, Mapping)
    ):
        raise PromotionError("fresh in-band R0 did not PASS")
    target = str(spec["target_resource"])
    for resource in ("cpu", "memory", "io"):
        value = finite(utilization.get(resource), f"R0 {resource}")
        lower, upper = (0.85, 0.95) if resource == target else (0.30, 0.50)
        if not lower <= value <= upper:
            raise PromotionError(
                f"fresh R0 {resource} is outside [{lower},{upper}]"
            )


def verify_fingerprint(
    fingerprint: Any, scenario: str, label: str,
) -> str:
    if not isinstance(fingerprint, Mapping):
        raise PromotionError(f"{label} fingerprint is absent")
    normalized = "job" if scenario == "job_memory" else "tpch"
    l1 = fingerprint.get("l1") if isinstance(fingerprint, Mapping) else None
    l2 = fingerprint.get("l2") if isinstance(fingerprint, Mapping) else None
    l3 = fingerprint.get("l3") if isinstance(fingerprint, Mapping) else None
    parameter_signature = (
        fingerprint.get("parameter_signature")
        if isinstance(fingerprint, Mapping) else None
    )
    expected_l2_fields = {
        "workers", "hash_batches", "sort_mode", "spill_mode",
        "spill_bytes", "bitmap_lossy_pages", "intermediate_rows",
    }
    expected_l3_fields = {
        "memory_peak_bytes", "dynamic_execution_memory_bytes",
        "spill_bytes",
    }
    if (
        fingerprint.get("schema_version")
        != "mysql_candidate_three_level_fingerprint_v1"
        or fingerprint.get("scenario") != normalized
        or fingerprint.get("true_duplicate_usable") is not True
        or fingerprint.get("true_duplicate_ineligible_reasons") != []
        or not isinstance(parameter_signature, list)
        or not parameter_signature
        or not isinstance(l1, Mapping)
        or l1.get("version") != "mysql_plan_structure_l1_v2"
        or not isinstance(l1.get("structure"), Mapping)
        or not l1["structure"]
        or l1.get("sha256")
        != sha_bytes(compact_canonical(l1["structure"]))
        or not isinstance(l2, Mapping)
        or l2.get("version") != "mysql_execution_state_l2_v1"
        or not isinstance(l2.get("queries"), Mapping)
        or not l2["queries"]
        or l2.get("sha256")
        != sha_bytes(compact_canonical({
            "version": l2.get("version"),
            "queries": l2.get("queries"),
        }))
        or any(
            not isinstance(row, Mapping)
            or set(row) != expected_l2_fields
            for row in l2["queries"].values()
        )
        or not isinstance(l3, Mapping)
        or l3.get("version") != "mysql_scenario_resource_l3_v1"
        or l3.get("scenario") != normalized
        or l3.get("unit_kind") != "query"
        or l3.get("primary_metrics") != [
            "memory_peak_bytes",
            "dynamic_execution_memory_bytes",
            "spill_bytes",
        ]
        or not isinstance(l3.get("vectors"), Mapping)
        or not l3["vectors"]
        or l3.get("qps_included") is not False
        or l3.get("sha256")
        != sha_bytes(compact_canonical({
            "version": l3.get("version"),
            "scenario": l3.get("scenario"),
            "unit_kind": l3.get("unit_kind"),
            "primary_metrics": l3.get("primary_metrics"),
            "vectors": l3.get("vectors"),
            "qps_included": l3.get("qps_included"),
        }))
        or any(
            not isinstance(row, Mapping)
            or not expected_l3_fields <= set(row)
            for row in l3["vectors"].values()
        )
        or fingerprint.get("qps_in_fingerprint") is not False
        or fingerprint.get("qps_role") != "outer_acceptance_only"
        or not isinstance(fingerprint.get("exact_identity_sha256"), str)
        or len(fingerprint["exact_identity_sha256"]) != 64
    ):
        raise PromotionError(f"{label} L1/L2/L3 fingerprint is incomplete")
    identity = {
        "parameter_signature": parameter_signature,
        "l1_sha256": l1["sha256"],
        "l2_sha256": l2["sha256"],
        "l3_sha256": l3["sha256"],
    }
    if fingerprint["exact_identity_sha256"] != sha_bytes(
        compact_canonical(identity)
    ):
        raise PromotionError(f"{label} fingerprint identity checksum differs")
    return str(fingerprint["exact_identity_sha256"])


def verify_ranking_audit(
    audit: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    scenario: str,
    bundle_id: str,
    checkpoint_sha: str,
    measurement_hashes: Mapping[int, str],
    fingerprint_hashes: Mapping[int, str],
    candidate_scores: Mapping[str, float],
) -> None:
    sealed = checkpoint["sealed_candidate_order"]
    predicted = [row.get("subset_id") for row in sealed]
    fixed_top5 = predicted[:5]
    if (
        audit.get("schema_version")
        != "mysql_algorithm_c_prediction_real_ranking_audit_v1"
        or audit.get("status") != "PASS"
        or audit.get("formal_scope") != "mysql_only"
        or audit.get("cross_engine_comparison_prohibited") is not True
        or audit.get("scenario") != scenario
        or audit.get("debug_bundle_id") != bundle_id
        or audit.get("checkpoint_sha256") != checkpoint_sha
        or audit.get("prediction_vs_real_comparison_complete") is not True
        or audit.get("predicted_order") != predicted
        or audit.get("fixed_top5_candidate_ids") != fixed_top5
        or sorted(audit.get("real_order") or []) != sorted(fixed_top5)
        or audit.get("unresolved_issues") != []
        or audit.get("candidate_score_qps_input_used") is not False
    ):
        raise PromotionError("prediction/real ranking audit differs")
    categories = audit.get("unresolved_issue_categories")
    if not isinstance(categories, Mapping) or any(
        categories.get(name) != []
        for name in (
            "sampling", "gradient", "estimation", "fingerprint", "technical"
        )
    ):
        raise PromotionError("ranking audit retains unresolved issues")
    rows = audit.get("per_rank")
    if not isinstance(rows, list) or len(rows) != 5:
        raise PromotionError("ranking audit per-rank evidence is incomplete")
    predicted_ranks: list[int] = []
    real_ranks: list[int] = []
    predicted_scores: dict[int, float] = {}
    real_scores: dict[int, float] = {}
    candidate_by_real_rank: dict[int, Any] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise PromotionError("ranking audit rank record is malformed")
        rank = row.get("predicted_rank")
        real_rank = row.get("real_rank")
        if (
            rank not in EXPECTED_RANKS
            or row.get("candidate_id") != fixed_top5[rank - 1]
            or row.get("measurement_sha256") != measurement_hashes[rank]
            or isinstance(real_rank, bool)
            or real_rank not in EXPECTED_RANKS
        ):
            raise PromotionError(f"ranking audit rank {rank} differs")
        predicted_ranks.append(rank)
        real_ranks.append(real_rank)
        predicted_scores[rank] = finite(
            row.get("candidate_score"), f"rank {rank} candidate_score"
        )
        if predicted_scores[rank] != candidate_scores[row["candidate_id"]]:
            raise PromotionError(
                f"ranking rank {rank} candidate_score differs from sealed order"
            )
        real_scores[real_rank] = finite(
            row.get("real_qps"), f"rank {rank} real_qps"
        )
        candidate_by_real_rank[real_rank] = row.get("candidate_id")
        ranking_fingerprint_sha = verify_fingerprint(
            row.get("three_level_fingerprint"),
            scenario,
            f"ranking rank {rank}",
        )
        if ranking_fingerprint_sha != fingerprint_hashes[rank]:
            raise PromotionError(
                f"ranking rank {rank} fingerprint differs from measurement"
            )
    if (
        sorted(predicted_ranks) != EXPECTED_RANKS
        or sorted(real_ranks) != EXPECTED_RANKS
        or audit.get("real_order")
        != [candidate_by_real_rank[rank] for rank in EXPECTED_RANKS]
        or any(
            predicted_scores[rank] < predicted_scores[rank + 1]
            for rank in EXPECTED_RANKS[:-1]
        )
        or any(
            real_scores[rank] < real_scores[rank + 1]
            for rank in EXPECTED_RANKS[:-1]
        )
    ):
        raise PromotionError("prediction/real ranking order is inconsistent")
    dedup = audit.get("dedup_audit")
    if (
        not isinstance(dedup, Mapping)
        or dedup.get("status") != "PASS"
        or dedup.get("identical_parameter_signature_required") is not True
        or dedup.get("identical_l1_required") is not True
        or dedup.get("l2_within_frozen_tolerance_required") is not True
        or dedup.get("l3_within_frozen_tolerance_required") is not True
        or dedup.get("missing_level_kept_independent") is not True
    ):
        raise PromotionError("three-level duplicate audit differs")


def audit_evidence(
    name: str, evidence_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path]:
    verify_contract()
    spec = SCENARIOS[name]
    manifest, base_config, readiness_path, source = validate_debug_bundle(name)
    evidence = load_object(evidence_path.resolve(), "promotion evidence manifest")
    _promotion_seal_root, promotion_seal_closure_sha = (
        verify_promotion_seal(evidence_path)
    )
    if (
        evidence.get("schema_version")
        != "mysql_algorithm_c_formal10_promotion_evidence_v1"
        or evidence.get("scenario") != spec["scenario"]
        or evidence.get("formal_scope") != "mysql_only"
        or evidence.get("cross_engine_comparison_prohibited") is not True
        or evidence.get("debug_bundle_id") != spec["debug_id"]
        or evidence.get("debug_run_kind") != "fresh_in_band_debug"
        or evidence.get("capture_or_historical_evidence_used") is not False
        or evidence.get("accepted_round_credit") != 0
        or evidence.get("unresolved_issues") != []
    ):
        raise PromotionError("promotion evidence authority differs")
    pointers = evidence.get("pointers")
    if not isinstance(pointers, Mapping):
        raise PromotionError("promotion evidence pointers are absent")
    debug_config_pointer = pointers.get("debug_config")
    if not isinstance(debug_config_pointer, Mapping):
        raise PromotionError("debug run config pointer is absent")
    debug_config_path = Path(
        str(debug_config_pointer.get("path") or "")
    )
    if not debug_config_path.is_absolute():
        raise PromotionError("debug run config path must be absolute")
    config = load_object(debug_config_path, "debug run config")
    if (
        len(str(debug_config_pointer.get("sha256") or "")) != 64
        or sha(debug_config_path)
        != str(debug_config_pointer.get("sha256") or "").lower()
    ):
        raise PromotionError("debug run config pointer checksum differs")
    output_root = Path(str(config.get("output", {}).get("directory") or ""))
    if (
        not output_root.is_absolute()
        or debug_config_path.resolve()
        != (output_root / "debug_config.json").resolve()
    ):
        raise PromotionError("debug run config/output authority differs")
    validate_debug_run_config(base_config, config, output_root)
    resolve_inside(evidence_path.resolve(), output_root, "promotion evidence")
    r0_path = pointer_path(pointers.get("r0"), output_root, "R0")
    checkpoint_path = pointer_path(
        pointers.get("checkpoint"), output_root, "checkpoint"
    )
    ranking_path = pointer_path(
        pointers.get("ranking_audit"), output_root, "ranking audit"
    )
    outer = config.get("formal_outer_protocol") or {}
    if (
        r0_path != Path(str(outer.get("formal_real_r0_checkpoint_path"))).resolve()
        or checkpoint_path
        != Path(str(outer.get("formal_checkpoint_path"))).resolve()
    ):
        raise PromotionError("debug evidence is outside configured authority")
    r0 = load_object(r0_path, "R0")
    verify_r0(r0, config, readiness_path, spec)
    checkpoint = load_object(checkpoint_path, "checkpoint")
    checkpoint_sha = sha(checkpoint_path)
    completed = checkpoint.get("completed_subset_ids")
    ordered = checkpoint.get("ordered_subset_ids")
    sealed = checkpoint.get("sealed_candidate_order")
    if (
        checkpoint.get("schema_version")
        != "mysql_sgd_outer_replay_checkpoint_v1"
        or checkpoint.get("r0_completed") is not True
        or not isinstance(ordered, list)
        or completed != ordered
        or len(completed) != 15
        or len(set(completed)) != 15
        or not isinstance(sealed, list)
        or len(sealed) != 15
        or [row.get("rank") for row in sealed] != list(range(1, 16))
        or checkpoint.get("sealed_representative_ranks") != EXPECTED_RANKS
        or checkpoint.get("replayed_candidate_ranks") != EXPECTED_RANKS
        or checkpoint.get("inflight_replays") != {}
    ):
        raise PromotionError("checkpoint is not complete 15/all + fixed Top-5")
    candidate_scores = verify_global_sealed_ranking(checkpoint)
    gradient_event_count = verify_gradient_events(checkpoint)

    audit_log = checkpoint.get("audit_log")
    if not isinstance(audit_log, list):
        raise PromotionError("checkpoint audit log is absent")
    start_rows = [
        row
        for row in audit_log
        if isinstance(row, Mapping)
        and row.get("event") == "candidate_replay_started"
        and row.get("rank") in EXPECTED_RANKS
    ]
    completion_rows = [
        row
        for row in audit_log
        if isinstance(row, Mapping)
        and row.get("event") == "candidate_replayed"
        and row.get("rank") in EXPECTED_RANKS
    ]
    starts = {row["rank"]: row for row in start_rows}
    completions = {row["rank"]: row for row in completion_rows}
    if (
        len(start_rows) != 5
        or len(completion_rows) != 5
        or sorted(starts) != EXPECTED_RANKS
        or sorted(completions) != EXPECTED_RANKS
    ):
        raise PromotionError("Top-5 start/completion audit is incomplete")

    journal_root = resolve_inside(
        Path(str(evidence.get("point_journal_root") or "")),
        output_root,
        "point journal root",
    )
    rank_authority = evidence.get("rank_authority")
    if not isinstance(rank_authority, list) or len(rank_authority) != 5:
        raise PromotionError("rank authority cardinality differs")
    by_rank = {
        row.get("rank"): row
        for row in rank_authority
        if isinstance(row, Mapping)
    }
    if (
        len(by_rank) != 5
        or set(by_rank) != set(EXPECTED_RANKS)
    ):
        raise PromotionError("rank authority contains duplicate/invalid ranks")
    measurement_hashes: dict[int, str] = {}
    fingerprint_hashes: dict[int, str] = {}
    rank_seals: list[dict[str, Any]] = []
    attempt_tokens: set[str] = set()
    for rank in EXPECTED_RANKS:
        record = by_rank.get(rank)
        start = starts[rank]
        completion = completions[rank]
        if not isinstance(record, Mapping):
            raise PromotionError(f"rank {rank} authority is absent")
        token = str(start.get("attempt_token") or "")
        if (
            not token
            or token in attempt_tokens
            or record.get("attempt_token") != token
            or completion.get("attempt_token") != token
            or start.get("stage") != "top_5"
            or completion.get("stage") != "top_5"
        ):
            raise PromotionError(f"rank {rank} attempt token differs")
        attempt_tokens.add(token)
        sealed_identity = sealed[rank - 1]
        started_identity = candidate_identity(start.get("candidate") or {})
        if started_identity != sealed_identity:
            raise PromotionError(f"rank {rank} sealed identity differs")
        identity_sha = sha_bytes(compact_canonical(sealed_identity))
        current_path, initial, current = verify_runtime_journal(
            journal_root, token, f"rank {rank} journal"
        )
        current_pointer = record.get("journal_current")
        if (
            not isinstance(current_pointer, Mapping)
            or current_pointer.get("path") != str(current_path)
            or current_pointer.get("sha256") != sha(current_path)
        ):
            raise PromotionError(
                f"rank {rank} journal current pointer differs"
            )
        expected_current = journal_root / token / "current.json"
        if current_path != expected_current.resolve():
            raise PromotionError(f"rank {rank} journal path/token differs")
        measurement_path = pointer_path(
            record.get("measurement"),
            journal_root,
            f"rank {rank} measurement",
        )
        measurement = load_object(
            measurement_path, f"rank {rank} measurement"
        )
        measurement_sha = sha(measurement_path)
        resource_coverage = finite(
            measurement.get("resource_sample_coverage"),
            f"rank {rank} resource coverage",
        )
        database_coverage = finite(
            measurement.get("database_counter_coverage"),
            f"rank {rank} DB counter coverage",
        )
        promotion = record.get("promotion_authority")
        embedded = current.get("measurement")
        pending = initial.get("pending")
        expected_pending_params = {
            name: finite(
                value, f"rank {rank} sealed pending parameter {name}"
            )
            for name, value in (
                start.get("candidate", {}).get("optimized_params") or {}
            ).items()
        }
        observed_pending_params = (
            pending.get("candidate_params")
            if isinstance(pending, Mapping)
            else None
        )
        if (
            current.get("accounting_token") != token
            or current.get("resume_token") != token
            or current.get("stage") != "point_complete"
            or current.get("measurement_complete") is not True
            or current.get("recovery_verified") is not True
            or current.get("lease_released") is not True
            or current.get("pending") is not None
            or not isinstance(embedded, Mapping)
            or not isinstance(pending, Mapping)
            or pending.get("stage") != "initial_top_k"
            or pending.get("replay_ordinal") != rank
            or pending.get("subset_id") != sealed_identity["subset_id"]
            or not isinstance(observed_pending_params, Mapping)
            or {
                str(name): finite(
                    value, f"rank {rank} journal pending parameter {name}"
                )
                for name, value in observed_pending_params.items()
            }
            != expected_pending_params
            or not isinstance(promotion, Mapping)
            or promotion.get("schema_version")
            != "mysql_algorithm_c_rank_promotion_authority_v1"
            or promotion.get("rank") != rank
            or promotion.get("attempt_token") != token
            or promotion.get("candidate_identity_sha256") != identity_sha
            or promotion.get("journal_current_sha256") != sha(current_path)
            or promotion.get("measurement_sha256") != measurement_sha
            or promotion.get("source_measurement_sha256")
            != sha_bytes(compact_canonical(embedded))
            or promotion.get("real_replay_completed") is not True
            or promotion.get("accepted_round_credit") != 0
            or measurement.get("rank") != rank
            or measurement.get("attempt_token") != token
            or measurement.get("status") != "COMPLETE"
            or measurement.get("real_replay") is not True
            or measurement.get("failed_replay") is not False
            or measurement.get("accepted_round_credit") != 0
            or measurement.get("correctness_passed") is not True
            or measurement.get("stable") is not True
            or not 0.9 <= resource_coverage <= 1.0
            or not 0.9 <= database_coverage <= 1.0
        ):
            raise PromotionError(
                f"rank {rank} journal/completion/measurement differs"
            )
        fingerprint_sha = verify_fingerprint(
            measurement.get("three_level_fingerprint"),
            spec["scenario"],
            f"rank {rank}",
        )
        expected_parameter_signature = [
            [name, encoded]
            for name, encoded in sorted(
                sealed_identity["optimized_params_float_hex"].items()
            )
        ]
        if (
            measurement["three_level_fingerprint"].get(
                "parameter_signature"
            )
            != expected_parameter_signature
        ):
            raise PromotionError(
                f"rank {rank} fingerprint parameter signature differs"
            )
        measurement_hashes[rank] = measurement_sha
        fingerprint_hashes[rank] = fingerprint_sha
        rank_seals.append({
            "rank": rank,
            "attempt_token": token,
            "candidate_identity_sha256": identity_sha,
            "journal_current_sha256": sha(current_path),
            "measurement_sha256": measurement_sha,
            "three_level_fingerprint_identity_sha256": fingerprint_sha,
        })
    ranking = load_object(ranking_path, "ranking audit")
    verify_ranking_audit(
        ranking,
        checkpoint,
        spec["scenario"],
        spec["debug_id"],
        checkpoint_sha,
        measurement_hashes,
        fingerprint_hashes,
        candidate_scores,
    )
    seal = {
        "schema_version": "mysql_algorithm_c_formal10_debug_gate_seal_v1",
        "status": "PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scenario": spec["scenario"],
        "debug_bundle_id": spec["debug_id"],
        "debug_bundle_manifest_sha256":
            sha(Path(spec["debug_root"]) / spec["debug_id"] / "bundle_manifest.json"),
        "debug_run_config_sha256": sha(debug_config_path),
        "promotion_seal_closure_sha256": promotion_seal_closure_sha,
        "formal10_contract_sha256": CONTRACT_SHA256,
        "fresh_in_band_r0_passed": True,
        "r0_sha256": sha(r0_path),
        "checkpoint_sha256": checkpoint_sha,
        "completed_nonempty_subsets": 15,
        "gradient_event_count": gradient_event_count,
        "required_gradient_event_fields": list(REQUIRED_GRADIENT_FIELDS),
        "original_gradient_only": True,
        "sealed_fixed_top5_ranks": EXPECTED_RANKS,
        "fixed_top5_real_replays_completed": 5,
        "rank_authority": rank_seals,
        "ranking_audit_sha256": sha(ranking_path),
        "prediction_vs_real_ranking_audit_passed": True,
        "unresolved_issues": [],
        "accepted_round_credit": 0,
        "promotion_authorized": True,
    }
    return seal, manifest, config, source


def formal_config(
    debug_config: Mapping[str, Any],
    bundle_id: str,
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    config = copy.deepcopy(dict(debug_config))
    output = (
        ROOT / "history/mysql_gradient_formal10"
        / str(spec["scenario"])
        / bundle_id
    )
    config["accepted_round_authorized"] = True
    config["outer_execution_authorized"] = True
    config["measurement_seconds"] = 600
    config["minimum_measurement_seconds"] = 180
    config["output"] = {
        **dict(config.get("output") or {}),
        "directory": str(output),
    }
    outer = dict(config.get("formal_outer_protocol") or {})
    outer.update({
        "accepted_shrink_count": 0,
        "rejected_count": 0,
        "target_accepted_shrinks": 10,
        "max_outer_attempts": 100,
        "formal_search_initial_replay_top_k": 5,
        "formal_search_max_replay_top_k": 5,
        "formal_checkpoint_path": str(
            output / "formal_outer_checkpoint.json"
        ),
        "formal_real_r0_checkpoint_path": str(
            output / "formal_real_r0.json"
        ),
        "formal10_campaign_checkpoint_path": str(
            output / "formal10_campaign_checkpoint.json"
        ),
    })
    config["formal_outer_protocol"] = outer
    config["formal10_authorization"] = {
        "schema_version": "mysql_algorithm_c_formal10_runtime_policy_v1",
        "bundle_id": bundle_id,
        "target_accepted_rounds": 10,
        "initial_accepted_rounds": 0,
        "minimum_measurement_seconds": 180,
        "maximum_measurement_seconds": 600,
        "stop_at_first_stable_valid_boundary": True,
        "qps_role": "outer_acceptance_after_real_replay_only",
        "candidate_score_qps_input_prohibited": True,
        "complete_fixed_top5_required_before_increment": True,
        "complete_l1_l2_l3_required_before_increment": True,
        "atomic_checkpoint_required_before_increment_visible": True,
        "never_count_as_accepted": [
            "capture",
            "r0",
            "candidate_generation",
            "failed_replay",
            "preparation",
            "debug_round",
            "failed_gate",
        ],
        "formal10_contract_sha256": CONTRACT_SHA256,
    }
    return config


def patched_optimizer_payload(source: Path) -> bytes:
    path = source / "mysql_parameter_optimizer.py"
    text = path.read_text(encoding="utf-8")
    marker = "\ndef _run_optimization_unleased(\n"
    helper = r'''

def _formal10_finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _formal10_complete_top5_gate(orchestration: Any) -> Dict[str, Any]:
    evaluations = (
        list(orchestration.get("evaluations") or [])
        if isinstance(orchestration, Mapping) else []
    )
    reasons: List[str] = []
    ranks = [row.get("candidate_rank") for row in evaluations]
    if len(evaluations) != 5 or sorted(ranks) != [1, 2, 3, 4, 5]:
        reasons.append("fixed_top5_not_complete")
    for row in evaluations:
        rank = row.get("candidate_rank")
        fingerprint = row.get("three_level_fingerprint")
        validation = row.get("candidate_final_validation")
        coverage = (
            _formal10_finite_number(validation.get("sample_coverage"))
            if isinstance(validation, Mapping) else None
        )
        db_qps = (
            _formal10_finite_number(validation.get("db_qps"))
            if isinstance(validation, Mapping) else None
        )
        if row.get("callback_error"):
            reasons.append(f"rank_{rank}_callback_error")
        if not isinstance(validation, Mapping):
            reasons.append(f"rank_{rank}_measurement_absent")
        elif (
            validation.get("stable") is not True
            or validation.get("correctness_passed") is not True
            or validation.get("oom") is not False
            or validation.get("mysql_restart") is not False
            or validation.get("container_restart") is not False
            or coverage is None
            or not 0.9 <= coverage <= 1.0
            or validation.get("db_qps_source")
            != "database_status_counter"
            or db_qps is None
            or db_qps <= 0.0
        ):
            reasons.append(f"rank_{rank}_measurement_gate_failed")
        if (
            not isinstance(fingerprint, Mapping)
            or fingerprint.get("true_duplicate_usable") is not True
            or not isinstance(fingerprint.get("l1"), Mapping)
            or not isinstance(fingerprint.get("l2"), Mapping)
            or not isinstance(fingerprint.get("l3"), Mapping)
            or fingerprint.get("qps_in_fingerprint") is not False
            or fingerprint.get("qps_role") != "outer_acceptance_only"
        ):
            reasons.append(f"rank_{rank}_l1_l2_l3_incomplete")
        recovery = row.get("recovery")
        if (
            not isinstance(recovery, Mapping)
            or recovery.get("verified") is not True
        ):
            reasons.append(f"rank_{rank}_recovery_incomplete")
    return {
        "passed": not reasons,
        "replay_count": len(evaluations),
        "ranks": ranks,
        "reasons": sorted(set(reasons)),
        "accepted_round_credit_if_failed": 0,
    }


def _formal10_runtime_r0_gate(
    validation: Any, target_resource: str,
) -> Dict[str, Any]:
    reasons: List[str] = []
    if not isinstance(validation, Mapping):
        reasons.append("runtime_r0_absent")
        utilization = {}
    else:
        utilization = validation.get("utilization_pct")
        coverage = _formal10_finite_number(
            validation.get("sample_coverage")
        )
        db_qps = _formal10_finite_number(validation.get("db_qps"))
        if (
            validation.get("stable") is not True
            or validation.get("correctness_passed") is not True
            or validation.get("oom") is not False
            or validation.get("mysql_restart") is not False
            or validation.get("container_restart") is not False
            or coverage is None
            or not 0.9 <= coverage <= 1.0
            or validation.get("db_qps_source")
            != "database_status_counter"
            or db_qps is None
            or db_qps <= 0.0
        ):
            reasons.append("runtime_r0_measurement_gate_failed")
        if not isinstance(utilization, Mapping):
            reasons.append("runtime_r0_utilization_absent")
            utilization = {}
    for resource in ("cpu", "memory", "io"):
        lower, upper = (
            (0.85, 0.95)
            if resource == target_resource else (0.30, 0.50)
        )
        value = _formal10_finite_number(utilization.get(resource))
        if value is None or not lower <= value <= upper:
            reasons.append(f"runtime_r0_{resource}_outside_envelope")
    return {
        "passed": not reasons,
        "reasons": sorted(set(reasons)),
        "accepted_round_credit_if_failed": 0,
    }


def _formal10_checkpoint_identity(config: Mapping[str, Any]) -> Dict[str, Any]:
    policy = config.get("formal10_authorization")
    if not isinstance(policy, Mapping):
        raise ValueError("formal10_authorization is absent")
    return {
        "bundle_id": policy.get("bundle_id"),
        "policy_sha256": hashlib.sha256(
            json.dumps(
                policy, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
        "resource_authority": copy.deepcopy(
            config.get("execution_resource_authority")
        ),
        "storage_authority": copy.deepcopy(
            config.get("execution_storage_authority")
        ),
    }


def _formal10_atomic_checkpoint(path: Path, document: Mapping[str, Any]) -> str:
    payload = (
        json.dumps(
            document, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ) + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    )
    sidecar = path.with_name(path.name + ".sha256")
    sidecar_temporary = sidecar.with_name(
        f".{sidecar.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    )
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        with sidecar_temporary.open("x", encoding="ascii") as handle:
            handle.write(f"{digest}  {path.name}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.replace(sidecar_temporary, sidecar)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        for candidate in (temporary, sidecar_temporary):
            if candidate.exists():
                candidate.unlink()
    return digest


def _formal10_load_checkpoint(
    config: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    policy = config.get("formal10_authorization")
    if not isinstance(policy, Mapping):
        return None
    outer = config.get("formal_outer_protocol") or {}
    path = Path(str(outer.get("formal10_campaign_checkpoint_path") or ""))
    if not path.is_absolute():
        raise ValueError("formal10 campaign checkpoint path must be absolute")
    sidecar = path.with_name(path.name + ".sha256")
    if not path.exists() and not sidecar.exists():
        return None
    if (
        not path.is_file() or path.is_symlink()
        or not sidecar.is_file() or sidecar.is_symlink()
    ):
        raise ValueError("formal10 campaign checkpoint is incomplete")
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if sidecar.read_text(encoding="ascii") != f"{digest}  {path.name}\n":
        raise ValueError("formal10 campaign checkpoint checksum differs")
    document = json.loads(payload)
    if (
        not isinstance(document, Mapping)
        or document.get("schema_version")
        != "mysql_algorithm_c_formal10_campaign_checkpoint_v1"
        or document.get("campaign_identity")
        != _formal10_checkpoint_identity(config)
        or document.get("target_accepted_rounds") != 10
    ):
        raise ValueError("formal10 campaign checkpoint identity differs")
    return copy.deepcopy(dict(document))
'''
    if marker not in text or text.count(marker) != 1:
        raise PromotionError("optimizer insertion marker differs")
    text = text.replace(marker, helper + marker)
    old = """    outer_decision = decide_validation_acceptance(
        baseline_validation or {},
        chosen_validation,
        target_resource=ranking_target_resource or "",
        hard_gate_passed=chosen_replay is not None,
    )
"""
    new = """    formal10_top5_gate = _formal10_complete_top5_gate(
        replay_orchestration
    )
    outer_decision = decide_validation_acceptance(
        baseline_validation or {},
        chosen_validation,
        target_resource=ranking_target_resource or "",
        hard_gate_passed=(
            chosen_replay is not None
            and (
                formal10_top5_gate["passed"]
                if isinstance(config.get("formal10_authorization"), Mapping)
                else True
            )
        ),
    )
"""
    if old not in text or text.count(old) != 1:
        raise PromotionError("optimizer acceptance marker differs")
    text = text.replace(old, new)
    old = """    local_converged = False

    for attempt in range(1, max_attempts + 1):
"""
    new = """    local_converged = False
    formal10_policy = campaign_config.get("formal10_authorization")
    formal10_runtime_r0_gate = _formal10_runtime_r0_gate(
        baseline_validation,
        str(campaign_config.get("target_resource") or "memory"),
    )
    formal10_checkpoint = _formal10_load_checkpoint(campaign_config)
    start_attempt = 1
    if isinstance(formal10_policy, Mapping):
        if (
            formal10_policy.get("target_accepted_rounds") != 10
            or formal10_policy.get("initial_accepted_rounds") != 0
            or formal10_policy.get("minimum_measurement_seconds") != 180
            or formal10_policy.get("maximum_measurement_seconds") != 600
            or formal10_policy.get("qps_role")
            != "outer_acceptance_after_real_replay_only"
            or formal10_policy.get("candidate_score_qps_input_prohibited")
            is not True
            or formal10_policy.get(
                "complete_fixed_top5_required_before_increment"
            ) is not True
            or formal10_policy.get(
                "complete_l1_l2_l3_required_before_increment"
            ) is not True
            or formal10_policy.get(
                "atomic_checkpoint_required_before_increment_visible"
            ) is not True
        ):
            raise ValueError("formal10 runtime policy differs")
        if not formal10_runtime_r0_gate["passed"]:
            raise ValueError(
                "formal10 fresh runtime R0 gate failed: "
                + ",".join(formal10_runtime_r0_gate["reasons"])
            )
    if formal10_checkpoint is not None:
        if formal10_checkpoint.get("status") == "complete":
            raise ValueError("formal10 campaign checkpoint is already complete")
        accepted_count = int(formal10_checkpoint["accepted_rounds"])
        rejected_count = int(formal10_checkpoint["rejected_rounds"])
        current_incumbent = copy.deepcopy(
            formal10_checkpoint["current_incumbent"]
        )
        current_validation = copy.deepcopy(
            formal10_checkpoint["current_validation"]
        )
        campaign_rounds = copy.deepcopy(
            formal10_checkpoint.get("campaign_rounds") or []
        )
        aggregate_outer_loop = copy.deepcopy(
            formal10_checkpoint.get("aggregate_outer_loop") or []
        )
        total_recovery_count = int(
            formal10_checkpoint.get("total_recovery_count", 0)
        )
        seen_candidate_tokens = set(
            formal10_checkpoint.get("seen_candidate_signatures") or []
        )
        current_sensitivity = copy.deepcopy(
            formal10_checkpoint["current_sensitivity"]
        )
        current_escape_radius = float(
            formal10_checkpoint["current_escape_radius"]
        )
        current_eta = float(formal10_checkpoint["current_eta"])
        consecutive_rejected_batches = int(
            formal10_checkpoint["consecutive_rejected_batches"]
        )
        start_attempt = int(formal10_checkpoint["next_outer_attempt"])
        restored_ranges = formal10_checkpoint.get("current_ranges")
        if not isinstance(restored_ranges, Mapping):
            raise ValueError("formal10 checkpoint ranges are absent")
        round_costs = dict(campaign_config.get("cost_parameters") or {})
        round_costs["baseline_values"] = copy.deepcopy(current_incumbent)
        round_costs["ranges"] = copy.deepcopy(dict(restored_ranges))
        campaign_config["cost_parameters"] = round_costs

    for attempt in range(start_attempt, max_attempts + 1):
"""
    if old not in text or text.count(old) != 1:
        raise PromotionError("optimizer resume marker differs")
    text = text.replace(old, new)
    old = """        campaign_rounds.append(round_record)
        outer_row = copy.deepcopy(round_result.formal_document["outer_loop"][0])
"""
    new = """        campaign_rounds.append(round_record)
        if isinstance(formal10_policy, Mapping):
            top5_gate = _formal10_complete_top5_gate(orchestration)
            if accepted_this_round and not top5_gate["passed"]:
                raise RuntimeError(
                    "accepted round crossed incomplete Top-5/L1/L2/L3 gate"
                )
            checkpoint_path = Path(str(
                (campaign_config.get("formal_outer_protocol") or {}).get(
                    "formal10_campaign_checkpoint_path"
                ) or ""
            ))
            checkpoint_document = {
                "schema_version":
                    "mysql_algorithm_c_formal10_campaign_checkpoint_v1",
                "campaign_identity":
                    _formal10_checkpoint_identity(campaign_config),
                "status":
                    "complete" if accepted_count == target else "running",
                "accepted_rounds": accepted_count,
                "rejected_rounds": rejected_count,
                "target_accepted_rounds": target,
                "next_outer_attempt": attempt + 1,
                "current_incumbent": copy.deepcopy(current_incumbent),
                "current_validation": copy.deepcopy(current_validation),
                "current_ranges": copy.deepcopy(
                    campaign_config["cost_parameters"]["ranges"]
                ),
                "current_sensitivity":
                    copy.deepcopy(current_sensitivity),
                "seen_candidate_signatures":
                    sorted(seen_candidate_tokens),
                "current_escape_radius": current_escape_radius,
                "current_eta": current_eta,
                "consecutive_rejected_batches":
                    consecutive_rejected_batches,
                "last_top5_gate": copy.deepcopy(top5_gate),
                "fresh_runtime_r0_gate":
                    copy.deepcopy(formal10_runtime_r0_gate),
                "last_round_accepted": accepted_this_round,
                "campaign_rounds": copy.deepcopy(campaign_rounds),
                "aggregate_outer_loop": copy.deepcopy(
                    aggregate_outer_loop + [{
                        **copy.deepcopy(
                            round_result.formal_document["outer_loop"][0]
                        ),
                        "iter": attempt,
                        "campaign_round": copy.deepcopy(round_record),
                    }]
                ),
                "total_recovery_count": total_recovery_count,
                "accepted_increment_boundary": {
                    "fresh_runtime_gate_pass":
                        formal10_runtime_r0_gate["passed"],
                    "complete_fixed_top5_real_replay":
                        top5_gate["passed"],
                    "outer_qps_acceptance_pass": accepted_this_round,
                    "complete_l1_l2_l3_evidence":
                        top5_gate["passed"],
                    "atomic_checkpoint_commit": True,
                },
            }
            checkpoint_sha = _formal10_atomic_checkpoint(
                checkpoint_path, checkpoint_document
            )
            round_record["formal10_checkpoint_sha256"] = checkpoint_sha
            round_record["accepted_count_visible_after_atomic_commit"] = (
                accepted_count
            )
        outer_row = copy.deepcopy(round_result.formal_document["outer_loop"][0])
"""
    if old not in text or text.count(old) != 1:
        raise PromotionError("optimizer checkpoint commit marker differs")
    text = text.replace(old, new)
    return text.encode("utf-8")


def launcher_payload(
    source: Path, spec: Mapping[str, Any], bundle_id: str,
) -> bytes:
    text = f'''#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import runpy
import sys

BUNDLE = Path(__file__).resolve().parent
SOURCE = BUNDLE / "source"
RUNTIME = BUNDLE / "runtime"
VERIFIER = BUNDLE / "formal10_runtime_verifier.py"
DRIVER = SOURCE / {str(spec["driver"])!r}
EXPECTED_SERVICE = {str(spec["service_factory"])!r}
EXPECTED_BUNDLE_ID = {bundle_id!r}
os.chdir("/tmp")
argv = sys.argv[1:]
if "--help" not in argv and "-h" not in argv:
    def required(name):
        if argv.count(name) != 1:
            raise RuntimeError("formal10 launch requires exactly one " + name)
        index = argv.index(name)
        if index + 1 >= len(argv):
            raise RuntimeError("formal10 option has no value: " + name)
        return argv[index + 1]
    if BUNDLE.name != EXPECTED_BUNDLE_ID:
        raise RuntimeError("formal10 content address differs")
    if required("--service-factory") != EXPECTED_SERVICE:
        raise RuntimeError("formal10 service factory override is prohibited")
    if Path(required("--config")).resolve() != BUNDLE / "config.json":
        raise RuntimeError("formal10 config must be immutable sibling")
    readiness = Path(required("--readiness-manifest"))
    if (
        readiness.is_symlink()
        or readiness.resolve().parent != BUNDLE
        or not readiness.name.startswith("readiness_manifest-")
        or readiness.suffix != ".json"
    ):
        raise RuntimeError("formal10 readiness must be immutable sibling")
    if "--r0-authority-readiness-manifest" in argv:
        raise RuntimeError("formal10 requires a fresh in-band R0")
sys.path[:] = [str(RUNTIME), str(SOURCE)] + [
    item for item in sys.path
    if item not in ("", str(RUNTIME), str(SOURCE))
]
shell_package = type(sys)("shell")
shell_package.__path__ = [str(SOURCE / "shell")]
sys.modules["shell"] = shell_package
spec = importlib.util.spec_from_file_location(
    "shell.verify_mysql_sgd_v4_readiness", VERIFIER
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load formal10 verifier")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
runpy.run_path(str(DRIVER), run_name="__main__")
'''
    return text.encode("utf-8")


def verifier_payload(
    bundle_id: str,
    resource: Mapping[str, Any],
    storage: Mapping[str, Any],
    source_manifest_sha: str,
) -> bytes:
    text = f'''"""Generated fail-closed formal10 runtime verifier."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

EXPECTED_BUNDLE_ID = {bundle_id!r}
EXPECTED_CONTRACT_SHA256 = {CONTRACT_SHA256!r}
EXPECTED_SOURCE_MANIFEST_SHA256 = {source_manifest_sha!r}
EXPECTED_RESOURCE = {resource!r}
EXPECTED_STORAGE = {storage!r}

@dataclass(frozen=True)
class Check:
    requirement: str
    item: str
    passed: bool
    reason: str

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def verify_document(document: Mapping[str, Any], _root=None):
    bundle = Path(str(document.get("bundle_path") or ""))
    pointers = document.get("pointers") or {{}}
    config_path = Path(str((pointers.get("config") or {{}}).get("path") or ""))
    contract_path = Path(str((pointers.get("formal10_contract") or {{}}).get("path") or ""))
    seal_path = Path(str((pointers.get("debug_gate_seal") or {{}}).get("path") or ""))
    manifest_path = Path(str((pointers.get("bundle_manifest") or {{}}).get("path") or ""))
    config = {{}}
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    policy = config.get("formal10_authorization") or {{}}
    outer = config.get("formal_outer_protocol") or {{}}
    source = bundle / "source"
    source_manifest_path = source / "source_manifest.json"
    source_closure_ok = False
    try:
        source_manifest = json.loads(
            source_manifest_path.read_text(encoding="utf-8")
        )
        source_closure_ok = (
            not source_manifest_path.is_symlink()
            and _sha(source_manifest_path)
            == EXPECTED_SOURCE_MANIFEST_SHA256
            and isinstance(source_manifest.get("files"), dict)
            and bool(source_manifest["files"])
            and all(
                (source / relative).is_file()
                and not (source / relative).is_symlink()
                and _sha(source / relative) == expected
                for relative, expected in source_manifest["files"].items()
            )
        )
    except Exception:
        source_closure_ok = False
    checks = []
    def add(group, item, passed, reason):
        checks.append(Check(group, item, bool(passed), reason))
    add("SCHEMA", "formal10", document.get("schema_version") == "mysql_algorithm_c_formal10_readiness_v1", "schema differs")
    add("AUTH", "bundle", document.get("bundle_id") == EXPECTED_BUNDLE_ID and bundle.name == EXPECTED_BUNDLE_ID, "bundle differs")
    add("R01", "scope", document.get("formal_scope") == "mysql_only", "scope differs")
    add("R02", "cross_engine", document.get("cross_engine_comparison_prohibited") is True, "cross-engine prohibition differs")
    add("R03", "resource", document.get("execution_resource_authority") == EXPECTED_RESOURCE and config.get("execution_resource_authority") == EXPECTED_RESOURCE, "resource authority differs")
    add("R04", "storage", document.get("execution_storage_authority") == EXPECTED_STORAGE and config.get("execution_storage_authority") == EXPECTED_STORAGE, "storage authority differs")
    add("R05", "contract", contract_path.is_file() and _sha(contract_path) == EXPECTED_CONTRACT_SHA256 and (pointers.get("formal10_contract") or {{}}).get("sha256") == EXPECTED_CONTRACT_SHA256, "contract differs")
    add("R06", "promotion", seal_path.is_file() and (pointers.get("debug_gate_seal") or {{}}).get("sha256") == _sha(seal_path), "promotion seal differs")
    add("R07", "manifest", manifest_path.is_file() and (pointers.get("bundle_manifest") or {{}}).get("sha256") == _sha(manifest_path), "manifest differs")
    add("R08", "config", config_path.is_file() and (pointers.get("config") or {{}}).get("sha256") == _sha(config_path), "config differs")
    add("R09", "algorithm", (config.get("optimizer") or {{}}).get("original_gradient_only") is True and policy.get("candidate_score_qps_input_prohibited") is True, "algorithm differs")
    add("R10", "top5", outer.get("formal_search_initial_replay_top_k") == 5 and outer.get("formal_search_max_replay_top_k") == 5 and policy.get("complete_l1_l2_l3_required_before_increment") is True, "Top-5/fingerprint policy differs")
    add("R11", "accepted", config.get("accepted_round_authorized") is True and outer.get("accepted_shrink_count") == 0 and outer.get("target_accepted_shrinks") == 10 and policy.get("atomic_checkpoint_required_before_increment_visible") is True, "accepted boundary differs")
    add("R12", "timing", config.get("minimum_measurement_seconds") == 180 and config.get("measurement_seconds") == 600 and policy.get("stop_at_first_stable_valid_boundary") is True, "timing differs")
    add("R13", "source", document.get("source_manifest_sha256") == EXPECTED_SOURCE_MANIFEST_SHA256, "source differs")
    add("R14", "source_closure", source_closure_ok, "source closure differs")
    return checks

def verify_ready(document):
    failed = [item for item in verify_document(document) if not item.passed]
    if failed:
        raise RuntimeError("formal10 readiness rejected: " + "; ".join(item.item for item in failed))
'''
    return text.encode("utf-8")


def publish(
    name: str,
    evidence_path: Path,
    seal: Mapping[str, Any],
    debug_manifest: Mapping[str, Any],
    debug_config: Mapping[str, Any],
    source: Path,
) -> Path:
    spec = SCENARIOS[name]
    evidence_sha = sha(evidence_path)
    promotion_seal_root, promotion_seal_closure_sha = (
        verify_promotion_seal(evidence_path)
    )
    closure = {
        "schema_version": "mysql_algorithm_c_formal10_bundle_closure_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scenario": spec["scenario"],
        "debug_bundle_id": spec["debug_id"],
        "debug_bundle_manifest_sha256": seal[
            "debug_bundle_manifest_sha256"
        ],
        "debug_gate_seal_sha256": sha_bytes(canonical(seal)),
        "promotion_evidence_manifest_sha256": evidence_sha,
        "promotion_seal_closure_sha256": promotion_seal_closure_sha,
        "formal10_contract_sha256": CONTRACT_SHA256,
        "source_bundle_id": debug_manifest["source_bundle_id"],
        "source_manifest_sha256": debug_manifest["source_manifest_sha256"],
        "execution_resource_authority":
            debug_manifest["execution_resource_authority"],
        "execution_storage_authority":
            debug_manifest["execution_storage_authority"],
        "target_accepted_rounds": 10,
        "initial_accepted_rounds": 0,
        "minimum_measurement_seconds": 180,
        "maximum_measurement_seconds": 600,
        "stop_at_first_stable_valid_boundary": True,
        "original_gradient_only": True,
        "enumerate_all_nonempty_subspaces": True,
        "candidate_count": 15,
        "fixed_top_k_real_replay": 5,
        "qps_role": "outer_acceptance_after_real_replay_only",
        "candidate_score_qps_input_prohibited": True,
        "accepted_increment_requires": [
            "fresh_runtime_gate_pass",
            "complete_fixed_top5_real_replay",
            "outer_qps_acceptance_pass",
            "complete_l1_l2_l3_evidence",
            "atomic_checkpoint_commit",
        ],
        "publication": "staged_then_atomic_rename",
        "idempotency": "content_addressed_existing_verified",
        "builder_sha256": sha(BUILDER),
    }
    bundle_id = sha_bytes(canonical(closure))
    final = Path(spec["formal_root"]) / bundle_id
    if final.exists():
        verify_formal_bundle(final, bundle_id)
        return final
    Path(spec["formal_root"]).mkdir(parents=True, exist_ok=True)
    stage = Path(spec["formal_root"]) / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        runtime = stage / "runtime"
        runtime.mkdir()
        frozen_source = stage / "source"
        frozen_source.mkdir()
        source_manifest = load_object(
            (source / "source_manifest.json").resolve(),
            "source manifest",
        )
        for relative, expected in source_manifest["files"].items():
            origin = resolve_inside(
                source / str(relative),
                source,
                f"source closure {relative}",
            )
            if (
                not origin.is_file()
                or origin.is_symlink()
                or sha(origin) != expected
            ):
                raise PromotionError(
                    f"source closure changed during publication: {relative}"
                )
            destination = frozen_source / str(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, destination)
        shutil.copyfile(
            source / "source_manifest.json",
            frozen_source / "source_manifest.json",
        )
        config = formal_config(debug_config, bundle_id, spec)
        (stage / "config.json").write_bytes(canonical(config))
        (runtime / "mysql_parameter_optimizer.py").write_bytes(
            patched_optimizer_payload(source)
        )
        (stage / "launch_formal10.py").write_bytes(
            launcher_payload(source, spec, bundle_id)
        )
        (stage / "formal10_runtime_verifier.py").write_bytes(
            verifier_payload(
                bundle_id,
                closure["execution_resource_authority"],
                closure["execution_storage_authority"],
                closure["source_manifest_sha256"],
            )
        )
        (stage / "formal10_contract.json").write_bytes(CONTRACT.read_bytes())
        (stage / "debug_gate_seal.json").write_bytes(canonical(seal))
        shutil.copyfile(
            evidence_path, stage / "promotion_evidence_manifest.json"
        )
        frozen_promotion_seal = stage / "promotion_seal"
        frozen_promotion_seal.mkdir()
        for origin in promotion_seal_root.rglob("*"):
            if origin.is_symlink():
                raise PromotionError(
                    "promotion seal contains a symlink"
                )
            relative = origin.relative_to(promotion_seal_root)
            destination = frozen_promotion_seal / relative
            if origin.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif origin.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, destination)
        (stage / "closure.json").write_bytes(canonical(closure))
        readiness = {
            "schema_version": "mysql_algorithm_c_formal10_readiness_v1",
            "status": "READY",
            "launch_authorized": True,
            "outer_execution_authorized": True,
            "algorithm_c_execution_authorized": True,
            "accepted_round_authorized": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "paired_result_merge_prohibited": True,
            "bundle_id": bundle_id,
            "bundle_path": str(final),
            "scenario": spec["scenario"],
            "source_bundle_id": closure["source_bundle_id"],
            "source_manifest_sha256": closure["source_manifest_sha256"],
            "execution_resource_authority":
                closure["execution_resource_authority"],
            "execution_storage_authority":
                closure["execution_storage_authority"],
            "target_accepted_rounds": 10,
            "initial_accepted_rounds": 0,
            "fresh_in_band_r0_required_for_formal_campaign": True,
            "pointers": {
                "config": {
                    "path": str(final / "config.json"),
                    "sha256": sha(stage / "config.json"),
                },
                "formal10_contract": {
                    "path": str(final / "formal10_contract.json"),
                    "sha256": CONTRACT_SHA256,
                },
                "debug_gate_seal": {
                    "path": str(final / "debug_gate_seal.json"),
                    "sha256": sha(stage / "debug_gate_seal.json"),
                },
                "bundle_manifest": {
                    "path": str(final / "bundle_manifest.json"),
                    "sha256": "DEFERRED",
                },
            },
        }
        # The runtime verifier checks the manifest pointer.  To avoid a
        # self-hash cycle, the pointer binds an immutable authority manifest
        # which excludes the readiness filename/hash.
        authority_manifest = {**closure, "bundle_id": bundle_id}
        (stage / "authority_manifest.json").write_bytes(
            canonical(authority_manifest)
        )
        readiness["pointers"]["bundle_manifest"] = {
            "path": str(final / "authority_manifest.json"),
            "sha256": sha(stage / "authority_manifest.json"),
        }
        readiness_payload = canonical(readiness)
        readiness_name = (
            "readiness_manifest-"
            + sha_bytes(readiness_payload)
            + ".json"
        )
        (stage / readiness_name).write_bytes(readiness_payload)
        files = {
            str(path.relative_to(stage)): sha(path)
            for path in stage.rglob("*")
            if path.is_file()
        }
        manifest = {
            **closure,
            "bundle_id": bundle_id,
            "source_directory": str(source),
            "formal_output_root": str(
                ROOT / "history/mysql_gradient_formal10"
                / str(spec["scenario"]) / bundle_id
            ),
            "files": dict(sorted(files.items())),
            "immutable": True,
        }
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        verify_formal_bundle(stage, bundle_id)
        for path in sorted(stage.rglob("*"), reverse=True):
            path.chmod(0o555 if path.is_dir() else 0o444)
        stage.chmod(0o555)
        stage.rename(final)
        verify_formal_bundle(final, bundle_id)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return final


def verify_formal_bundle(bundle: Path, bundle_id: str) -> None:
    manifest = load_object(
        (bundle / "bundle_manifest.json").resolve(),
        "formal bundle manifest",
    )
    if (
        manifest.get("bundle_id") != bundle_id
        or sha(bundle / "closure.json") != bundle_id
        or manifest.get("accepted_increment_requires")
        != [
            "fresh_runtime_gate_pass",
            "complete_fixed_top5_real_replay",
            "outer_qps_acceptance_pass",
            "complete_l1_l2_l3_evidence",
            "atomic_checkpoint_commit",
        ]
    ):
        raise PromotionError("formal bundle identity differs")
    for relative, expected in manifest.get("files", {}).items():
        path = bundle / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or sha(path) != expected
        ):
            raise PromotionError(f"formal bundle file differs: {relative}")
    config = load_object((bundle / "config.json").resolve(), "formal config")
    outer = config.get("formal_outer_protocol") or {}
    policy = config.get("formal10_authorization") or {}
    if (
        config.get("accepted_round_authorized") is not True
        or outer.get("accepted_shrink_count") != 0
        or outer.get("target_accepted_shrinks") != 10
        or outer.get("max_outer_attempts") != 100
        or outer.get("formal_search_initial_replay_top_k") != 5
        or outer.get("formal_search_max_replay_top_k") != 5
        or config.get("minimum_measurement_seconds") != 180
        or config.get("measurement_seconds") != 600
        or policy.get("atomic_checkpoint_required_before_increment_visible")
        is not True
    ):
        raise PromotionError("formal config boundary differs")
    runtime = (
        bundle / "runtime/mysql_parameter_optimizer.py"
    ).read_text(encoding="utf-8")
    required_runtime = (
        "_formal10_complete_top5_gate",
        "_formal10_atomic_checkpoint",
        "complete_l1_l2_l3_required_before_increment",
        "accepted_count_visible_after_atomic_commit",
    )
    if any(item not in runtime for item in required_runtime):
        raise PromotionError("formal runtime accepted boundary is incomplete")
    source_manifest_path = bundle / "source/source_manifest.json"
    source_manifest = load_object(
        source_manifest_path.resolve(), "formal source manifest"
    )
    if sha(source_manifest_path) != manifest.get("source_manifest_sha256"):
        raise PromotionError("formal source manifest differs")
    for relative, expected in source_manifest.get("files", {}).items():
        path = bundle / "source" / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or sha(path) != expected
        ):
            raise PromotionError(f"formal source file differs: {relative}")
    promotion_seal = bundle / "promotion_seal"
    promotion_closure_path = promotion_seal / "seal_closure.json"
    promotion_closure = load_object(
        promotion_closure_path.resolve(),
        "formal frozen promotion seal closure",
    )
    if sha(promotion_closure_path) != manifest.get(
        "promotion_seal_closure_sha256"
    ):
        raise PromotionError("formal promotion seal closure differs")
    observed_promotion_files = {
        str(path.relative_to(promotion_seal)): sha(path)
        for path in promotion_seal.rglob("*")
        if path.is_file() and path != promotion_closure_path
    }
    if observed_promotion_files != promotion_closure.get("files"):
        raise PromotionError("formal promotion seal files differ")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("job", "tpch"), required=True)
    parser.add_argument("--evidence-manifest", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        seal, manifest, config, source = audit_evidence(
            args.scenario, args.evidence_manifest.resolve()
        )
        result: dict[str, Any] = {
            "status": "PROMOTION_GATE_PASS",
            "scenario": args.scenario,
            "debug_bundle_id": manifest["bundle_id"],
            "debug_gate_seal_sha256": sha_bytes(canonical(seal)),
            "formal10_published": False,
        }
        if args.execute:
            bundle = publish(
                args.scenario,
                args.evidence_manifest.resolve(),
                seal,
                manifest,
                config,
                source,
            )
            result.update({
                "status": "FORMAL10_PUBLISHED",
                "formal10_published": True,
                "formal10_bundle": str(bundle),
                "formal10_bundle_id": bundle.name,
                "formal10_manifest_sha256":
                    sha(bundle / "bundle_manifest.json"),
            })
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except PromotionError as exc:
        print(json.dumps({
            "status": "PROMOTION_GATE_BLOCKED",
            "scenario": args.scenario,
            "formal10_published": False,
            "reason": str(exc),
        }, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
