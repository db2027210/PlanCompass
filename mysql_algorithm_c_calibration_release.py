#!/usr/bin/env python3
"""Immutable, compatibility-checked MySQL Algorithm C calibration releases.

Only a real-replay T admitted with at least ten training rows and five
independent holdout rows may be published.  Reuse never replaces the current
run's baseline controls, dynamic interference gate, resource readback, or XP
reset.  A compatibility or full-EXPLAIN fingerprint mismatch resolves to fresh
calibration rather than fail-open reuse.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Iterable, Mapping
import math


RELEASE_CONTRACT = "mysql_algorithm_c_verified_calibration_release_v1"
COMPATIBILITY_CONTRACT = "mysql_algorithm_c_calibration_compatibility_v1"
REQUEST_CONTRACT = "mysql_algorithm_c_calibration_release_request_v1"
ARTIFACT_CONTRACT = "mysql_algorithm_c_calibrated_experience_table_v1"
EXPLAIN_CONTRACT = "mysql_algorithm_c_full_explain_fingerprint_v1"
MINIMUM_TRAIN_COUNT = 10
MINIMUM_INDEPENDENT_HOLDOUT_COUNT = 5
CALIBRATED_PARAMETERS = (
    "io_block_read_cost",
    "memory_block_read_cost",
    "row_evaluate_cost",
    "key_compare_cost",
    "disk_temptable_create_cost",
    "disk_temptable_row_cost",
    "memory_temptable_row_cost",
)
DEFAULT_RELEASE_ROOT = (
    Path(__file__).resolve().parent
    / "config/formal/frozen_mysql_algorithm_c_calibration_releases_v1"
)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"invalid JSONL {path}:{line_number}: {error}") from error
            if not isinstance(value, dict):
                raise RuntimeError(f"JSON object required: {path}:{line_number}")
            result.append(value)
    return result


def _required_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def validate_compatibility_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(value))
    workload = _required_mapping(payload.get("workload"), "workload")
    concurrency = _required_mapping(payload.get("concurrency"), "concurrency")
    snapshot = _required_mapping(payload.get("snapshot"), "snapshot")
    capacity = _required_mapping(payload.get("resource_capacity"), "resource capacity")
    model = _required_mapping(payload.get("plan_feature_model"), "plan feature model")
    parameters = payload.get("optimizer_parameters")
    if not isinstance(parameters, list):
        raise RuntimeError("optimizer parameter signature list required")
    names = [str(row.get("name")) for row in parameters if isinstance(row, Mapping)]
    if (
        payload.get("contract") != COMPATIBILITY_CONTRACT
        or payload.get("database") != "mysql"
        or payload.get("formal_scope") != "mysql_only"
        or payload.get("cross_engine_comparison_prohibited") is not True
        or not str(payload.get("scene") or "")
        or not str(workload.get("benchmark") or "")
        or not str(workload.get("workload_version") or "")
        or not str(workload.get("workload_contract_sha256") or "")
        or workload.get("full_workload_required") is not True
        or not str(concurrency.get("contract_sha256") or "")
        or not str(snapshot.get("name") or "")
        or not str(snapshot.get("sha256") or "")
        or float(capacity.get("cpu_quota_cores") or 0.0) <= 0.0
        or int(capacity.get("memory_bytes") or 0) <= 0
        or not str(model.get("contract_sha256") or "")
        or not str(model.get("implementation_sha256") or "")
        or names != list(CALIBRATED_PARAMETERS)
        or len(parameters) != len(CALIBRATED_PARAMETERS)
    ):
        raise RuntimeError("calibration compatibility payload is incomplete")
    for row, expected_name in zip(parameters, CALIBRATED_PARAMETERS, strict=True):
        row = _required_mapping(row, f"optimizer parameter {expected_name}")
        if (
            row.get("name") != expected_name
            or not isinstance(row.get("active"), bool)
            or float(row.get("initial") or 0.0) <= 0.0
            or (
                row["active"]
                and (
                    float(row.get("lower") or 0.0) <= 0.0
                    or float(row.get("upper") or 0.0) <= float(row.get("lower") or 0.0)
                )
            )
        ):
            raise RuntimeError(f"optimizer parameter signature differs: {expected_name}")
    return payload


def compatibility_sha256(value: Mapping[str, Any]) -> str:
    return canonical_sha256(validate_compatibility_payload(value))


def verify_artifact_internal_sha(artifact: Mapping[str, Any]) -> str:
    expected = str(artifact.get("artifact_sha256") or "")
    payload = copy.deepcopy(dict(artifact))
    payload.pop("artifact_sha256", None)
    actual = canonical_sha256(payload)
    if not expected or expected != actual:
        raise RuntimeError("calibration artifact internal SHA mismatch")
    return expected


def validate_calibration_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(artifact))
    # Reject historical/operator-physics priors with the release-contract
    # diagnostic before attempting to dereference fields that only exist on a
    # real 10+5 calibration artifact.
    if (
        value.get("schema_version") != ARTIFACT_CONTRACT
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("source") != "mysql_real_replay_calibration"
        or value.get("real_measurement") is not True
        or value.get("qps_used_for_resource_model") is not False
        or value.get("calibrated_parameters") != list(CALIBRATED_PARAMETERS)
    ):
        raise RuntimeError("calibration artifact is not admitted by 10+5 contract")
    admission = _required_mapping(value.get("formal_admission"), "formal admission")
    fit = _required_mapping(value.get("fit"), "fit")
    if (
        admission.get("ready") is not True
        or admission.get("errors") not in ([], None)
        or int(admission.get("minimum_train_count") or 0) < MINIMUM_TRAIN_COUNT
        or int(admission.get("minimum_holdout_count") or 0)
        < MINIMUM_INDEPENDENT_HOLDOUT_COUNT
        or int(fit.get("train_count") or 0) < MINIMUM_TRAIN_COUNT
        or int(fit.get("holdout_count") or 0) < MINIMUM_INDEPENDENT_HOLDOUT_COUNT
        or fit.get("independent_holdout") is not True
        or fit.get("holdout_used_for_model_selection") is not False
        or fit.get("holdout_refit_into_model") is not False
    ):
        raise RuntimeError("calibration artifact is not admitted by 10+5 contract")
    table = _required_mapping(value.get("table"), "calibrated T")
    if set(table) != set(CALIBRATED_PARAMETERS):
        raise RuntimeError("calibrated T does not cover the seven parameters")
    for name in CALIBRATED_PARAMETERS:
        effects = _required_mapping(table[name], f"T row {name}")
        if set(effects) != {"cpu", "memory", "io"} or any(
            not isinstance(effect, (int, float))
            or not math.isfinite(float(effect))
            for effect in effects.values()
        ):
            raise RuntimeError(f"calibrated T row differs: {name}")
    verify_artifact_internal_sha(value)
    return value


def _parameter_vector(row: Mapping[str, Any]) -> tuple[str, ...]:
    params = _required_mapping(row.get("params"), "replay params")
    if not all(name in params and float(params[name]) > 0.0 for name in CALIBRATED_PARAMETERS):
        raise RuntimeError("replay seven-parameter vector is incomplete")
    return tuple(format(float(params[name]), ".17g") for name in CALIBRATED_PARAMETERS)


def validate_replay_split(
    rows: Iterable[Mapping[str, Any]], *, scene: str, split: str,
) -> tuple[list[dict[str, Any]], set[str], set[tuple[str, ...]]]:
    validated: list[dict[str, Any]] = []
    ids: set[str] = set()
    vectors: set[tuple[str, ...]] = set()
    for raw in rows:
        row = copy.deepcopy(dict(raw))
        replay_id = str(row.get("replay_id") or "")
        vector = _parameter_vector(row)
        if (
            not replay_id
            or replay_id in ids
            or vector in vectors
            or row.get("scene") != scene
            or row.get("split") != split
            or row.get("formal_scope") != "mysql_only"
            or row.get("cross_engine_comparison_prohibited") is not True
            or row.get("real_replay") is not True
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            or row.get("technical_failure") is not False
            or row.get("qps_used_for_resource_model") is not False
            or not str(row.get("workload_contract_sha256") or "")
            or not str(row.get("full_explain_fingerprint_sha256") or "")
        ):
            raise RuntimeError(f"invalid {split} replay evidence: {replay_id or '<missing>'}")
        ids.add(replay_id)
        vectors.add(vector)
        validated.append(row)
    minimum = (
        MINIMUM_TRAIN_COUNT if split == "train"
        else MINIMUM_INDEPENDENT_HOLDOUT_COUNT
    )
    if len(validated) < minimum:
        raise RuntimeError(f"{split} replay count below {minimum}")
    return validated, ids, vectors


def validate_full_explain_fingerprint(value: Mapping[str, Any]) -> dict[str, Any]:
    fingerprint = copy.deepcopy(dict(value))
    queries = _required_mapping(fingerprint.get("query_fingerprints"), "query fingerprints")
    expected = int(fingerprint.get("expected_query_count") or 0)
    if (
        fingerprint.get("contract") != EXPLAIN_CONTRACT
        or fingerprint.get("complete") is not True
        or expected <= 0
        or len(queries) != expected
        or fingerprint.get("missing_query_ids") not in ([], None)
        or not str(fingerprint.get("aggregate_sha256") or "")
        or canonical_sha256(queries) != fingerprint.get("aggregate_sha256")
        or any(not str(key) or not str(item) for key, item in queries.items())
    ):
        raise RuntimeError("full EXPLAIN fingerprint is incomplete")
    return fingerprint


def _bound_request_path(
    request: Mapping[str, Any], path_key: str, sha_key: str,
) -> Path:
    path = Path(str(request.get(path_key) or "")).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular release evidence required: {path}")
    if file_sha256(path) != str(request.get(sha_key) or ""):
        raise RuntimeError(f"release evidence SHA mismatch: {path}")
    return path


def publish_calibration_release(
    *, request_path: Path, release_root: Path = DEFAULT_RELEASE_ROOT,
) -> dict[str, Any]:
    request = read_json(request_path)
    if (
        request.get("schema_version") != REQUEST_CONTRACT
        or request.get("formal_scope") != "mysql_only"
        or request.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("calibration release request contract differs")
    scene = str(request.get("scene") or "")
    compatibility = validate_compatibility_payload(
        _required_mapping(request.get("compatibility_payload"), "compatibility payload")
    )
    if compatibility.get("scene") != scene:
        raise RuntimeError("release scene and compatibility scene differ")
    artifact_path = _bound_request_path(
        request, "experience_table_path", "experience_table_sha256"
    )
    train_path = _bound_request_path(request, "train_replays_path", "train_replays_sha256")
    holdout_path = _bound_request_path(
        request, "holdout_replays_path", "holdout_replays_sha256"
    )
    predictions_path = _bound_request_path(
        request, "plan_predictions_path", "plan_predictions_sha256"
    )
    contract_path = _bound_request_path(
        request, "workload_contract_path", "workload_contract_sha256"
    )
    config_path = _bound_request_path(
        request, "fresh_calibration_config_path", "fresh_calibration_config_sha256"
    )
    artifact = validate_calibration_artifact(read_json(artifact_path))
    if artifact.get("scene") != scene:
        raise RuntimeError("calibration artifact scene differs")
    train, train_ids, train_vectors = validate_replay_split(
        read_jsonl(train_path), scene=scene, split="train"
    )
    holdout, holdout_ids, holdout_vectors = validate_replay_split(
        read_jsonl(holdout_path), scene=scene, split="holdout"
    )
    if train_ids & holdout_ids or train_vectors & holdout_vectors:
        raise RuntimeError("independent holdout overlaps training evidence")
    if (
        int(artifact["fit"]["train_count"]) != len(train)
        or int(artifact["fit"]["holdout_count"]) != len(holdout)
    ):
        raise RuntimeError("artifact fit counts differ from replay evidence")
    predictions = read_json(predictions_path)
    baseline = validate_full_explain_fingerprint(
        _required_mapping(predictions.get("baseline"), "baseline fingerprint")
    )
    point_ids = set(_required_mapping(predictions.get("points"), "point predictions"))
    if predictions.get("complete") is not True or point_ids != train_ids | holdout_ids:
        raise RuntimeError("plan prediction coverage differs from 10+5 replays")
    workload_contract = read_json(contract_path)
    if (
        canonical_sha256(workload_contract)
        != compatibility["workload"]["workload_contract_sha256"]
    ):
        raise RuntimeError("workload contract compatibility SHA differs")

    artifact_sha = verify_artifact_internal_sha(artifact)
    compatibility_sha = compatibility_sha256(compatibility)
    release_dir = release_root / scene / compatibility_sha / artifact_sha
    release_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "experience_table.json": artifact_path,
        "train_replays.jsonl": train_path,
        "holdout_replays.jsonl": holdout_path,
        "plan_predictions.json": predictions_path,
        "workload_contract.json": contract_path,
        "fresh_calibration_config.json": config_path,
        "release_request.json": request_path,
    }
    evidence: dict[str, Any] = {}
    for name, source in files.items():
        destination = release_dir / name
        if destination.exists():
            if file_sha256(destination) != file_sha256(source):
                raise RuntimeError(f"immutable calibration release conflict: {destination}")
        else:
            shutil.copy2(source, destination)
        evidence[name] = {
            "sha256": file_sha256(destination),
            "size_bytes": destination.stat().st_size,
        }
    manifest = {
        "contract": RELEASE_CONTRACT,
        "immutable": True,
        "published_at_epoch_s": time.time(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": scene,
        "compatibility_contract": COMPATIBILITY_CONTRACT,
        "compatibility_sha256": compatibility_sha,
        "compatibility_payload": compatibility,
        "artifact_internal_sha256": artifact_sha,
        "fit": {
            "train_count": len(train),
            "holdout_count": len(holdout),
            "independent_holdout": True,
            "holdout_used_for_model_selection": False,
            "holdout_refit_into_model": False,
        },
        "baseline_full_explain_fingerprint": baseline,
        "reuse_policy": {
            "full_explain_revalidation_required": True,
            "mismatch_action": "fresh_calibration",
            "replay_15_calibration_points_when_incompatible": True,
            "formal_baseline_controls_required": True,
            "dynamic_noninterference_gate_required": True,
            "resource_readback_required": True,
            "xp_reset_required_before_new_measurement": True,
        },
        "evidence": evidence,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    manifest_path = release_dir / "release_manifest.json"
    if manifest_path.exists():
        existing = verify_release_manifest(manifest_path)
        if existing["manifest_sha256"] != manifest["manifest_sha256"]:
            # Publishing time is the only permitted non-semantic difference.
            old = copy.deepcopy(existing)
            new = copy.deepcopy(manifest)
            for row in (old, new):
                row.pop("published_at_epoch_s", None)
                row.pop("manifest_sha256", None)
                row.pop("manifest_path", None)
            if old != new:
                raise RuntimeError(f"immutable release manifest conflict: {manifest_path}")
        manifest = read_json(manifest_path)
    else:
        write_json_atomic(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path)}


def verify_release_manifest(
    manifest_path: Path, *, compatibility: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    expected_sha = str(manifest.get("manifest_sha256") or "")
    payload = copy.deepcopy(manifest)
    payload.pop("manifest_sha256", None)
    if (
        manifest.get("contract") != RELEASE_CONTRACT
        or manifest.get("immutable") is not True
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or canonical_sha256(payload) != expected_sha
    ):
        raise RuntimeError("calibration release manifest contract or SHA differs")
    manifest_compatibility = validate_compatibility_payload(
        _required_mapping(manifest.get("compatibility_payload"), "manifest compatibility")
    )
    if compatibility_sha256(manifest_compatibility) != manifest.get("compatibility_sha256"):
        raise RuntimeError("manifest compatibility SHA differs")
    if compatibility is not None and compatibility_sha256(compatibility) != manifest.get(
        "compatibility_sha256"
    ):
        raise RuntimeError("calibration release compatibility mismatch")
    fit = _required_mapping(manifest.get("fit"), "manifest fit")
    if (
        int(fit.get("train_count") or 0) < MINIMUM_TRAIN_COUNT
        or int(fit.get("holdout_count") or 0) < MINIMUM_INDEPENDENT_HOLDOUT_COUNT
        or fit.get("independent_holdout") is not True
        or fit.get("holdout_used_for_model_selection") is not False
        or fit.get("holdout_refit_into_model") is not False
    ):
        raise RuntimeError("release manifest 10+5 fit contract differs")
    validate_full_explain_fingerprint(
        _required_mapping(
            manifest.get("baseline_full_explain_fingerprint"),
            "release baseline fingerprint",
        )
    )
    release_dir = manifest_path.parent
    evidence = _required_mapping(manifest.get("evidence"), "release evidence")
    for name, raw in evidence.items():
        row = _required_mapping(raw, f"evidence {name}")
        path = release_dir / name
        if (
            not path.is_file()
            or path.is_symlink()
            or file_sha256(path) != str(row.get("sha256") or "")
            or path.stat().st_size != int(row.get("size_bytes") or -1)
        ):
            raise RuntimeError(f"release evidence differs: {path}")
    artifact = validate_calibration_artifact(read_json(release_dir / "experience_table.json"))
    if verify_artifact_internal_sha(artifact) != manifest.get("artifact_internal_sha256"):
        raise RuntimeError("release artifact identity differs")
    return {**manifest, "manifest_path": str(manifest_path)}


def find_compatible_calibration_release(
    compatibility: Mapping[str, Any], *, release_root: Path = DEFAULT_RELEASE_ROOT,
) -> dict[str, Any] | None:
    payload = validate_compatibility_payload(compatibility)
    directory = release_root / str(payload["scene"]) / compatibility_sha256(payload)
    if not directory.is_dir():
        return None
    errors: list[str] = []
    for path in sorted(
        directory.glob("*/release_manifest.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        try:
            return verify_release_manifest(path, compatibility=payload)
        except Exception as error:  # corrupt compatible releases fail closed
            errors.append(f"{path}: {type(error).__name__}: {error}")
    if errors:
        raise RuntimeError(f"all compatible calibration releases are corrupt: {errors}")
    return None


def resolve_release_reuse(
    *, compatibility: Mapping[str, Any], observed_full_explain: Mapping[str, Any],
    release_root: Path = DEFAULT_RELEASE_ROOT,
) -> dict[str, Any]:
    payload = validate_compatibility_payload(compatibility)
    release = find_compatible_calibration_release(payload, release_root=release_root)
    common = {
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "compatibility_sha256": compatibility_sha256(payload),
        "formal_baseline_controls_required": True,
        "dynamic_noninterference_gate_required": True,
        "resource_readback_required": True,
    }
    if release is None:
        return {
            **common,
            "status": "FRESH_CALIBRATION_REQUIRED",
            "reason": "compatible_release_not_found",
            "replay_count_required": 15,
        }
    try:
        observed = validate_full_explain_fingerprint(observed_full_explain)
    except Exception as error:
        return {
            **common,
            "status": "FRESH_CALIBRATION_REQUIRED",
            "reason": "observed_full_explain_incomplete",
            "detail": f"{type(error).__name__}: {error}",
            "replay_count_required": 15,
        }
    expected = release["baseline_full_explain_fingerprint"]
    if observed != expected:
        expected_queries = expected.get("query_fingerprints") or {}
        observed_queries = observed.get("query_fingerprints") or {}
        changed = sorted(
            set(expected_queries) | set(observed_queries),
            key=str,
        )
        changed = [
            query for query in changed
            if expected_queries.get(query) != observed_queries.get(query)
        ]
        return {
            **common,
            "status": "FRESH_CALIBRATION_REQUIRED",
            "reason": "full_explain_fingerprint_mismatch",
            "changed_query_ids": changed,
            "release_manifest_path": release["manifest_path"],
            "release_manifest_sha256": release["manifest_sha256"],
            "replay_count_required": 15,
        }
    return {
        **common,
        "status": "REUSE_CALIBRATION_RELEASE",
        "reason": "compatibility_and_full_explain_match",
        "release_manifest_path": release["manifest_path"],
        "release_manifest_sha256": release["manifest_sha256"],
        "artifact_internal_sha256": release["artifact_internal_sha256"],
        "replay_count_skipped": 15,
    }


def materialize_calibration_release(
    manifest: Mapping[str, Any], *, destination: Path,
) -> dict[str, Any]:
    path = Path(str(manifest.get("manifest_path") or "")).resolve()
    verified = verify_release_manifest(path)
    source = path.parent / "experience_table.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "manifest_path": str(path),
        "manifest_sha256": verified["manifest_sha256"],
        "artifact_internal_sha256": verified["artifact_internal_sha256"],
        "materialized_path": str(destination),
        "materialized_sha256": file_sha256(destination),
        "formal_baseline_controls_required": True,
    }
