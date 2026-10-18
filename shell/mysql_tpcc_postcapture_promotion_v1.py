#!/usr/bin/env python3
"""Fail-closed TPC-C capture/debug/formal10 promotion pipeline.

This tool is deliberately offline.  It validates and seals evidence that was
already produced by a real compute00 run; it never starts a database or workload.
Synthetic fixtures may call the pure validators, but can never be published.
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
import re
import shutil
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path("/home/anonymous/program/resource_adaptive_mysql")
PROJECT_ROOT_AUTHORITY = str(ROOT)
CAPTURE_BUNDLE_ID = (
    "fe7aaa24d15256f4e771af598ac672cb"
    "72ad9106a02c6dea5e30cc679e55d516"
)
CAPTURE_MANIFEST_SHA256 = (
    "4843e16b627f874d7cd8c1c373ec74d4"
    "d89c928df6fa8a2c224bf9c6536e405c"
)
BINDING_MANIFEST_SHA256 = (
    "689d4bc1558d14042c3e43a2267d3cc3"
    "32d71ba567c635d92180a189c3f90182"
)
COLLECTOR_SHA256 = (
    "f95c21304314474eaca1930fc3fd14931"
    "e3034f2c0ecd875813fa53b7d227994"
)
SHARED_CONTRACT = (
    ROOT / "preflight/mysql_algorithm_c_formal10_contract_20260729.json"
)
SHARED_CONTRACT_SHA256 = (
    "2f3d7775e005a31dc5b950277ec5153c"
    "2d4b76b67ca7bb42a4f52b5026ae5641"
)
CAPTURE_BUNDLE = (
    ROOT / "artifacts/mysql_tpcc_typed_runtime_v1" / CAPTURE_BUNDLE_ID
)
DEFAULT_BASE_DEBUG = (
    ROOT / "artifacts/mysql_tpcc_algorithm_c_local_debug_v1/"
    "e8eec21a8a4467de0d012cd602f45c27"
    "7843312431bccc05a84d874e2bcacdde"
)
DEFAULT_REGISTRY_ROOT = ROOT / "artifacts/mysql_tpcc_typed_trace_registry_v1"
DEFAULT_DEBUG_ROOT = ROOT / "artifacts/mysql_tpcc_algorithm_c_postcapture_debug_v1"
DEFAULT_FORMAL10_ROOT = ROOT / "artifacts/mysql_tpcc_algorithm_c_formal10_v1"
DEFAULT_TOOL_ROOT = ROOT / "artifacts/mysql_tpcc_postcapture_tool_v1"
TRANSACTIONS = ("Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel")
GRADIENT_FIELDS = (
    "feature_ratio", "predicted_resource", "utilization", "dScore/dU",
    "T", "confidence", "g_j", "eta", "delta_param", "candidate_score",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PromotionError(RuntimeError):
    """Any evidence mismatch blocks promotion."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise PromotionError(f"required regular file is absent: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PromotionError(f"invalid JSON: {path}") from error
    if not isinstance(value, dict):
        raise PromotionError(f"JSON object required: {path}")
    return value


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    expected = f"{sha256(path)}  {path.name}\n"
    if (
        sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii") != expected
    ):
        raise PromotionError(f"SHA-256 sidecar differs: {path}")


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise PromotionError(message)


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _reject_qps_resource(value: Any, location: str = "resource") -> None:
    forbidden = {"qps", "throughput", "transactions_per_second"}
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in forbidden:
                raise PromotionError(
                    f"QPS/throughput resource input prohibited: {location}.{key}"
                )
            _reject_qps_resource(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_qps_resource(item, f"{location}[{index}]")


def verify_shared_contract(path: Path = SHARED_CONTRACT) -> dict[str, Any]:
    _expect(sha256(path) == SHARED_CONTRACT_SHA256, "shared formal10 contract hash differs")
    value = load(path)
    measurement = value.get("measurement") or {}
    algorithm = value.get("algorithm") or {}
    space = value.get("candidate_space") or {}
    fingerprint = value.get("fingerprint") or {}
    promotion = value.get("debug_to_formal10_promotion") or {}
    prerequisite = (value.get("scenario_prerequisites") or {}).get("tpcc") or {}
    formal10 = value.get("formal10") or {}
    publication = value.get("publication") or {}
    _expect(value.get("schema_version") == "mysql_algorithm_c_formal10_promotion_contract_v1", "shared contract schema differs")
    _expect(value.get("formal_scope") == "mysql_only", "shared contract scope differs")
    _expect(value.get("cross_engine_comparison_prohibited") is True, "cross-engine prohibition absent")
    _expect(value.get("current_evidence_may_not_authorize_formal10") is True, "current-evidence fail-close absent")
    _expect(measurement == {
        "warmup_seconds": 60,
        "minimum_measurement_seconds": 180,
        "maximum_measurement_seconds": 600,
        "decision_step_seconds": 30,
        "stability_decision_window_seconds": 180,
        "stop_at_first_stable_valid_boundary": True,
        "database_counter_minimum_coverage_ratio": 0.9,
        "resource_sample_minimum_coverage_ratio": 0.9,
        "missing_or_timed_out_sampling_action": "FAIL_CLOSED",
    }, "shared measurement contract differs")
    _expect(algorithm.get("method") == "experience_table_all_subset_sgd", "algorithm method differs")
    _expect(algorithm.get("original_gradient_only") is True, "original gradient is not frozen")
    _expect(algorithm.get("gradient_equation") == "g_j = sum_r(dScore_dU_r * T_j_r * confidence)", "gradient equation differs")
    _expect(algorithm.get("parameter_update_must_be_derived_from") == ["g_j", "eta"], "parameter update authority differs")
    _expect(tuple(algorithm.get("required_gradient_event_fields") or ()) == GRADIENT_FIELDS, "gradient fields differ")
    _expect(algorithm.get("candidate_score_qps_input_prohibited") is True, "candidate QPS prohibition absent")
    _expect(algorithm.get("qps_usage") == "outer_acceptance_after_real_replay_only", "QPS role differs")
    _expect(space.get("tpcc") == {"parameter_count": 4, "nonempty_subset_count": 15}, "TPC-C subset contract differs")
    _expect(space.get("enumeration") == "all_nonempty_subsets", "enumeration differs")
    _expect(space.get("ranking") == "global_final_candidate_score", "ranking differs")
    _expect(space.get("sealed_replay_top_k") == 5 and space.get("replay_set_must_remain_fixed") is True, "Top-5 contract differs")
    _expect(fingerprint.get("l1_required") == ["operator_types", "join_order", "scan_types", "parallel_structure"], "L1 contract differs")
    _expect(fingerprint.get("l2_required") == ["workers", "hash_batches", "sort_or_spill", "bitmap_lossy_pages", "intermediate_result_sizes"], "L2 contract differs")
    _expect((fingerprint.get("l3_required_by_scenario") or {}).get("tpcc") == ["transaction_typed_cpu", "lock_wait", "io", "p50", "p95", "p99"], "TPC-C L3 contract differs")
    _expect(fingerprint.get("duplicate_requires") == ["identical_parameter_signature", "identical_l1", "l2_within_frozen_tolerance", "l3_within_frozen_tolerance"], "duplicate contract differs")
    _expect(fingerprint.get("missing_any_level_action") == "KEEP_AS_INDEPENDENT_CANDIDATE", "missing fingerprint policy differs")
    _expect(promotion.get("fresh_in_band_r0_status") == "PASS" and promotion.get("historical_or_capture_r0_prohibited") is True, "fresh R0 contract differs")
    _expect(promotion.get("all_subsets_completed") is True, "all-subset completion absent")
    _expect(promotion.get("sealed_ranks") == [1, 2, 3, 4, 5], "sealed ranks differ")
    _expect(promotion.get("all_fixed_top5_real_replays_completed") is True, "Top-5 replay completion absent")
    _expect(promotion.get("prediction_vs_real_ranking_audit_status") == "PASS", "ranking audit contract differs")
    _expect(promotion.get("unresolved_sampling_gradient_estimation_fingerprint_issues") == [], "unresolved issue contract differs")
    _expect(promotion.get("per_rank_authority_required") == ["sealed_candidate_identity", "point_journal", "completion", "measurement", "attempt_token"], "per-rank authority differs")
    _expect(promotion.get("started_without_point_journal_action") == "FAIL_CLOSED_NO_RERUN", "point-journal fail-close differs")
    _expect(promotion.get("attempt_token_mismatch_action") == "FAIL_CLOSED", "attempt token fail-close differs")
    _expect(promotion.get("bundle_or_device_authority_mismatch_action") == "FAIL_CLOSED", "bundle/device fail-close differs")
    _expect(prerequisite.get("typed_jdbc_binding_count") == 34, "typed binding prerequisite differs")
    _expect(prerequisite.get("transaction_classes") == list(TRANSACTIONS), "transaction prerequisite differs")
    _expect(prerequisite.get("sensitive_stock_level_normalization_required") is True, "sensitive normalization prerequisite absent")
    _expect(prerequisite.get("qps_resource_input_prohibited") is True, "TPC-C QPS resource prohibition absent")
    _expect(formal10.get("target_accepted_rounds") == 10 and formal10.get("initial_accepted_rounds") == 0, "formal10 counter bounds differ")
    _expect(formal10.get("accepted_increment_requires") == ["fresh_runtime_gate_pass", "complete_fixed_top5_real_replay", "outer_qps_acceptance_pass", "complete_l1_l2_l3_evidence", "atomic_checkpoint_commit"], "accepted increment contract differs")
    _expect(formal10.get("never_count_as_accepted") == ["capture", "r0", "candidate_generation", "failed_replay", "preparation", "debug_round", "failed_gate"], "accepted exclusions differ")
    _expect(publication == {
        "content_addressed": True,
        "transactional": True,
        "idempotent": True,
        "immutable_after_publish": True,
        "all_executable_inputs_manifest_bound": True,
    }, "publication contract differs")
    return value


def verify_capture_bundle(bundle: Path = CAPTURE_BUNDLE) -> dict[str, Any]:
    manifest_path = bundle / "bundle_manifest.json"
    _expect(bundle.name == CAPTURE_BUNDLE_ID, "capture bundle ID differs")
    _expect(sha256(manifest_path) == CAPTURE_MANIFEST_SHA256, "capture manifest hash differs")
    manifest = load(manifest_path)
    _expect(manifest.get("bundle_id") == bundle.name, "capture manifest identity differs")
    _expect(manifest.get("formal_scope") == "mysql_only", "capture scope differs")
    _expect(manifest.get("capture_only") is True, "capture-only flag differs")
    files = manifest.get("files")
    _expect(isinstance(files, dict) and len(files) >= 50, "capture closure is incomplete")
    for relative, expected in files.items():
        path = bundle / relative
        _expect(
            path.is_file() and not path.is_symlink() and sha256(path) == expected,
            f"capture closure differs: {relative}",
        )
    _expect(sha256(bundle / "config/tpcc_offline_binding_manifest.json") == BINDING_MANIFEST_SHA256, "binding manifest differs")
    _expect(sha256(bundle / "collector/tpcc_typed_collector.py") == COLLECTOR_SHA256, "collector differs")
    return manifest


def strict_elapsed_cadence(
    epochs_ms: Sequence[int], *, start_ms: int, end_ms: int
) -> dict[str, Any]:
    _expect(end_ms > start_ms and bool(epochs_ms), "cadence evidence is absent")
    epochs = [int(item) for item in epochs_ms]
    _expect(epochs == sorted(epochs) and len(epochs) == len(set(epochs)), "cadence timestamps are not strictly ordered")
    _expect(epochs[0] > start_ms and epochs[-1] <= end_ms, "cadence timestamps exceed boundary")
    intervals = [
        (current - previous) / 1000.0
        for previous, current in zip([start_ms, *epochs[:-1]], epochs)
    ]
    pairs = []
    index = 0
    while index < len(intervals):
        interval = intervals[index]
        if 0.75 <= interval <= 1.25:
            index += 1
            continue
        if 1.25 < interval <= 1.50 and index + 1 < len(intervals):
            following = intervals[index + 1]
            combined = interval + following
            if 0.50 <= following <= 0.75 and 1.90 <= combined <= 2.10:
                pairs.append([interval, following, combined])
                index += 2
                continue
        raise PromotionError(f"invalid strict elapsed cadence: {interval}")
    seconds = (end_ms - start_ms) / 1000.0
    elapsed_coverage = (epochs[-1] - start_ms) / (end_ms - start_ms)
    count_coverage = len(epochs) / seconds
    _expect(elapsed_coverage >= 0.90, "real elapsed coverage below 0.9")
    _expect(count_coverage >= 0.90, "count coverage below 0.9")
    return {
        "passed": True,
        "real_elapsed_coverage": min(1.0, elapsed_coverage),
        "count_coverage": min(1.0, count_coverage),
        "maximum_interval_seconds": max(intervals),
        "compensated_pair_count": len(pairs),
        "compensated_pairs": pairs,
    }


def verify_sampling_evidence(value: Mapping[str, Any], label: str) -> None:
    start = value.get("measurement_start_epoch_ms")
    end = value.get("measurement_end_epoch_ms")
    _expect(isinstance(start, int) and isinstance(end, int), f"{label}: sampling boundaries absent")
    seconds = (end - start) / 1000 if end > start else 0
    _expect(180 <= seconds <= 600 and seconds % 30 == 0, f"{label}: measurement boundary differs")
    _expect(value.get("stability_window_seconds") == 180, f"{label}: stability window differs")
    _expect(value.get("stopped_at_first_stable_valid_boundary") is True, f"{label}: early-stop boundary unproven")
    for sampler in ("resource_epoch_ms", "progress_epoch_ms", "database_counter_epoch_ms"):
        rows = value.get(sampler)
        _expect(isinstance(rows, list), f"{label}: {sampler} absent")
        strict_elapsed_cadence(rows, start_ms=start, end_ms=end)
    _expect(value.get("missing_or_timed_out_sample_count") == 0, f"{label}: missing/timed-out sample")


def _local_reference(value: Any, field: str) -> Path:
    _expect(isinstance(value, str) and value and "://" not in value, f"{field}: local path required")
    path = Path(value).resolve(strict=True)
    _expect(path.is_file() and not path.is_symlink(), f"{field}: regular file required")
    return path


def _load_collector(bundle: Path):
    path = bundle / "collector/tpcc_typed_collector.py"
    spec = importlib.util.spec_from_file_location("sealed_tpcc_typed_collector", path)
    _expect(spec is not None and spec.loader is not None, "cannot load sealed collector")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_capture_completion(
    completion_path: Path, *, allow_synthetic_fixture: bool = False
) -> dict[str, Any]:
    verify_shared_contract()
    verify_capture_bundle()
    completion_path = completion_path.resolve(strict=True)
    verify_sidecar(completion_path)
    completion = load(completion_path)
    _expect(
        allow_synthetic_fixture
        or completion.get("synthetic_fixture") is not True,
        "synthetic fixture cannot authorize publication",
    )
    _expect(completion.get("schema_version") == "mysql_tpcc_typed_capture_completion_authority_v1", "capture completion schema differs")
    _expect(completion.get("status") == "PASS_CAPTURE_ONLY_COMPLETE", "capture cleanup is incomplete")
    _expect(completion.get("formal_scope") == "mysql_only", "capture completion scope differs")
    _expect(completion.get("cross_engine_comparison_prohibited") is True, "capture comparison prohibition absent")
    token = completion.get("capture_token")
    _expect(isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{16,128}", token) is not None, "capture token differs")
    _expect(completion.get("immutable_runtime_bundle_id") == CAPTURE_BUNDLE_ID, "capture bundle binding differs")
    _expect(completion.get("immutable_runtime_bundle_manifest_sha256") == CAPTURE_MANIFEST_SHA256, "capture manifest binding differs")
    _expect(completion.get("capture_workload_authorized") is True, "capture workload was not authorized")
    for key in (
        "capture_result_is_r0", "capture_result_is_accepted_round",
        "formal_r0_authorized", "algorithm_c_authorized",
        "outer_execution_authorized", "accepted_round_authorized",
    ):
        _expect(completion.get(key) is False, f"capture authority must remain false: {key}")
    _expect(completion.get("qps_resource_input_prohibited") is True, "capture QPS prohibition absent")
    result_path = _local_reference(completion.get("typed_capture_result_path"), "typed_capture_result_path")
    _expect(sha256(result_path) == completion.get("typed_capture_result_sha256"), "capture result hash differs")
    verify_sidecar(result_path)
    result = load(result_path)
    _expect(result.get("status") == "PASS_CAPTURE_ONLY", "capture result status differs")
    _expect(result.get("capture_token") == token, "capture token mismatch")
    _expect(result.get("immutable_runtime_bundle_id") == CAPTURE_BUNDLE_ID, "result bundle binding differs")
    _expect(result.get("immutable_runtime_bundle_manifest_sha256") == CAPTURE_MANIFEST_SHA256, "result manifest binding differs")
    _expect(result.get("bundle_bound_collector_sha256") == COLLECTOR_SHA256, "result collector binding differs")
    _expect(
        result.get("bundle_bound_capture_config_sha256")
        == sha256(CAPTURE_BUNDLE / "capture/tpcc_typed_capture_config_v1.json"),
        "result capture config binding differs",
    )
    _expect(result.get("typed_binding_manifest_sha256") == BINDING_MANIFEST_SHA256, "result binding manifest differs")
    _expect(result.get("observed_binding_count") == 34, "capture is not 34/34")
    _expect(result.get("transaction_types") == ["NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel"], "capture transaction coverage differs")
    _expect(result.get("correctness_invariant_passed") is True, "capture correctness differs")
    _expect((result.get("correctness_evidence") or {}).get("passed") is True, "capture correctness evidence absent")
    _expect(result.get("qps_resource_input_prohibited") is True, "result QPS prohibition absent")
    for key in ("r0", "accepted_round", "formal_r0_authorized", "algorithm_c_authorized", "outer_execution_authorized", "accepted_round_authorized", "execution_authorized"):
        _expect(result.get(key) is False, f"capture result must remain false: {key}")
    references = {
        "trace": ("trace_path", "trace_sha256"),
        "validation": ("collector_validation_path", "collector_validation_sha256"),
        "l2": ("collector_l2_path", "collector_l2_sha256"),
        "l3": ("collector_l3_path", "collector_l3_sha256"),
        "coverage": ("coverage_path", "coverage_sha256"),
        "resource_readback": ("resource_readback_path", "resource_readback_sha256"),
        "effective_readback": ("effective_resource_readback_path", "effective_resource_readback_sha256"),
        "performance_schema": ("performance_schema_readback_path", "performance_schema_readback_sha256"),
        "runtime_gate": ("runtime_process_interference_gate_path", "runtime_process_interference_gate_sha256"),
        "dynamic_gate": ("dynamic_interference_gate_path", "dynamic_interference_gate_sha256"),
    }
    paths: dict[str, Path] = {}
    for label, (path_key, hash_key) in references.items():
        source = completion if label in {"runtime_gate", "dynamic_gate"} else result
        path = _local_reference(source.get(path_key), path_key)
        expected = source.get(hash_key)
        _expect(SHA256_RE.fullmatch(str(expected or "")) is not None and sha256(path) == expected, f"{label} hash differs")
        paths[label] = path
    for field, label in (
        ("trace_sha256", "trace"),
        ("collector_validation_sha256", "validation"),
        ("collector_l2_sha256", "l2"),
        ("collector_l3_sha256", "l3"),
        ("coverage_sha256", "coverage"),
        ("resource_readback_sha256", "resource_readback"),
        ("effective_resource_readback_sha256", "effective_readback"),
        ("performance_schema_readback_sha256", "performance_schema"),
    ):
        _expect(
            completion.get(field) == sha256(paths[label]),
            f"completion/result hash mismatch: {field}",
        )
    _expect(
        completion.get("trace_path") == result.get("trace_path"),
        "completion/result trace path mismatch",
    )
    verify_sidecar(paths["trace"])
    trace_header = json.loads(paths["trace"].read_text(encoding="utf-8").splitlines()[0])
    _expect(trace_header.get("capture_token") == token, "trace token mismatch")
    _expect(trace_header.get("manifest_sha256") == BINDING_MANIFEST_SHA256, "trace manifest mismatch")
    _expect(trace_header.get("capture_only") is True and trace_header.get("outer_execution_authorized") is False, "trace authorization differs")
    _expect(trace_header.get("correctness_verified") is True and trace_header.get("qps_included") is False, "trace correctness/QPS flags differ")
    collector = _load_collector(CAPTURE_BUNDLE)
    rebuilt = collector.collect_and_validate(
        CAPTURE_BUNDLE / "config/tpcc_offline_binding_manifest.json",
        paths["trace"],
    )
    observed_validation = load(paths["validation"])
    _expect(canonical(rebuilt) == canonical(observed_validation), "collector validation does not reproduce")
    _expect(rebuilt.get("binding_count") == 34 and rebuilt.get("observed_binding_count") == 34, "typed binding coverage differs")
    _expect(set(rebuilt.get("transaction_types") or []) == set(TRANSACTIONS), "five transaction classes are incomplete")
    _expect(set((rebuilt.get("l3_vectors") or {}).keys()) == set(TRANSACTIONS), "L3 transaction vectors are incomplete")
    _expect(rebuilt.get("qps_included") is False, "collector contains QPS")
    manifest = load(CAPTURE_BUNDLE / "config/tpcc_offline_binding_manifest.json")
    sensitive = [
        row for row in manifest["statements"]
        if row.get("transaction_type") == "SensitiveStockLevel"
    ]
    _expect(len(sensitive) == 1, "SensitiveStockLevel manifest cardinality differs")
    sensitive_id = sensitive[0]["binding_id"]
    stock_rows = [
        statement
        for transaction in rebuilt["l2_transactions"]
        if transaction["transaction_type"] == "StockLevel"
        for statement in transaction["statements"]
    ]
    _expect(any(row["binding_id"] == sensitive_id for row in stock_rows), "SensitiveStockLevel was not normalized into StockLevel")
    _reject_qps_resource({
        "l2_transactions": rebuilt["l2_transactions"],
        "l3_vectors": rebuilt["l3_vectors"],
    })
    for gate_name in ("runtime_gate", "dynamic_gate"):
        gate = load(paths[gate_name])
        _expect(gate.get("passed") is True and gate.get("status") == "PASS", f"{gate_name} did not pass")
    effective_readback = load(paths["effective_readback"])
    effective = effective_readback.get("effective")
    _expect(
        effective_readback.get("verified") is True
        and effective_readback.get("formal_scope") == "mysql_only"
        and isinstance(effective, Mapping),
        "effective resource readback authority differs",
    )
    _expect(
        effective.get("cpu_quota_us") == 400000
        and effective.get("cpu_period_us") == 100000
        and effective.get("cpuset_cpus") == "208-215,464-471"
        and effective.get("cpuset_mems") == "1"
        and effective.get("memory_bytes") == 8589934592
        and effective.get("memory_swap_bytes") == 0
        and effective.get("pids_max") == 512
        and effective.get("io_read_bps") == 1205862400
        and effective.get("io_write_bps") == 1205862400
        and effective.get("io_device_major_minor") == "8:0"
        and effective.get("buffer_pool_bytes") == 4294967296,
        "capture bundle/device/effective envelope mismatch",
    )
    return {
        "completion_path": str(completion_path),
        "completion_sha256": sha256(completion_path),
        "capture_token": token,
        "trace_sha256": sha256(paths["trace"]),
        "paths": paths,
        "result_path": result_path,
        "result": result,
        "validation": rebuilt,
        "synthetic_fixture": completion.get("synthetic_fixture") is True,
    }


def _readonly(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    for directory in sorted(
        [path for path in root.rglob("*") if path.is_dir()],
        key=lambda path: len(path.parts), reverse=True,
    ) + [root]:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _publish(
    output_root: Path, identity: Mapping[str, Any],
    payloads: Mapping[str, bytes],
) -> Path:
    files = {
        relative: sha256_bytes(payload)
        for relative, payload in sorted(payloads.items())
    }
    unsigned = {**identity, "files": files}
    bundle_id = sha256_bytes(canonical(unsigned))
    final = output_root / bundle_id
    manifest = {**unsigned, "bundle_id": bundle_id}
    if final.exists():
        _expect(not final.is_symlink() and final.is_dir(), "existing publication is not a directory")
        _expect(load(final / "bundle_manifest.json") == manifest, "existing publication identity differs")
        for relative, expected in files.items():
            _expect(sha256(final / relative) == expected, f"existing publication differs: {relative}")
        return final
    output_root.mkdir(parents=True, exist_ok=True)
    stage = output_root / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir()
    try:
        for relative, payload in payloads.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        manifest_path = stage / "bundle_manifest.json"
        with manifest_path.open("xb") as stream:
            stream.write(canonical(manifest))
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_tree(stage)
        _readonly(stage)
        os.rename(stage, final)
        descriptor = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return final


def _verify_content_addressed_manifest(
    directory: Path, manifest: Mapping[str, Any]
) -> None:
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    _expect(
        bundle_id == directory.name
        and sha256_bytes(canonical(unsigned)) == directory.name,
        "content-addressed manifest identity differs",
    )


def seal_capture_registry(
    completion_path: Path, output_root: Path = DEFAULT_REGISTRY_ROOT
) -> Path:
    evidence = validate_capture_completion(completion_path)
    _expect(evidence["synthetic_fixture"] is False, "synthetic capture cannot be sealed")
    paths = evidence["paths"]
    payloads = {
        "capture_completion.json": Path(evidence["completion_path"]).read_bytes(),
        "capture_result.json": evidence["result_path"].read_bytes(),
        "trace.jsonl": paths["trace"].read_bytes(),
        "collector_validation.json": paths["validation"].read_bytes(),
        "l2_transactions.json": paths["l2"].read_bytes(),
        "l3_vectors.json": paths["l3"].read_bytes(),
        "coverage.json": paths["coverage"].read_bytes(),
        "resource_readback.json": paths["resource_readback"].read_bytes(),
        "effective_resource_readback.json": paths["effective_readback"].read_bytes(),
        "performance_schema_readback.json": paths["performance_schema"].read_bytes(),
        "runtime_process_gate.json": paths["runtime_gate"].read_bytes(),
        "dynamic_interference_gate.json": paths["dynamic_gate"].read_bytes(),
        "runtime_bundle_manifest.json": (CAPTURE_BUNDLE / "bundle_manifest.json").read_bytes(),
        "typed_binding_manifest.json": (CAPTURE_BUNDLE / "config/tpcc_offline_binding_manifest.json").read_bytes(),
        "typed_collector.py": (CAPTURE_BUNDLE / "collector/tpcc_typed_collector.py").read_bytes(),
        "capture_config.json": (CAPTURE_BUNDLE / "capture/tpcc_typed_capture_config_v1.json").read_bytes(),
        "formal10_shared_contract.json": SHARED_CONTRACT.read_bytes(),
    }
    identity = {
        "schema_version": "mysql_tpcc_typed_trace_registry_bundle_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "synthetic_fixture": False,
        "capture_bundle_id": CAPTURE_BUNDLE_ID,
        "capture_bundle_manifest_sha256": CAPTURE_MANIFEST_SHA256,
        "capture_token": evidence["capture_token"],
        "trace_sha256": evidence["trace_sha256"],
        "binding_count": 34,
        "observed_binding_count": 34,
        "transaction_types": list(TRANSACTIONS),
        "sensitive_stock_level_normalized_to": "StockLevel",
        "setter_type_position_sql_hash_verified": True,
        "l2_l3_complete": True,
        "qps_resource_input_prohibited": True,
        "capture_result_is_r0": False,
        "capture_result_is_accepted_round": False,
        "outer_execution_authorized": False,
        "accepted_round_authorized": False,
        "formal10_shared_contract_sha256": SHARED_CONTRACT_SHA256,
    }
    return _publish(output_root, identity, payloads)


def verify_sealed_registry(registry: Path) -> dict[str, Any]:
    registry = registry.resolve(strict=True)
    manifest = load(registry / "bundle_manifest.json")
    _verify_content_addressed_manifest(registry, manifest)
    _expect(manifest.get("schema_version") == "mysql_tpcc_typed_trace_registry_bundle_v1", "registry schema differs")
    _expect(manifest.get("synthetic_fixture") is False, "synthetic registry is prohibited")
    _expect(manifest.get("capture_bundle_id") == CAPTURE_BUNDLE_ID, "registry capture bundle differs")
    _expect(manifest.get("binding_count") == 34 and manifest.get("observed_binding_count") == 34, "registry coverage differs")
    _expect(manifest.get("transaction_types") == list(TRANSACTIONS), "registry transactions differ")
    _expect(manifest.get("sensitive_stock_level_normalized_to") == "StockLevel", "registry sensitive normalization differs")
    _expect(manifest.get("setter_type_position_sql_hash_verified") is True, "registry binding semantics unproven")
    _expect(manifest.get("l2_l3_complete") is True, "registry L2/L3 incomplete")
    _expect(manifest.get("qps_resource_input_prohibited") is True, "registry QPS prohibition absent")
    _expect(manifest.get("formal10_shared_contract_sha256") == SHARED_CONTRACT_SHA256, "registry shared contract differs")
    for relative, expected in (manifest.get("files") or {}).items():
        path = registry / relative
        _expect(path.is_file() and not path.is_symlink() and sha256(path) == expected, f"registry closure differs: {relative}")
    verify_shared_contract(registry / "formal10_shared_contract.json")
    completion = load(registry / "capture_completion.json")
    _expect(completion.get("capture_token") == manifest.get("capture_token"), "registry token differs")
    _expect(sha256(registry / "trace.jsonl") == manifest.get("trace_sha256"), "registry trace differs")
    return {
        "path": str(registry),
        "sha256": sha256(registry / "bundle_manifest.json"),
        "capture_token": manifest["capture_token"],
        "trace_sha256": manifest["trace_sha256"],
        "effective_resource_readback_sha256": sha256(registry / "effective_resource_readback.json"),
        "registry_id": registry.name,
    }


def _publish_patched_source(base_debug: Path) -> Path:
    base_manifest = load(base_debug / "bundle_manifest.json")
    source = Path(str(base_manifest["source_directory"])).resolve(strict=True)
    source_manifest = load(source / "source_manifest.json")
    payloads: dict[str, bytes] = {}
    for relative in sorted(source_manifest["files"]):
        candidate = source / relative
        if relative == "formal_benchbase_continuous_session.py":
            candidate = ROOT / "formal_benchbase_continuous_session.py"
        elif relative == "mysql_parameter_optimizer.py":
            candidate = ROOT / "mysql_parameter_optimizer.py"
        payloads[relative] = candidate.read_bytes()
    files = {relative: sha256_bytes(payload) for relative, payload in payloads.items()}
    identity = {
        "schema_version": "mysql_tpcc_postcapture_source_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "base_source_id": source_manifest["bundle_id"],
        "strict_elapsed_cadence_required": True,
        "real_elapsed_coverage_minimum": 0.9,
        "missing_sampling_policy": "fail_closed",
        "files": files,
    }
    source_id = sha256_bytes(canonical(identity))
    root = ROOT / "history/mysql_gradient_debug/readiness_tpcc_postcapture_v1"
    final = root / f"source-{source_id}"
    expected_manifest = {**identity, "bundle_id": source_id}
    if final.exists():
        _expect(load(final / "source_manifest.json") == expected_manifest, "existing postcapture source differs")
        return final
    root.mkdir(parents=True, exist_ok=True)
    stage = root / f".source-{source_id}.tmp.{os.getpid()}"
    stage.mkdir()
    try:
        for relative, payload in payloads.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        (stage / "source_manifest.json").write_bytes(canonical(expected_manifest))
        _fsync_tree(stage)
        _readonly(stage)
        os.rename(stage, final)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return final


def build_bound_debug_bundle(
    registry: Path, base_debug: Path = DEFAULT_BASE_DEBUG,
    output_root: Path = DEFAULT_DEBUG_ROOT,
) -> Path:
    capture = verify_sealed_registry(registry)
    base_debug = base_debug.resolve(strict=True)
    base_manifest = load(base_debug / "bundle_manifest.json")
    _expect(base_manifest.get("algorithm_contract") == {
        "all_nonempty_subset_count": 15,
        "global_fixed_top_k": 5,
        "missing_fingerprint_evidence_policy": "independent_singleton",
        "original_gradient_only": True,
        "parameter_count": 4,
        "qps_role": "outer_acceptance_only",
        "three_level_fingerprint": True,
    }, "base debug algorithm contract differs")
    source = _publish_patched_source(base_debug)
    config = load(base_debug / "config.json")
    config["early_stop_source_bundle"] = {
        "source": str(source),
        "source_manifest_sha256": sha256(source / "source_manifest.json"),
    }
    config["typed_capture_registry"] = {
        "path": "__BUNDLE_CAPTURE_REGISTRY__",
        "registry_id": capture["registry_id"],
        "registry_manifest_sha256": capture["sha256"],
        "capture_token": capture["capture_token"],
        "trace_sha256": capture["trace_sha256"],
    }
    config["fresh_r0_required"] = True
    config["accepted_round_authorized"] = False
    config["formal_outer_protocol"]["max_outer_attempts"] = 1
    config["formal_outer_protocol"]["target_accepted_shrinks"] = 10
    config["formal_outer_protocol"]["accepted_shrink_count"] = 0
    config["measurement_seconds"] = 600
    config["minimum_measurement_seconds"] = 180
    config["sampling_authority"] = {
        "real_elapsed_coverage_minimum": 0.9,
        "strict_compensated_pair_bounds_seconds": {
            "delayed": [1.25, 1.5],
            "catchup": [0.5, 0.75],
            "combined": [1.9, 2.1],
        },
        "resource_progress_database_counter_required": True,
        "missing_or_timeout_action": "FAIL_CLOSED",
    }
    payloads = {
        "config.json": canonical(config),
        "profile.json": (base_debug / "profile.json").read_bytes(),
        "typed_binding_manifest.json": (CAPTURE_BUNDLE / "config/tpcc_offline_binding_manifest.json").read_bytes(),
        "typed_capture_collector.py": (CAPTURE_BUNDLE / "collector/tpcc_typed_collector.py").read_bytes(),
        "typed_capture_bundle_manifest.json": (CAPTURE_BUNDLE / "bundle_manifest.json").read_bytes(),
        "capture_registry/bundle_manifest.json": (registry / "bundle_manifest.json").read_bytes(),
        **{
            f"capture_registry/{relative}": (registry / relative).read_bytes()
            for relative in (load(registry / "bundle_manifest.json").get("files") or {})
        },
        "base_debug_runtime.py": (ROOT / "shell/mysql_tpcc_algorithm_c_local_debug_launcher_v1.py").read_bytes(),
        "launcher.py": (ROOT / "shell/mysql_tpcc_postcapture_debug_launcher_v1.py").read_bytes(),
        "postcapture_promotion.py": Path(__file__).read_bytes(),
        "formal10_shared_contract.json": SHARED_CONTRACT.read_bytes(),
        "debug_promotion_contract.json": canonical({
            "schema_version": "mysql_tpcc_postcapture_debug_contract_v1",
            "fresh_in_band_r0_required": True,
            "historical_or_capture_r0_prohibited": True,
            "all_nonempty_subsets": 15,
            "global_final_candidate_score_sort": True,
            "fixed_real_replay_ranks": [1, 2, 3, 4, 5],
            "accepted_round_authorized": False,
            "sampling": "strict_elapsed_cadence_v1",
        }),
    }
    identity = {
        "schema_version": "mysql_tpcc_algorithm_c_postcapture_debug_bundle_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "capture_registry_id": capture["registry_id"],
        "capture_registry_manifest_sha256": capture["sha256"],
        "capture_bundle_id": CAPTURE_BUNDLE_ID,
        "source_directory": str(source),
        "source_manifest_sha256": sha256(source / "source_manifest.json"),
        "accepted_round_authorized": False,
        "one_debug_round": True,
        "fresh_r0_required": True,
        "formal10_shared_contract_sha256": SHARED_CONTRACT_SHA256,
        "algorithm_contract": base_manifest["algorithm_contract"],
    }
    return _publish(output_root, identity, payloads)


def verify_debug_bundle(bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    manifest = load(bundle / "bundle_manifest.json")
    _verify_content_addressed_manifest(bundle, manifest)
    _expect(manifest.get("schema_version") == "mysql_tpcc_algorithm_c_postcapture_debug_bundle_v1", "debug bundle schema differs")
    _expect(manifest.get("accepted_round_authorized") is False and manifest.get("one_debug_round") is True, "debug authorization differs")
    _expect(manifest.get("fresh_r0_required") is True, "fresh debug R0 is not required")
    _expect(manifest.get("formal10_shared_contract_sha256") == SHARED_CONTRACT_SHA256, "debug shared contract differs")
    for relative, expected in (manifest.get("files") or {}).items():
        path = bundle / relative
        _expect(path.is_file() and not path.is_symlink() and sha256(path) == expected, f"debug closure differs: {relative}")
    verify_shared_contract(bundle / "formal10_shared_contract.json")
    source = Path(str(manifest["source_directory"])).resolve(strict=True)
    source_manifest = load(source / "source_manifest.json")
    _expect(sha256(source / "source_manifest.json") == manifest.get("source_manifest_sha256"), "debug source hash differs")
    _expect(source_manifest.get("strict_elapsed_cadence_required") is True, "strict cadence source absent")
    return manifest


def _expected_subsets(parameters: Sequence[str]) -> set[tuple[str, ...]]:
    return {
        tuple(combo)
        for size in range(1, len(parameters) + 1)
        for combo in itertools.combinations(parameters, size)
    }


def verify_debug_audit(
    debug_bundle: Path, audit_path: Path
) -> dict[str, Any]:
    manifest = verify_debug_bundle(debug_bundle)
    verify_sidecar(audit_path)
    audit = load(audit_path)
    _expect(audit.get("schema_version") == "mysql_tpcc_debug_ranking_audit_v1", "debug audit schema differs")
    _expect(audit.get("status") == "PASS", "debug ranking audit did not pass")
    _expect(audit.get("debug_bundle_id") == debug_bundle.name, "debug audit bundle differs")
    _expect(audit.get("capture_registry_id") == manifest["capture_registry_id"], "debug audit registry differs")
    _expect(audit.get("real_data_plane") is True and audit.get("synthetic_fixture") is False, "debug audit is not real")
    _expect(audit.get("unresolved_issues") == [], "debug audit has unresolved issues")
    r0 = audit.get("fresh_r0") or {}
    _expect(r0.get("status") == "PASS" and r0.get("in_band") is True, "fresh in-band R0 did not pass")
    _expect(r0.get("capture_result") is False and r0.get("historical") is False, "capture/historical R0 prohibited")
    _expect(SHA256_RE.fullmatch(str(r0.get("checkpoint_sha256") or "")) is not None, "R0 checkpoint hash absent")
    verify_sampling_evidence(r0.get("sampling") or {}, "fresh_r0")
    parameters = audit.get("parameters")
    _expect(isinstance(parameters, list) and len(parameters) == 4 and len(set(parameters)) == 4, "debug parameter set differs")
    candidates = audit.get("candidates")
    _expect(isinstance(candidates, list) and len(candidates) == 15, "debug candidate count differs")
    identities = [row.get("candidate_id") for row in candidates]
    _expect(all(isinstance(item, str) and item for item in identities) and len(set(identities)) == 15, "candidate identities differ")
    observed_subsets = set()
    for row in candidates:
        subset = tuple(row.get("subset") or ())
        observed_subsets.add(subset)
        _expect(_finite(row.get("candidate_score")), "candidate score absent")
        events = row.get("gradient_events")
        _expect(isinstance(events, list) and events, "gradient events absent")
        for event in events:
            _expect(all(field in event for field in GRADIENT_FIELDS), "gradient event is incomplete")
            _reject_qps_resource(event)
        fingerprint = row.get("fingerprint") or {}
        levels = [fingerprint.get(name) for name in ("l1", "l2", "l3")]
        if any(level is None for level in levels):
            _expect(row.get("duplicate_disposition") == "KEEP_AS_INDEPENDENT_CANDIDATE", "missing fingerprint was deduplicated")
        else:
            _expect(all(isinstance(level, Mapping) for level in levels), "fingerprint level differs")
    _expect(observed_subsets == _expected_subsets(parameters), "not all 15 nonempty subsets were enumerated")
    predicted = audit.get("predicted_global_order")
    _expect(isinstance(predicted, list) and set(predicted) == set(identities) and len(predicted) == 15, "predicted global order differs")
    score_by_id = {row["candidate_id"]: float(row["candidate_score"]) for row in candidates}
    expected_order = sorted(identities, key=lambda item: (-score_by_id[item], item))
    _expect(predicted == expected_order, "predicted order is not global final candidate_score order")
    sealed = audit.get("sealed_top5")
    _expect(isinstance(sealed, list) and sealed == predicted[:5], "sealed Top-5 is not fixed predicted ranks 1..5")
    replays = audit.get("real_replays")
    _expect(isinstance(replays, list) and len(replays) == 5, "fixed Top-5 replay count differs")
    for rank, replay in enumerate(replays, 1):
        _expect(replay.get("rank") == rank and replay.get("candidate_id") == sealed[rank - 1], "replay rank identity differs")
        _expect(replay.get("status") == "COMPLETED" and replay.get("real_data_plane") is True, "replay did not complete")
        _expect(replay.get("attempt_token") and replay.get("attempt_token") == replay.get("point_journal_attempt_token"), "replay attempt token mismatch")
        for field in ("sealed_candidate_identity_sha256", "point_journal_sha256", "completion_sha256", "measurement_sha256"):
            _expect(SHA256_RE.fullmatch(str(replay.get(field) or "")) is not None, f"rank {rank}: {field} absent")
        _expect(_finite(replay.get("real_score")), "real replay score absent")
        _expect(replay.get("qps_used_for_outer_acceptance_only") is True, "replay QPS role differs")
        verify_sampling_evidence(replay.get("sampling") or {}, f"rank_{rank}")
        _reject_qps_resource(replay.get("resource_inputs") or {})
    actual = audit.get("real_top5_order")
    real_scores = {row["candidate_id"]: float(row["real_score"]) for row in replays}
    expected_actual = sorted(sealed, key=lambda item: (-real_scores[item], item))
    _expect(actual == expected_actual, "real Top-5 order differs")
    _expect((audit.get("prediction_vs_real_ranking_audit") or {}).get("status") == "PASS", "prediction-vs-real audit did not pass")
    _expect(audit.get("accepted_round_authorized") is False, "debug round cannot count as accepted")
    return audit


def strict_accepted_round_count(events: Sequence[Mapping[str, Any]]) -> int:
    accepted = 0
    tokens: set[str] = set()
    never = {
        "capture", "r0", "candidate_generation", "failed_replay",
        "preparation", "debug_round", "failed_gate",
    }
    for event in events:
        token = event.get("attempt_token")
        _expect(isinstance(token, str) and token and token not in tokens, "attempt token missing or reused")
        tokens.add(token)
        kind = event.get("kind")
        claims_accepted = event.get("accepted_round") is True
        if kind in never:
            _expect(not claims_accepted, f"{kind} cannot count as accepted")
            continue
        _expect(kind == "outer_acceptance", "unknown formal10 ledger event")
        if not claims_accepted:
            continue
        required = {
            "fresh_runtime_gate_pass": True,
            "complete_fixed_top5_real_replay": True,
            "outer_qps_acceptance_pass": True,
            "complete_l1_l2_l3_evidence": True,
            "atomic_checkpoint_commit": True,
            "replay_status": "COMPLETED",
            "point_journals_complete": True,
        }
        _expect(all(event.get(key) == value for key, value in required.items()), "accepted increment gate differs")
        _expect(event.get("fixed_top5_replay_count") == 5, "accepted event lacks fixed Top-5")
        _expect(event.get("capture") is False and event.get("r0") is False and event.get("debug_round") is False, "non-formal event counted")
        accepted += 1
        _expect(accepted <= 10, "accepted count exceeds target")
    return accepted


def _cadence_audits(value: Any) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if (
                key in {"resource_cadence_audit", "progress_cadence_audit"}
                and isinstance(item, Mapping)
            ):
                found.append(item)
            found.extend(_cadence_audits(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_cadence_audits(item))
    return found


def verify_formal_campaign_result(
    result_path: Path, point_journal_root: Path | None = None,
) -> dict[str, Any]:
    """Verify the ten-round campaign before emitting completion authority."""
    result = load(result_path)
    summary = result.get("result") or {}
    _expect(
        summary.get("campaign_status") == "complete"
        and summary.get("target_complete") is True
        and summary.get("accepted_shrink_count") == 10
        and summary.get("target_accepted_shrinks") == 10,
        "formal campaign did not complete ten accepted rounds",
    )
    outer = result.get("outer_loop")
    _expect(isinstance(outer, list) and outer, "formal campaign outer loop absent")
    accepted = 0
    tokens: set[str] = set()
    required_evidence = {
        "campaign_method_benchmark_scenario",
        "envelope_and_template_versions",
        "round_candidate_repeat",
        "lane_host_container_port",
        "software_checksums",
        "workload_seed_registry_weights_clients",
        "cpuset_and_numa",
        "resource_limit_readback",
        "fixed_mysql_settings_before_after",
        "requested_effective_costs",
        "fresh_connection_generation",
        "explain_and_fingerprints",
        "estimator_model_formula",
        "per_query_operator_breakdown",
        "per_second_samples",
        "stability_windows_and_coverage",
        "correctness_errors_oom_restarts",
        "utility_components",
        "range_before_after",
        "subset_sgd_trajectory_and_winner",
    }
    for ordinal, row in enumerate(outer, 1):
        inner = row.get("inner_loop") or {}
        _expect(
            inner.get("active_param_count") == 4
            and inner.get("expected_parameter_subsets") == 15
            and inner.get("parameter_subsets_enumerated") == 15
            and inner.get("successful_parameter_subsets") == 15
            and inner.get("failed_parameter_subsets") == 0,
            f"formal outer {ordinal}: all-subset evidence differs",
        )
        _expect(
            inner.get("initial_real_replay_top_k") == 5
            and inner.get("maximum_real_replay_top_k") == 5
            and inner.get("staged_real_replays") == 5,
            f"formal outer {ordinal}: fixed Top-5 differs",
        )
        evaluations = (row.get("real_replay") or {}).get("evaluations")
        _expect(
            isinstance(evaluations, list)
            and len(evaluations) == 5
            and [item.get("candidate_rank") for item in evaluations]
            == [1, 2, 3, 4, 5],
            f"formal outer {ordinal}: replay ranks differ",
        )
        for replay in evaluations:
            token = replay.get("outer_replay_attempt_token")
            validation = replay.get("candidate_final_validation") or {}
            recovery = replay.get("recovery") or {}
            evidence = validation.get("evidence") or {}
            _expect(
                isinstance(token, str)
                and token
                and token not in tokens
                and validation.get("resume_token") == token
                and validation.get("accounting_token") == token
                and recovery.get("resume_token") == token
                and recovery.get("accounting_token") == token,
                f"formal outer {ordinal}: attempt token mismatch",
            )
            tokens.add(token)
            _expect(
                recovery.get("verified") is True
                and required_evidence.issubset(evidence)
                and all(evidence[name] for name in required_evidence),
                f"formal outer {ordinal}: replay completion/evidence incomplete",
            )
            audits = _cadence_audits(validation)
            _expect(
                len(audits) >= 2
                and all(
                    audit.get("passed") is True
                    and float(audit.get("real_elapsed_coverage", 0.0)) >= 0.9
                    and float(audit.get("maximum_interval_seconds", 99.0)) <= 1.5
                    for audit in audits
                ),
                f"formal outer {ordinal}: strict elapsed cadence evidence absent",
            )
        campaign_round = row.get("campaign_round") or {}
        if row.get("outer_accepted") is True:
            accepted += 1
            token = campaign_round.get("accepted_accounting_token")
            _expect(
                campaign_round.get("accepted") is True
                and campaign_round.get("accepted_round_authorized") is True
                and campaign_round.get("atomic_accounting_committed") is True
                and isinstance(token, str)
                and token,
                f"formal outer {ordinal}: atomic accepted commit absent",
            )
            if point_journal_root is not None:
                marker = Path(point_journal_root) / token / "accounted.once"
                _expect(
                    marker.is_file()
                    and not marker.is_symlink()
                    and marker.read_text(encoding="utf-8") == token + "\n",
                    f"formal outer {ordinal}: accounting marker differs",
                )
        else:
            _expect(
                campaign_round.get("atomic_accounting_committed") is False,
                f"formal outer {ordinal}: rejected round was counted",
            )
    _expect(accepted == 10, "formal campaign accepted count does not reconcile")
    return result


def promote_formal10(
    debug_bundle: Path, audit_path: Path,
    output_root: Path = DEFAULT_FORMAL10_ROOT,
) -> Path:
    audit = verify_debug_audit(debug_bundle, audit_path)
    manifest = verify_debug_bundle(debug_bundle)
    config = load(debug_bundle / "config.json")
    config["schema_version"] = "mysql_tpcc_algorithm_c_formal10_config_v1"
    config["accepted_round_authorized"] = True
    config["fresh_r0_required"] = True
    config["minimum_measurement_seconds"] = 180
    config["measurement_seconds"] = 600
    config["formal_outer_protocol"].update({
        "target_accepted_shrinks": 10,
        "accepted_shrink_count": 0,
        "formal_search_initial_replay_top_k": 5,
        "formal_search_max_replay_top_k": 5,
        "max_outer_attempts": 100,
    })
    config["formal10_strict_counting"] = {
        "initial_accepted_rounds": 0,
        "target_accepted_rounds": 10,
        "increment_exactly_once_per_unique_attempt_token": True,
        "accepted_increment_requires": [
            "fresh_runtime_gate_pass",
            "complete_fixed_top5_real_replay",
            "outer_qps_acceptance_pass",
            "complete_l1_l2_l3_evidence",
            "atomic_checkpoint_commit",
        ],
        "never_count": [
            "capture", "r0", "candidate_generation", "failed_replay",
            "preparation", "debug_round", "failed_gate",
        ],
    }
    debug_files = {
        relative: (debug_bundle / relative).read_bytes()
        for relative in (manifest.get("files") or {})
    }
    payloads = {
        **debug_files,
        "config.json": canonical(config),
        "launcher.py": (
            ROOT / "shell/mysql_tpcc_formal10_launcher_v1.py"
        ).read_bytes(),
        "debug_bundle_manifest.json": (debug_bundle / "bundle_manifest.json").read_bytes(),
        "debug_ranking_audit.json": audit_path.read_bytes(),
        "capture_registry_manifest.json": (debug_bundle / "capture_registry/bundle_manifest.json").read_bytes(),
        "formal10_shared_contract.json": SHARED_CONTRACT.read_bytes(),
        "postcapture_promotion.py": Path(__file__).read_bytes(),
        "formal10_contract.json": canonical({
            "schema_version": "mysql_tpcc_formal10_execution_contract_v1",
            "target_accepted_rounds": 10,
            "initial_accepted_rounds": 0,
            "minimum_stable_measurement_seconds": 180,
            "maximum_measurement_seconds": 600,
            "stop_at_first_stable_valid_boundary": True,
            "all_nonempty_subset_count_per_round": 15,
            "fixed_real_replay_top_k_per_round": 5,
            "strict_accepted_counter": True,
            "fresh_runtime_gate_each_round": True,
            "missing_sampling_action": "FAIL_CLOSED",
        }),
    }
    identity = {
        "schema_version": "mysql_tpcc_algorithm_c_formal10_bundle_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "debug_bundle_id": debug_bundle.name,
        "debug_bundle_manifest_sha256": sha256(debug_bundle / "bundle_manifest.json"),
        "debug_ranking_audit_sha256": sha256(audit_path),
        "capture_registry_id": manifest["capture_registry_id"],
        "source_directory": manifest["source_directory"],
        "source_manifest_sha256": manifest["source_manifest_sha256"],
        "formal10_shared_contract_sha256": SHARED_CONTRACT_SHA256,
        "target_accepted_rounds": 10,
        "initial_accepted_rounds": 0,
        "accepted_round_authorized": True,
        "launch_authorized_only_after_fresh_runtime_gate": True,
        "strict_accepted_counter": True,
    }
    return _publish(output_root, identity, payloads)


def verify_formal10_bundle(bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    manifest = load(bundle / "bundle_manifest.json")
    _verify_content_addressed_manifest(bundle, manifest)
    _expect(
        manifest.get("schema_version")
        == "mysql_tpcc_algorithm_c_formal10_bundle_v1",
        "formal10 bundle schema differs",
    )
    _expect(
        manifest.get("formal_scope") == "mysql_only"
        and manifest.get("cross_engine_comparison_prohibited") is True,
        "formal10 scope differs",
    )
    _expect(
        manifest.get("target_accepted_rounds") == 10
        and manifest.get("initial_accepted_rounds") == 0
        and manifest.get("strict_accepted_counter") is True,
        "formal10 accepted counter differs",
    )
    _expect(
        manifest.get("formal10_shared_contract_sha256")
        == SHARED_CONTRACT_SHA256,
        "formal10 shared contract differs",
    )
    for relative, expected in (manifest.get("files") or {}).items():
        path = bundle / relative
        _expect(
            path.is_file()
            and not path.is_symlink()
            and sha256(path) == expected,
            f"formal10 closure differs: {relative}",
        )
    verify_shared_contract(bundle / "formal10_shared_contract.json")
    config = load(bundle / "config.json")
    outer = config.get("formal_outer_protocol") or {}
    _expect(
        config.get("accepted_round_authorized") is True
        and config.get("fresh_r0_required") is True
        and config.get("minimum_measurement_seconds") == 180
        and config.get("measurement_seconds") == 600,
        "formal10 measurement/authorization config differs",
    )
    _expect(
        outer.get("target_accepted_shrinks") == 10
        and outer.get("accepted_shrink_count") == 0
        and outer.get("formal_search_initial_replay_top_k") == 5
        and outer.get("formal_search_max_replay_top_k") == 5
        and outer.get("max_outer_attempts") == 100,
        "formal10 outer-loop config differs",
    )
    source = Path(str(manifest["source_directory"])).resolve(strict=True)
    _expect(
        sha256(source / "source_manifest.json")
        == manifest.get("source_manifest_sha256"),
        "formal10 source authority differs",
    )
    return manifest


def publish_tool_bundle(
    output_root: Path = DEFAULT_TOOL_ROOT,
) -> Path:
    payloads = {
        "postcapture_promotion.py": Path(__file__).read_bytes(),
        "debug_launcher.py": (
            ROOT / "shell/mysql_tpcc_postcapture_debug_launcher_v1.py"
        ).read_bytes(),
        "formal10_launcher.py": (
            ROOT / "shell/mysql_tpcc_formal10_launcher_v1.py"
        ).read_bytes(),
        "tests/test_mysql_tpcc_postcapture_promotion_v1.py": (
            ROOT / "tests/test_mysql_tpcc_postcapture_promotion_v1.py"
        ).read_bytes(),
        "formal10_shared_contract.json": SHARED_CONTRACT.read_bytes(),
        "tool_contract.json": canonical({
            "schema_version": "mysql_tpcc_postcapture_tool_contract_v1",
            "project_root_authority": PROJECT_ROOT_AUTHORITY,
            "project_root_resolution": "manifest_bound_absolute",
            "data_plane_actions": False,
            "runtime_authority_published": False,
            "synthetic_authority_published": False,
            "capture_bundle_id": CAPTURE_BUNDLE_ID,
            "capture_bundle_manifest_sha256": CAPTURE_MANIFEST_SHA256,
            "formal10_shared_contract_sha256": SHARED_CONTRACT_SHA256,
        }),
    }
    identity = {
        "schema_version": "mysql_tpcc_postcapture_tool_bundle_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "project_root_authority": PROJECT_ROOT_AUTHORITY,
        "project_root_resolution": "manifest_bound_absolute",
        "capture_bundle_id": CAPTURE_BUNDLE_ID,
        "capture_bundle_manifest_sha256": CAPTURE_MANIFEST_SHA256,
        "formal10_shared_contract_sha256": SHARED_CONTRACT_SHA256,
        "data_plane_actions": False,
        "runtime_authority_published": False,
        "synthetic_authority_published": False,
    }
    return _publish(output_root, identity, payloads)


def verify_tool_bundle(bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve(strict=True)
    manifest = load(bundle / "bundle_manifest.json")
    _verify_content_addressed_manifest(bundle, manifest)
    _expect(
        manifest.get("schema_version")
        == "mysql_tpcc_postcapture_tool_bundle_v1",
        "tool bundle schema differs",
    )
    _expect(
        manifest.get("project_root_authority") == PROJECT_ROOT_AUTHORITY
        and manifest.get("project_root_resolution")
        == "manifest_bound_absolute",
        "tool project-root authority differs",
    )
    _expect(
        manifest.get("data_plane_actions") is False
        and manifest.get("runtime_authority_published") is False
        and manifest.get("synthetic_authority_published") is False,
        "tool authorization flags differ",
    )
    _expect(
        manifest.get("capture_bundle_manifest_sha256")
        == CAPTURE_MANIFEST_SHA256
        and manifest.get("formal10_shared_contract_sha256")
        == SHARED_CONTRACT_SHA256,
        "tool external authority binding differs",
    )
    for relative, expected in (manifest.get("files") or {}).items():
        path = bundle / relative
        _expect(
            path.is_file()
            and not path.is_symlink()
            and sha256(path) == expected,
            f"tool closure differs: {relative}",
        )
    verify_shared_contract(bundle / "formal10_shared_contract.json")
    verify_capture_bundle()
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    seal = sub.add_parser("seal-capture")
    seal.add_argument("--completion", type=Path, required=True)
    seal.add_argument("--output-root", type=Path, default=DEFAULT_REGISTRY_ROOT)
    debug = sub.add_parser("build-debug")
    debug.add_argument("--registry", type=Path, required=True)
    debug.add_argument("--base-debug", type=Path, default=DEFAULT_BASE_DEBUG)
    debug.add_argument("--output-root", type=Path, default=DEFAULT_DEBUG_ROOT)
    formal = sub.add_parser("promote-formal10")
    formal.add_argument("--debug-bundle", type=Path, required=True)
    formal.add_argument("--debug-audit", type=Path, required=True)
    formal.add_argument("--output-root", type=Path, default=DEFAULT_FORMAL10_ROOT)
    tool = sub.add_parser("publish-tool")
    tool.add_argument("--output-root", type=Path, default=DEFAULT_TOOL_ROOT)
    verify_tool = sub.add_parser("verify-tool")
    verify_tool.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "seal-capture":
        output = seal_capture_registry(args.completion, args.output_root)
    elif args.command == "build-debug":
        output = build_bound_debug_bundle(args.registry, args.base_debug, args.output_root)
    elif args.command == "promote-formal10":
        output = promote_formal10(args.debug_bundle, args.debug_audit, args.output_root)
    elif args.command == "publish-tool":
        output = publish_tool_bundle(args.output_root)
    else:
        verify_tool_bundle(args.bundle)
        output = args.bundle.resolve(strict=True)
    print(json.dumps({
        "status": "PUBLISHED_OR_VERIFIED",
        "path": str(output),
        "bundle_id": output.name,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
