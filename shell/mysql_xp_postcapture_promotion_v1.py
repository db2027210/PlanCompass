#!/usr/bin/env python3
"""Fail-closed XP post-capture/debug/formal10 promotion.

This module is deliberately offline.  It only validates immutable evidence
and publishes content-addressed files beneath an explicitly supplied output
root.  It never starts a workload, contacts MySQL, invokes Docker/QEMU, or
changes another process.

There are two authority transitions:

* a real a7d238 XP capture may become a formal-debug input only after exact
  AT1..AT6, worker-Future, token/hash and cleanup evidence passes; and
* a debug input may become a formal10 input only after a fresh in-band R0,
  all 127 subsets, the sealed fixed Top-5 real replays, per-rank journals and
  prediction-vs-real ranking audit all pass.

Synthetic evidence is useful for testing this validator but can never be
published or returned as launch authority.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SOURCE_BUNDLE_ID = (
    "a7d2386cd2a703b6bb73e5269cb8cace807b5fb0eb5fb3d5ccb6e71c2b9caeed"
)
SOURCE_BUNDLE = ROOT / "artifacts/mysql_xp_v8" / SOURCE_BUNDLE_ID
FORMAL10_CONTRACT = (
    ROOT / "preflight/mysql_algorithm_c_formal10_contract_20260729.json"
)
FORMAL10_CONTRACT_SHA256 = (
    "2f3d7775e005a31dc5b950277ec5153c2d4b76b67ca7bb42a4f52b5026ae5641"
)
ENVELOPE = {
    "cpu_cores": 5.3,
    "cpuset": "184-199,440-455",
    "mems": "1",
    "read_bps": 29360128,
    "write_bps": 524288,
}
ACTIVE_PARAMETERS = (
    "io_block_read_cost",
    "memory_block_read_cost",
    "row_evaluate_cost",
    "key_compare_cost",
    "disk_temptable_create_cost",
    "disk_temptable_row_cost",
    "memory_temptable_row_cost",
)
REQUIRED_AT = tuple(f"AT{index}" for index in range(1, 7))
REQUIRED_WORKERS = tuple(
    [f"XAP-{index}" for index in range(1, 9)]
    + [f"XTP-{index}" for index in range(1, 9)]
)
GRADIENT_FIELDS = (
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
RUNTIME_PATCH_MARKER = "XP_STRICT_ELAPSED_CADENCE_V1"


class PromotionError(RuntimeError):
    """Evidence is incomplete, inconsistent, synthetic, or otherwise unsafe."""


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _regular_file(path: Path) -> Path:
    if path.is_symlink():
        raise PromotionError(f"symlink rejected: {path}")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise PromotionError(f"regular file required: {path}")
    return resolved


def _load_json(path: Path) -> dict[str, Any]:
    path = _regular_file(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise PromotionError(f"JSON object required: {path}")
    return value


def _sidecar_valid(path: Path) -> bool:
    sidecar = path.with_name(path.name + ".sha256")
    return (
        sidecar.is_file()
        and not sidecar.is_symlink()
        and sidecar.read_text(encoding="ascii")
        == f"{sha256_file(path)}  {path.name}\n"
    )


def _verified_json(path: Path) -> dict[str, Any]:
    path = _regular_file(path)
    if not _sidecar_valid(path):
        raise PromotionError(f"checksum sidecar absent or differs: {path}")
    return _load_json(path)


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise PromotionError(f"{label} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PromotionError(f"{label} must be finite") from exc
    if not math.isfinite(number):
        raise PromotionError(f"{label} must be finite")
    return number


def _assert_real_evidence(document: Mapping[str, Any], schema: str) -> None:
    if (
        document.get("schema_version") != schema
        or document.get("evidence_origin") != "real_data_plane"
        or document.get("synthetic") is not False
    ):
        raise PromotionError(
            f"{schema} requires explicit non-synthetic real_data_plane evidence"
        )


def _contract() -> dict[str, Any]:
    path = _regular_file(FORMAL10_CONTRACT)
    if sha256_file(path) != FORMAL10_CONTRACT_SHA256:
        raise PromotionError("shared formal10 contract hash differs")
    document = _load_json(path)
    # These are checked semantically, not merely copied into an output.
    xp = ((document.get("scenario_prerequisites") or {}).get("xp") or {})
    algorithm = document.get("algorithm") or {}
    space = document.get("candidate_space") or {}
    measurement = document.get("measurement") or {}
    promotion = document.get("debug_to_formal10_promotion") or {}
    formal10 = document.get("formal10") or {}
    publication = document.get("publication") or {}
    if (
        document.get("schema_version")
        != "mysql_algorithm_c_formal10_promotion_contract_v1"
        or document.get("formal_scope") != "mysql_only"
        or document.get("cross_engine_comparison_prohibited") is not True
        or document.get("current_evidence_may_not_authorize_formal10") is not True
        or measurement.get("minimum_measurement_seconds") != 180
        or measurement.get("maximum_measurement_seconds") != 600
        or measurement.get("decision_step_seconds") != 30
        or measurement.get("stop_at_first_stable_valid_boundary") is not True
        or measurement.get("resource_sample_minimum_coverage_ratio") != 0.9
        or measurement.get("database_counter_minimum_coverage_ratio") != 0.9
        or measurement.get("missing_or_timed_out_sampling_action") != "FAIL_CLOSED"
        or algorithm.get("original_gradient_only") is not True
        or algorithm.get("gradient_equation")
        != "g_j = sum_r(dScore_dU_r * T_j_r * confidence)"
        or algorithm.get("candidate_score_qps_input_prohibited") is not True
        or algorithm.get("qps_usage")
        != "outer_acceptance_after_real_replay_only"
        or list(algorithm.get("required_gradient_event_fields") or [])
        != list(GRADIENT_FIELDS)
        or ((space.get("xp") or {}).get("parameter_count")) != 7
        or ((space.get("xp") or {}).get("nonempty_subset_count")) != 127
        or space.get("enumeration") != "all_nonempty_subsets"
        or space.get("ranking") != "global_final_candidate_score"
        or space.get("sealed_replay_top_k") != 5
        or space.get("replay_set_must_remain_fixed") is not True
        or xp.get("required_at_evidence") != list(REQUIRED_AT)
        or xp.get("worker_future_exception_count") != 0
        or (xp.get("formal_envelope") or {}).get("cpu_quota_cores") != 5.3
        or (xp.get("formal_envelope") or {}).get("io_read_bps") != 29360128
        or (xp.get("formal_envelope") or {}).get("io_write_bps") != 524288
        or (xp.get("formal_envelope") or {}).get(
            "graceful_quiescence_seconds"
        )
        != 120
        or xp.get("capture_io_may_not_authorize_r0") is not True
        or promotion.get("fresh_in_band_r0_status") != "PASS"
        or promotion.get("historical_or_capture_r0_prohibited") is not True
        or promotion.get("all_subsets_completed") is not True
        or promotion.get("sealed_ranks") != [1, 2, 3, 4, 5]
        or promotion.get("all_fixed_top5_real_replays_completed") is not True
        or promotion.get("prediction_vs_real_ranking_audit_status") != "PASS"
        or promotion.get("unresolved_sampling_gradient_estimation_fingerprint_issues")
        != []
        or promotion.get("started_without_point_journal_action")
        != "FAIL_CLOSED_NO_RERUN"
        or formal10.get("target_accepted_rounds") != 10
        or formal10.get("initial_accepted_rounds") != 0
        or publication
        != {
            "content_addressed": True,
            "transactional": True,
            "idempotent": True,
            "immutable_after_publish": True,
            "all_executable_inputs_manifest_bound": True,
        }
    ):
        raise PromotionError("shared formal10 contract semantic audit failed")
    return document


def verify_runtime_bundle(bundle: Path, *, source: bool = False) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    manifest = _load_json(bundle / "manifest.json")
    if (
        bundle.name != manifest.get("bundle_id")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or (source and bundle.name != SOURCE_BUNDLE_ID)
        or (source and manifest.get("schema_version")
            != "mysql_xp_v8_immutable_runtime_v1")
        or manifest.get("resource_envelope") != ENVELOPE
        or manifest.get("formal_graceful_quiescence_seconds") != 120
        or manifest.get("active_parameter_count") != 7
        or manifest.get("required_nonempty_subsets") != 127
        or manifest.get("fixed_replay_count") != 5
    ):
        raise PromotionError("XP runtime identity/scope/envelope differs")
    payload = manifest.get("payload_files")
    if not isinstance(payload, Mapping) or not payload:
        raise PromotionError("XP runtime payload closure is absent")
    for relative, expected in payload.items():
        path = _regular_file(bundle / str(relative))
        if sha256_file(path) != expected:
            raise PromotionError(f"XP runtime payload differs: {relative}")
    profile = _load_json(bundle / "profile/hybench_xp_io_formal_v8.json")
    if {key: profile.get(key) for key in ENVELOPE} != ENVELOPE:
        raise PromotionError("XP profile does not enforce the formal envelope")
    _audit_algorithm_sources(bundle)
    return manifest


def _audit_algorithm_sources(bundle: Path) -> None:
    config = _load_json(
        bundle / "config/hybench_xp_io.algorithm_c.optimizer.template.json"
    )
    optimizer = config.get("optimizer") or {}
    outer = config.get("formal_outer_protocol") or {}
    fingerprint = config.get("three_level_fingerprint") or {}
    if (
        tuple((config.get("cost_parameters") or {}).get("active") or ())
        != ACTIVE_PARAMETERS
        or optimizer.get("original_gradient_only") is not True
        or optimizer.get("candidate_generator")
        != "all_nonempty_subsets_experience_table_analytic_sgd_v1"
        or outer.get("formal_search_initial_replay_top_k") != 5
        or outer.get("formal_search_max_replay_top_k") != 5
        or outer.get("target_accepted_shrinks") != 10
        or fingerprint.get("duplicate_rule")
        != "exact_parameter_signature_and_equal_l1_and_approximate_l2_l3_v1"
        or fingerprint.get("missing_evidence_policy") != "independent_singleton"
        or fingerprint.get("qps_role") != "outer_acceptance_only"
        or fingerprint.get("l3_primary_metrics")
        != [
            "shared_read_blocks",
            "shared_write_blocks",
            "local_read_blocks",
            "local_write_blocks",
            "temp_read_blocks",
            "temp_write_blocks",
            "io_wait_ms",
        ]
    ):
        raise PromotionError("XP Algorithm C config contract differs")
    gradient = (
        bundle / "driver/gradient_descent_optimizer.py"
    ).read_text(encoding="utf-8")
    parameter = (
        bundle / "driver/mysql_parameter_optimizer.py"
    ).read_text(encoding="utf-8")
    required_gradient_tokens = (
        "grad_component = pressure * sensitivity * confidence",
        "total_gradient += grad_component",
        '"original_dScore_dU_times_T_times_confidence"',
        "proposed = current_value * (1.0 + current_eta * directions[name])",
    )
    if any(token not in gradient for token in required_gradient_tokens):
        raise PromotionError("XP original-gradient implementation differs")
    for field in GRADIENT_FIELDS:
        if repr(field) not in parameter and f'"{field}"' not in parameter:
            raise PromotionError(f"XP gradient event field absent: {field}")
    if (
        "for size in range(1, len(active) + 1)" not in parameter
        or "itertools.combinations(active, size)" not in parameter
        or "global_raw_all_subset_fixed_top_5_v1" not in parameter
        or "Plan clustering remains diagnostic evidence only" not in parameter
    ):
        raise PromotionError("XP all-subset/global fixed-Top5 contract differs")


def strict_elapsed_cadence_audit(
    epochs_ms: Sequence[int | float],
    *,
    start_epoch_ms: int,
    end_epoch_ms: int,
    nominal_interval_seconds: float = 1.0,
) -> dict[str, Any]:
    """Validate real elapsed coverage and a non-overlapping catch-up schedule.

    A normal interval is [0.5, 1.5] nominal seconds.  One late interval may be
    paired with one immediate short interval only if the pair total is within
    [1.9, 2.1] nominal seconds.  The short interval is consumed by that pair
    and cannot compensate another delay.  Count coverage and real boundary
    elapsed coverage must both be >=0.90.
    """
    nominal = _finite(nominal_interval_seconds, "nominal_interval_seconds")
    if nominal <= 0.0 or end_epoch_ms <= start_epoch_ms:
        raise PromotionError("invalid cadence boundary")
    epochs = [int(value) for value in epochs_ms]
    if not epochs:
        raise PromotionError("sampling evidence is empty")
    if any(
        epoch <= start_epoch_ms or epoch > end_epoch_ms
        for epoch in epochs
    ):
        raise PromotionError("sampling epoch is outside the fixed boundary")
    if any(right <= left for left, right in zip(epochs, epochs[1:])):
        raise PromotionError("sampling epochs are not strictly increasing")
    intervals = [
        (right - left) / 1000.0
        for left, right in zip([start_epoch_ms, *epochs[:-1]], epochs)
    ]
    catchups: list[dict[str, Any]] = []
    index = 0
    while index < len(intervals):
        interval = intervals[index]
        if nominal * 0.5 <= interval <= nominal * 1.5:
            index += 1
            continue
        if interval <= nominal * 1.5:
            raise PromotionError(
                f"pseudo-dense or unpaired sample interval: {interval:.6f}"
            )
        following = intervals[index + 1] if index + 1 < len(intervals) else None
        combined = interval + following if following is not None else None
        if (
            interval > nominal * 2.0
            or following is None
            or not (0.05 * nominal <= following <= 0.5 * nominal)
            or combined is None
            or not (1.9 * nominal <= combined <= 2.1 * nominal)
        ):
            raise PromotionError(
                f"uncompensated sampling gap: {interval:.6f}"
            )
        catchups.append(
            {
                "delayed_sample_index": index + 1,
                "catchup_sample_index": index + 2,
                "delayed_interval_seconds": interval,
                "catchup_interval_seconds": following,
                "combined_interval_seconds": combined,
            }
        )
        index += 2
    duration_seconds = (end_epoch_ms - start_epoch_ms) / 1000.0
    count_coverage = len(epochs) * nominal / duration_seconds
    elapsed_coverage = (epochs[-1] - start_epoch_ms) / (
        end_epoch_ms - start_epoch_ms
    )
    trailing = (end_epoch_ms - epochs[-1]) / 1000.0
    if (
        count_coverage < 0.90
        or elapsed_coverage < 0.90
        or trailing > nominal * 1.5
    ):
        raise PromotionError(
            "sampling real elapsed/count/boundary coverage below contract"
        )
    return {
        "schema_version": "mysql_xp_strict_elapsed_cadence_audit_v1",
        "status": "PASS",
        "policy": RUNTIME_PATCH_MARKER,
        "nominal_interval_seconds": nominal,
        "sample_count": len(epochs),
        "count_coverage_ratio": min(1.0, count_coverage),
        "real_elapsed_coverage_ratio": min(1.0, elapsed_coverage),
        "maximum_interval_seconds": max(intervals),
        "trailing_boundary_gap_seconds": trailing,
        "compensated_catchup_count": len(catchups),
        "compensated_catchups": catchups,
        "missing_or_timed_out_sampling_action": "FAIL_CLOSED",
    }


def _evidence_files(
    manifest: Mapping[str, Any], names: Iterable[str],
) -> dict[str, Path]:
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise PromotionError("evidence files map is absent")
    required = set(names)
    if set(files) != required:
        raise PromotionError(
            f"evidence file roles differ: expected={sorted(required)} "
            f"actual={sorted(files)}"
        )
    result: dict[str, Path] = {}
    for name in sorted(required):
        row = files[name]
        if not isinstance(row, Mapping):
            raise PromotionError(f"invalid evidence file row: {name}")
        path = _regular_file(Path(str(row.get("path") or "")))
        if row.get("sha256") != sha256_file(path) or not _sidecar_valid(path):
            raise PromotionError(f"evidence hash/sidecar differs: {name}")
        result[name] = path
    return result


def _registry_validator(bundle: Path) -> Any:
    path = _regular_file(bundle / "capture/offline_registry/xp_strict_registry.py")
    spec = importlib.util.spec_from_file_location(
        "mysql_xp_postcapture_registry_validator", path
    )
    if spec is None or spec.loader is None:
        raise PromotionError("cannot load bundle-bound XP registry validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def audit_capture_evidence(
    bundle: Path,
    evidence_manifest_path: Path,
    *,
    validator: Callable[..., Mapping[str, Any]] | None = None,
    allow_synthetic_for_test: bool = False,
) -> dict[str, Any]:
    manifest = verify_runtime_bundle(bundle, source=True)
    evidence_manifest_path = _regular_file(evidence_manifest_path)
    if not _sidecar_valid(evidence_manifest_path):
        raise PromotionError("capture evidence manifest sidecar differs")
    evidence = _load_json(evidence_manifest_path)
    if allow_synthetic_for_test:
        if evidence.get("schema_version") != "mysql_xp_postcapture_input_v1":
            raise PromotionError("capture evidence schema differs")
    else:
        _assert_real_evidence(evidence, "mysql_xp_postcapture_input_v1")
    token = str(evidence.get("launch_token") or "")
    if (
        not token
        or evidence.get("runtime_bundle_id") != manifest["bundle_id"]
        or evidence.get("status") != "CAPTURE_COMPLETE"
        or evidence.get("capture_only") is not True
        or evidence.get("r0_authorized") is not False
        or evidence.get("accepted_round_authorized") is not False
    ):
        raise PromotionError("capture evidence identity/authority differs")
    paths = _evidence_files(
        evidence,
        (
            "trace",
            "trace_manifest",
            "runtime_authority",
            "capture_authorization",
            "capture_spec",
            "capture_completion",
            "cleanup",
        ),
    )
    capture_spec = _verified_json(paths["capture_spec"])
    completion = _verified_json(paths["capture_completion"])
    cleanup = _verified_json(paths["cleanup"])
    if (
        capture_spec.get("runtime_bundle_id") != manifest["bundle_id"]
        or capture_spec.get("launch_token") != token
        or capture_spec.get("capture_only") is not True
        or capture_spec.get("outer_execution_authorized") is not False
        or ((capture_spec.get("frozen_resource_envelope") or {}).get(
            "io_read_bps"
        ))
        != 1205862400
        or ((capture_spec.get("frozen_resource_envelope") or {}).get(
            "io_write_bps"
        ))
        != 1205862400
    ):
        raise PromotionError("capture spec identity/envelope differs")
    worker_rows = completion.get("worker_futures")
    worker_map = {
        str(row.get("worker")): row
        for row in worker_rows
        if isinstance(row, Mapping)
    } if isinstance(worker_rows, list) else {}
    if (
        completion.get("schema_version") != "mysql_xp_capture_completion_v1"
        or completion.get("status") != "COMPLETE"
        or completion.get("launch_token") != token
        or completion.get("runtime_bundle_id") != manifest["bundle_id"]
        or completion.get("process_exit_code") != 0
        or completion.get("trace_sealed") is not True
        or completion.get("worker_future_exception_count") != 0
        or completion.get("technical_failure_count") != 0
        or set(worker_map) != set(REQUIRED_WORKERS)
        or any(
            row.get("status") != "SUCCESS"
            or row.get("exception") is not None
            or row.get("cancelled") is not False
            for row in worker_map.values()
        )
    ):
        raise PromotionError("worker Future/capture completion evidence differs")
    bound_hashes = completion.get("artifact_sha256") or {}
    if any(
        bound_hashes.get(name) != sha256_file(paths[name])
        for name in (
            "trace",
            "trace_manifest",
            "runtime_authority",
            "capture_authorization",
            "capture_spec",
        )
    ):
        raise PromotionError("capture completion artifact hashes differ")
    if (
        cleanup.get("schema_version") != "mysql_xp_capture_cleanup_v1"
        or cleanup.get("status") != "PASS"
        or cleanup.get("launch_token") != token
        or cleanup.get("runtime_bundle_id") != manifest["bundle_id"]
        or cleanup.get("graceful_quiescence_seconds") != 120
        or cleanup.get("cleanup_complete") is not True
        or cleanup.get("zero_pressure_verified") is not True
        or cleanup.get("workload_process_absent") is not True
        or cleanup.get("mysql_container_absent") is not True
        or cleanup.get("worker_future_exception_count") != 0
        or cleanup.get("trace_sha256") != sha256_file(paths["trace"])
    ):
        raise PromotionError("capture cleanup/token/hash evidence differs")
    validate = validator
    if validate is None:
        validate = _registry_validator(bundle).validate_xp_capture_trace
    validation = dict(
        validate(
            paths["trace"],
            paths["trace_manifest"],
            paths["runtime_authority"],
            paths["capture_authorization"],
        )
    )
    events = validation.get("events")
    if (
        validation.get("capture_authorized") is not True
        or validation.get("execution_authorized") is not False
        or validation.get("reasons") != []
        or validation.get("run_id") != token
        or not isinstance(events, list)
        or any(
            isinstance(row, Mapping)
            and row.get("event_type") == "technical_failure"
            for row in events
        )
    ):
        raise PromotionError("bundle-bound trace validation failed closed")
    commits = {
        str(row.get("transaction_type"))
        for row in events
        if isinstance(row, Mapping)
        and row.get("event_type") == "commit"
        and row.get("outcome") == "success"
    }
    statements = {
        name: sum(
            1
            for row in events
            if isinstance(row, Mapping)
            and row.get("event_type") == "statement"
            and row.get("outcome") == "success"
            and row.get("transaction_type") == name
        )
        for name in REQUIRED_AT
    }
    if commits != set(REQUIRED_AT) or any(statements[name] < 1 for name in REQUIRED_AT):
        raise PromotionError("real AT1-AT6 statement/commit evidence is incomplete")
    return {
        "schema_version": "mysql_xp_postcapture_registry_seal_v1",
        "status": "SEALED_FOR_FRESH_FORMAL_R0_ONLY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "source_runtime_bundle_id": manifest["bundle_id"],
        "source_bundle_manifest_sha256": sha256_file(bundle / "manifest.json"),
        "formal10_contract_sha256": FORMAL10_CONTRACT_SHA256,
        "launch_token": token,
        "trace_sha256": sha256_file(paths["trace"]),
        "capture_evidence_manifest_sha256": sha256_file(evidence_manifest_path),
        "at_statement_coverage": statements,
        "at_commit_coverage": {name: True for name in REQUIRED_AT},
        "worker_future_success": {name: True for name in REQUIRED_WORKERS},
        "worker_future_exception_count": 0,
        "cleanup_verified": True,
        "capture_io_is_not_r0": True,
        "formal_r0_launch_authorized": True,
        "outer_execution_authorized": False,
        "formal10_launch_authorized": False,
        "accepted_round_authorized": False,
        "sources": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in sorted(paths.items())
        },
    }


def _gradient_event(event: Mapping[str, Any], active_subset: Sequence[str]) -> None:
    missing = [name for name in GRADIENT_FIELDS if name not in event]
    if missing:
        raise PromotionError(f"gradient event fields missing: {missing}")
    if event.get("gradient_source") != (
        "original_dScore_dU_times_T_times_confidence"
    ):
        raise PromotionError("non-original gradient event rejected")
    if any(
        key.lower() in {"qps", "xap_qps", "xtp_tps", "throughput"}
        for key in (event.get("feature_ratio") or {})
    ):
        raise PromotionError("QPS/throughput used by gradient candidate scoring")
    slopes = event.get("dScore/dU")
    table = event.get("T")
    confidence = event.get("confidence")
    gradients = event.get("g_j")
    deltas = event.get("delta_param")
    eta = _finite(event.get("eta"), "gradient eta")
    if not all(
        isinstance(item, Mapping)
        for item in (slopes, table, confidence, gradients, deltas)
    ):
        raise PromotionError("gradient vector evidence is malformed")
    if set(gradients) != set(active_subset):
        raise PromotionError("gradient vector does not match active subset")
    for parameter in active_subset:
        p_slopes = slopes.get(parameter)
        p_table = table.get(parameter)
        if not isinstance(p_slopes, Mapping) or not isinstance(p_table, Mapping):
            raise PromotionError("gradient resource terms are absent")
        resources = set(p_slopes)
        if resources != set(p_table) or not resources:
            raise PromotionError("gradient resource term domains differ")
        conf = _finite(confidence.get(parameter), f"confidence[{parameter}]")
        expected = sum(
            _finite(p_slopes[name], "dScore/dU")
            * _finite(p_table[name], "T")
            * conf
            for name in resources
        )
        observed = _finite(gradients.get(parameter), f"g_j[{parameter}]")
        if not math.isclose(expected, observed, rel_tol=1e-9, abs_tol=1e-12):
            raise PromotionError("g_j is not raw dScore/dU*T*confidence sum")
        delta = deltas.get(parameter)
        if not isinstance(delta, Mapping):
            raise PromotionError("delta_param row absent")
        before = _finite(delta.get("before"), "delta before")
        proposed = _finite(delta.get("proposed_before_clip"), "delta proposed")
        if not math.isclose(
            proposed, before * (1.0 + eta * observed),
            rel_tol=1e-9, abs_tol=1e-12,
        ):
            raise PromotionError("parameter update is not derived from g_j and eta")
    _finite(event.get("candidate_score"), "candidate_score")


def _complete_fingerprint(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise PromotionError("three-level fingerprint absent")
    l1 = value.get("l1")
    l2 = value.get("l2")
    l3 = value.get("l3")
    if not all(isinstance(row, Mapping) for row in (l1, l2, l3)):
        raise PromotionError("L1/L2/L3 evidence is incomplete")
    if not {
        "operator_types", "join_order", "scan_types", "parallel_structure",
    }.issubset(l1):
        raise PromotionError("XP L1 evidence is incomplete")
    if not {
        "workers", "hash_batches", "sort_or_spill",
        "bitmap_lossy_pages", "intermediate_result_sizes",
    }.issubset(l2):
        raise PromotionError("XP L2 evidence is incomplete")
    if not {"shared_local_temp_read_write", "io_wait"}.issubset(l3):
        raise PromotionError("XP L3 evidence is incomplete")


def audit_debug_round(
    debug_bundle: Path,
    evidence_manifest_path: Path,
    *,
    allow_synthetic_for_test: bool = False,
) -> dict[str, Any]:
    manifest = verify_runtime_bundle(debug_bundle)
    evidence_manifest_path = _regular_file(evidence_manifest_path)
    if not _sidecar_valid(evidence_manifest_path):
        raise PromotionError("debug evidence manifest sidecar differs")
    evidence = _load_json(evidence_manifest_path)
    if allow_synthetic_for_test:
        if evidence.get("schema_version") != "mysql_xp_debug_round_input_v1":
            raise PromotionError("debug evidence schema differs")
    else:
        _assert_real_evidence(evidence, "mysql_xp_debug_round_input_v1")
    token = str(evidence.get("attempt_token") or "")
    if (
        not token
        or evidence.get("status") != "DEBUG_ROUND_COMPLETE"
        or evidence.get("runtime_bundle_id") != manifest["bundle_id"]
        or evidence.get("accepted_rounds") != 0
        or evidence.get("debug_round_counts_as_accepted") is not False
    ):
        raise PromotionError("debug evidence identity/accounting differs")
    paths = _evidence_files(
        evidence,
        (
            "formal_r0_authority",
            "candidate_inventory",
            "top5_seal",
            "replay_audit",
            "ranking_audit",
            "cleanup",
        ),
    )
    r0 = _verified_json(paths["formal_r0_authority"])
    candidates = _verified_json(paths["candidate_inventory"])
    top5 = _verified_json(paths["top5_seal"])
    replays = _verified_json(paths["replay_audit"])
    ranking = _verified_json(paths["ranking_audit"])
    cleanup = _verified_json(paths["cleanup"])
    if (
        r0.get("schema_version") != "mysql_xp_formal_point_gate_v1"
        or r0.get("bundle_id") != manifest["bundle_id"]
        or r0.get("status") != "FORMAL_R0_PASSED"
        or r0.get("fresh_in_band") is not True
        or r0.get("capture_or_historical_r0") is not False
        or r0.get("r0_gate_passed") is not True
        or r0.get("correctness_passed") is not True
        or r0.get("stability_passed") is not True
        or r0.get("accepted_round_authorized") is not False
    ):
        raise PromotionError("fresh in-band XP R0 authority is incomplete")
    resource_audit = r0.get("resource_cadence_audit")
    progress_audit = r0.get("progress_cadence_audit")
    if any(
        not isinstance(audit, Mapping)
        or audit.get("status") != "PASS"
        or _finite(audit.get("real_elapsed_coverage_ratio"), "elapsed coverage")
        < 0.90
        or audit.get("policy") != RUNTIME_PATCH_MARKER
        for audit in (resource_audit, progress_audit)
    ):
        raise PromotionError("R0 strict elapsed cadence evidence is incomplete")
    rows = candidates.get("candidates")
    if (
        candidates.get("schema_version")
        != "mysql_xp_all_subset_candidate_inventory_v1"
        or candidates.get("runtime_bundle_id") != manifest["bundle_id"]
        or candidates.get("attempt_token") != token
        or candidates.get("status") != "COMPLETE"
        or candidates.get("active_parameters") != list(ACTIVE_PARAMETERS)
        or candidates.get("expected_nonempty_subsets") != 127
        or candidates.get("completed_nonempty_subsets") != 127
        or candidates.get("ranking") != "global_final_candidate_score"
        or not isinstance(rows, list)
        or len(rows) != 127
    ):
        raise PromotionError("XP all-subset candidate inventory is incomplete")
    expected_subsets = {
        combination
        for size in range(1, 8)
        for combination in itertools.combinations(ACTIVE_PARAMETERS, size)
    }
    observed_subsets: set[tuple[str, ...]] = set()
    scores: list[float] = []
    identities: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise PromotionError("candidate row is malformed")
        subset = tuple(row.get("active_subset") or ())
        identity = str(row.get("sealed_candidate_identity") or "")
        events = row.get("gradient_events")
        if (
            subset not in expected_subsets
            or subset in observed_subsets
            or not identity
            or not isinstance(events, list)
            or not events
        ):
            raise PromotionError("candidate subset/identity/gradient evidence differs")
        for event in events:
            if not isinstance(event, Mapping):
                raise PromotionError("gradient event is malformed")
            _gradient_event(event, subset)
        observed_subsets.add(subset)
        identities.append(identity)
        scores.append(_finite(row.get("candidate_score"), "candidate score"))
    if observed_subsets != expected_subsets or len(set(identities)) != 127:
        raise PromotionError("XP candidate universe is not exact 2^7-1")
    sorted_rows = sorted(
        rows,
        key=lambda row: (
            -_finite(row.get("candidate_score"), "candidate score"),
            str(row.get("sealed_candidate_identity")),
        ),
    )
    sealed = top5.get("sealed_candidates")
    if (
        top5.get("schema_version") != "mysql_xp_fixed_top5_seal_v1"
        or top5.get("runtime_bundle_id") != manifest["bundle_id"]
        or top5.get("attempt_token") != token
        or top5.get("status") != "SEALED"
        or top5.get("fixed") is not True
        or top5.get("replay_top_k") != 5
        or not isinstance(sealed, list)
        or len(sealed) != 5
        or [row.get("rank") for row in sealed] != [1, 2, 3, 4, 5]
        or [row.get("sealed_candidate_identity") for row in sealed]
        != [row.get("sealed_candidate_identity") for row in sorted_rows[:5]]
    ):
        raise PromotionError("sealed Top-5 is not the global score Top-5")
    replay_rows = replays.get("replays")
    if (
        replays.get("schema_version") != "mysql_xp_fixed_top5_replay_audit_v1"
        or replays.get("runtime_bundle_id") != manifest["bundle_id"]
        or replays.get("attempt_token") != token
        or replays.get("status") != "COMPLETE"
        or replays.get("real_replay") is not True
        or replays.get("fixed_replay_set_unchanged") is not True
        or not isinstance(replay_rows, list)
        or len(replay_rows) != 5
    ):
        raise PromotionError("fixed Top-5 real replay audit is incomplete")
    replay_tokens: set[str] = set()
    for rank, (sealed_row, replay) in enumerate(
        zip(sealed, replay_rows), 1
    ):
        if not isinstance(replay, Mapping):
            raise PromotionError("replay row is malformed")
        replay_token = str(replay.get("attempt_token") or "")
        if (
            replay.get("rank") != rank
            or replay.get("sealed_candidate_identity")
            != sealed_row.get("sealed_candidate_identity")
            or replay.get("real_replay") is not True
            or replay.get("status") != "COMPLETE"
            or replay.get("failed") is not False
            or replay.get("point_journal_present") is not True
            or replay.get("point_journal_complete") is not True
            or replay.get("completion_present") is not True
            or replay.get("measurement_present") is not True
            or not replay_token
            or replay_token in replay_tokens
        ):
            raise PromotionError("per-rank replay authority is incomplete/mismatched")
        replay_tokens.add(replay_token)
        for audit_name in ("resource_cadence_audit", "progress_cadence_audit"):
            audit = replay.get(audit_name)
            if (
                not isinstance(audit, Mapping)
                or audit.get("status") != "PASS"
                or audit.get("policy") != RUNTIME_PATCH_MARKER
                or _finite(
                    audit.get("real_elapsed_coverage_ratio"),
                    "replay elapsed coverage",
                )
                < 0.90
            ):
                raise PromotionError("replay strict elapsed cadence is incomplete")
        _complete_fingerprint(replay.get("three_level_fingerprint"))
    if (
        ranking.get("schema_version") != "mysql_xp_ranking_audit_v1"
        or ranking.get("runtime_bundle_id") != manifest["bundle_id"]
        or ranking.get("attempt_token") != token
        or ranking.get("status") != "PASS"
        or ranking.get("predicted_ranking_complete") is not True
        or ranking.get("real_ranking_complete") is not True
        or ranking.get("compared_fixed_top5_only") is not True
        or ranking.get(
            "unresolved_sampling_gradient_estimation_fingerprint_issues"
        )
        != []
    ):
        raise PromotionError("prediction-vs-real ranking audit is unresolved")
    if (
        cleanup.get("schema_version") != "mysql_xp_debug_cleanup_v1"
        or cleanup.get("runtime_bundle_id") != manifest["bundle_id"]
        or cleanup.get("attempt_token") != token
        or cleanup.get("status") != "PASS"
        or cleanup.get("zero_pressure_verified") is not True
        or cleanup.get("exact_costs_restored") is not True
        or cleanup.get("atomic_checkpoint_committed") is not True
    ):
        raise PromotionError("debug cleanup/checkpoint evidence is incomplete")
    return {
        "schema_version": "mysql_xp_formal10_promotion_authority_v1",
        "status": "FORMAL10_INPUT_ELIGIBLE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "runtime_bundle_id": manifest["bundle_id"],
        "runtime_manifest_sha256": sha256_file(debug_bundle / "manifest.json"),
        "formal10_contract_sha256": FORMAL10_CONTRACT_SHA256,
        "debug_evidence_manifest_sha256": sha256_file(evidence_manifest_path),
        "attempt_token": token,
        "fresh_r0_verified": True,
        "candidate_count": 127,
        "sealed_ranks": [1, 2, 3, 4, 5],
        "real_replay_count": 5,
        "ranking_audit_status": "PASS",
        "initial_accepted_rounds": 0,
        "target_accepted_rounds": 10,
        "minimum_measurement_seconds": 180,
        "maximum_measurement_seconds": 600,
        "stop_at_first_stable_valid_boundary": True,
        "qps_role": "outer_acceptance_after_real_replay_only",
        "accepted_round_authorized": False,
    }


_CADENCE_HELPER = r'''

# XP_STRICT_ELAPSED_CADENCE_V1
def _xp_strict_elapsed_cadence(
    rows: Sequence[Mapping[str, Any]], *, start_ms: int, end_ms: int,
) -> Mapping[str, Any]:
    epochs = [int(row["epoch_ms"]) for row in rows]
    if not epochs or any(b <= a for a, b in zip(epochs, epochs[1:])):
        raise FormalHyBenchSessionError("XP sampling epochs are absent/non-monotonic")
    intervals = [
        (right - left) / 1000.0
        for left, right in zip([start_ms, *epochs[:-1]], epochs)
    ]
    catchups = []
    index = 0
    while index < len(intervals):
        interval = intervals[index]
        if 0.5 <= interval <= 1.5:
            index += 1
            continue
        following = intervals[index + 1] if index + 1 < len(intervals) else None
        combined = interval + following if following is not None else None
        if (
            interval <= 1.5 or interval > 2.0 or following is None
            or not (0.05 <= following <= 0.5)
            or combined is None or not (1.9 <= combined <= 2.1)
        ):
            raise FormalHyBenchSessionError(
                "XP sampling cadence gap/pseudo-density failed closed"
            )
        catchups.append({
            "delayed_sample_index": index + 1,
            "catchup_sample_index": index + 2,
            "delayed_interval_seconds": interval,
            "catchup_interval_seconds": following,
            "combined_interval_seconds": combined,
        })
        index += 2
    duration = (end_ms - start_ms) / 1000.0
    count_coverage = len(epochs) / duration
    elapsed_coverage = (epochs[-1] - start_ms) / (end_ms - start_ms)
    trailing = (end_ms - epochs[-1]) / 1000.0
    if count_coverage < 0.90 or elapsed_coverage < 0.90 or trailing > 1.5:
        raise FormalHyBenchSessionError(
            "XP sampling real elapsed/count/boundary coverage below 0.9"
        )
    return {
        "schema_version": "mysql_xp_strict_elapsed_cadence_audit_v1",
        "status": "PASS",
        "policy": "XP_STRICT_ELAPSED_CADENCE_V1",
        "sample_count": len(epochs),
        "count_coverage_ratio": min(1.0, count_coverage),
        "real_elapsed_coverage_ratio": min(1.0, elapsed_coverage),
        "maximum_interval_seconds": max(intervals),
        "trailing_boundary_gap_seconds": trailing,
        "compensated_catchup_count": len(catchups),
        "compensated_catchups": catchups,
        "missing_or_timed_out_sampling_action": "FAIL_CLOSED",
    }
'''


def patch_hybench_sampling(source: str) -> str:
    if RUNTIME_PATCH_MARKER in source:
        return source
    anchor = "\n\nSESSION_MARKER = "
    if source.count(anchor) != 1:
        raise PromotionError("HyBench cadence helper insertion anchor differs")
    source = source.replace(anchor, _CADENCE_HELPER + anchor, 1)
    resource_anchor = '''        if len(resources) < measurement_seconds * 0.9:
            raise FormalHyBenchSessionError("HyBench resource coverage below 0.9")
'''
    if source.count(resource_anchor) != 1:
        raise PromotionError("HyBench resource coverage patch anchor differs")
    source = source.replace(
        resource_anchor,
        resource_anchor
        + '''        resource_cadence_audit = _xp_strict_elapsed_cadence(
            resources, start_ms=start, end_ms=end,
        )
''',
        1,
    )
    progress_anchor = '''        if len(measured) < measurement_seconds * 0.9:
            raise FormalHyBenchSessionError("HyBench native progress coverage below 0.9")
'''
    if source.count(progress_anchor) != 1:
        raise PromotionError("HyBench progress coverage patch anchor differs")
    source = source.replace(
        progress_anchor,
        progress_anchor
        + '''        progress_cadence_audit = _xp_strict_elapsed_cadence(
            measured, start_ms=start, end_ms=end,
        )
''',
        1,
    )
    return_anchor = '''            "resource_sample_coverage": min(1.0, len(resources) / measurement_seconds),
            "progress_sample_coverage": min(1.0, len(measured) / measurement_seconds),
'''
    if source.count(return_anchor) != 1:
        raise PromotionError("HyBench coverage result patch anchor differs")
    source = source.replace(
        return_anchor,
        '''            "resource_sample_coverage": resource_cadence_audit[
                "real_elapsed_coverage_ratio"
            ],
            "progress_sample_coverage": progress_cadence_audit[
                "real_elapsed_coverage_ratio"
            ],
            "resource_cadence_audit": resource_cadence_audit,
            "progress_cadence_audit": progress_cadence_audit,
''',
        1,
    )
    return source


def _make_writable(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        os.chmod(path, 0o755 if path.is_dir() else 0o644)
    os.chmod(root, 0o755)


def _payload_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }


def _publish_tree(
    source_bundle: Path,
    *,
    output_root: Path,
    stage: str,
    authority: Mapping[str, Any],
) -> Path:
    """Copy, patch, bind and atomically publish a successor runtime."""
    if stage not in {"formal_debug", "formal10"}:
        raise PromotionError("unknown XP promotion stage")
    source_manifest = verify_runtime_bundle(source_bundle)
    _contract()
    output_root.mkdir(parents=True, exist_ok=True)
    staging_parent = Path(
        tempfile.mkdtemp(prefix=".mysql_xp_promotion.", dir=output_root)
    )
    staging = staging_parent / "payload"
    try:
        shutil.copytree(source_bundle, staging, symlinks=False)
        _make_writable(staging)
        (staging / "manifest.json").unlink()
        for relative in (
            "orchestration/formal_hybench_continuous_session.py",
            "python/formal_hybench_continuous_session.py",
        ):
            path = staging / relative
            path.write_text(
                patch_hybench_sampling(path.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
        authority_root = staging / "promotion_authority"
        authority_root.mkdir(exist_ok=True)
        authority_name = (
            "postcapture_registry_seal.json"
            if stage == "formal_debug"
            else "formal10_promotion_authority.json"
        )
        (authority_root / authority_name).write_bytes(canonical_bytes(authority))
        (authority_root / "formal10_contract.json").write_bytes(
            FORMAL10_CONTRACT.read_bytes()
        )
        identity = {
            "schema_version": "mysql_xp_promoted_runtime_identity_v1",
            "stage": stage,
            "source_bundle_id": source_manifest["bundle_id"],
            "source_manifest_sha256": sha256_file(source_bundle / "manifest.json"),
            "authority_sha256": canonical_sha256(authority),
            "formal10_contract_sha256": FORMAL10_CONTRACT_SHA256,
            "runtime_patch": RUNTIME_PATCH_MARKER,
        }
        bundle_id = canonical_sha256(identity)
        config_path = (
            staging / "config/hybench_xp_io.algorithm_c.optimizer.template.json"
        )
        config = _load_json(config_path)
        config.update(
            {
                "runtime_bundle_id": bundle_id,
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "measurement_seconds": 600,
                "minimum_measurement_seconds": 180,
                "authorization_stage": stage,
                "formal_r0_launch_authorized": stage == "formal_debug",
                "execution_authorized": stage == "formal10",
                "outer_execution_authorized": stage == "formal10",
                "accepted_round_authorized": False,
            }
        )
        config.setdefault("workload", {})
        config["workload"].update(
            {
                "capture_only": False,
                "formal_graceful_quiescence_seconds": 120,
                "stop_at_first_stable_valid_boundary": True,
                "sampling_cadence_policy": RUNTIME_PATCH_MARKER,
            }
        )
        config_path.write_bytes(canonical_bytes(config))
        payload = _payload_hashes(staging)
        manifest = {
            "schema_version": "mysql_xp_promoted_runtime_v1",
            "bundle_id": bundle_id,
            "promotion_stage": stage,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "source_bundle_id": source_manifest["bundle_id"],
            "source_manifest_sha256": identity["source_manifest_sha256"],
            "formal10_contract": {
                "path": "promotion_authority/formal10_contract.json",
                "sha256": FORMAL10_CONTRACT_SHA256,
                "semantically_verified": True,
            },
            "promotion_authority": {
                "path": f"promotion_authority/{authority_name}",
                "sha256": canonical_sha256(authority),
            },
            "resource_envelope": ENVELOPE,
            "formal_graceful_quiescence_seconds": 120,
            "active_parameter_count": 7,
            "required_nonempty_subsets": 127,
            "fixed_replay_count": 5,
            "sampling_cadence_policy": RUNTIME_PATCH_MARKER,
            "minimum_real_elapsed_coverage_ratio": 0.90,
            "missing_or_timed_out_sampling_action": "FAIL_CLOSED",
            "initial_accepted_rounds": 0,
            "target_accepted_rounds": 10,
            "formal_r0_authorized": stage == "formal_debug",
            "outer_execution_authorized": stage == "formal10",
            "accepted_round_authorized": False,
            "publication": {
                "content_addressed": True,
                "transactional": True,
                "idempotent": True,
                "immutable_after_publish": True,
            },
            "payload_files": payload,
        }
        (staging / "manifest.json").write_bytes(canonical_bytes(manifest))
        destination = output_root / bundle_id
        if destination.exists():
            existing = verify_runtime_bundle(destination)
            if existing != manifest:
                raise PromotionError("idempotent destination identity differs")
            return destination
        publish_staging = output_root / f".{bundle_id}.{os.getpid()}"
        os.rename(staging, publish_staging)
        for path in sorted(publish_staging.rglob("*"), reverse=True):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        os.chmod(publish_staging, 0o555)
        try:
            os.rename(publish_staging, destination)
        except FileExistsError:
            verify_runtime_bundle(destination)
        verify_runtime_bundle(destination)
        return destination
    finally:
        if staging_parent.exists():
            shutil.rmtree(staging_parent)


def publish_debug(
    evidence_manifest: Path, output_root: Path,
) -> tuple[Path, dict[str, Any]]:
    seal = audit_capture_evidence(SOURCE_BUNDLE, evidence_manifest)
    return (
        _publish_tree(
            SOURCE_BUNDLE,
            output_root=output_root,
            stage="formal_debug",
            authority=seal,
        ),
        seal,
    )


def publish_formal10(
    debug_bundle: Path, evidence_manifest: Path, output_root: Path,
) -> tuple[Path, dict[str, Any]]:
    authority = audit_debug_round(debug_bundle, evidence_manifest)
    return (
        _publish_tree(
            debug_bundle,
            output_root=output_root,
            stage="formal10",
            authority=authority,
        ),
        authority,
    )


def _write_report(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise PromotionError(f"fresh report path required: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(value)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha256_file(path)}  {path.name}\n", encoding="ascii"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify-source")
    verify.add_argument("--bundle", type=Path, default=SOURCE_BUNDLE)
    capture = subparsers.add_parser("audit-capture")
    capture.add_argument("--evidence-manifest", type=Path, required=True)
    capture.add_argument("--report", type=Path, required=True)
    debug = subparsers.add_parser("publish-debug")
    debug.add_argument("--evidence-manifest", type=Path, required=True)
    debug.add_argument("--output-root", type=Path, required=True)
    formal10 = subparsers.add_parser("publish-formal10")
    formal10.add_argument("--debug-bundle", type=Path, required=True)
    formal10.add_argument("--evidence-manifest", type=Path, required=True)
    formal10.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "verify-source":
        manifest = verify_runtime_bundle(args.bundle, source=True)
        _contract()
        result = {
            "status": "PASS",
            "bundle_id": manifest["bundle_id"],
            "formal10_contract_sha256": FORMAL10_CONTRACT_SHA256,
            "data_plane_touched": False,
        }
    elif args.command == "audit-capture":
        result = audit_capture_evidence(
            SOURCE_BUNDLE, args.evidence_manifest
        )
        _write_report(args.report, result)
    elif args.command == "publish-debug":
        bundle, authority = publish_debug(
            args.evidence_manifest, args.output_root
        )
        result = {
            "status": "PUBLISHED",
            "bundle": str(bundle),
            "authority_sha256": canonical_sha256(authority),
            "formal10_authorized": False,
        }
    else:
        bundle, authority = publish_formal10(
            args.debug_bundle, args.evidence_manifest, args.output_root
        )
        result = {
            "status": "PUBLISHED",
            "bundle": str(bundle),
            "authority_sha256": canonical_sha256(authority),
            "initial_accepted_rounds": 0,
            "target_accepted_rounds": 10,
        }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "BLOCKED_FAIL_CLOSED",
                    "error": f"{type(exc).__name__}: {exc}",
                    "data_plane_touched": False,
                },
                sort_keys=True,
            )
        )
        raise
