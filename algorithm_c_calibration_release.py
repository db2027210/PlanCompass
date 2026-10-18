#!/usr/bin/env python3
"""Immutable, compatibility-checked Algorithm C calibration releases."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


RELEASE_CONTRACT = "algorithm_c_verified_calibration_release_v1"
COMPATIBILITY_CONTRACT = "algorithm_c_calibration_compatibility_v1"
STRUCTURAL_ADMISSION_CONTRACT = (
    "hard_structural_prediction_quality_diagnostic_v3"
)
RESOURCE_QUALITY_ADMISSION_CONTRACT = (
    "independent_grouped_holdout_resource_quality_v4"
)
DEFAULT_RELEASE_ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_calibration_releases_v1"
)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _normalized_plan_evidence(value: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("path", None)
    return result


def _normalized_native_xp(value: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(dict(value))
    result.pop("statement_file", None)
    result.pop("native_jar_file", None)
    return result


def normalized_workload_contract(value: Mapping[str, Any]) -> Dict[str, Any]:
    """Remove run-local locations while preserving experiment semantics."""
    result = copy.deepcopy(dict(value))
    if isinstance(result.get("plan_evidence"), Mapping):
        result["plan_evidence"] = _normalized_plan_evidence(
            result["plan_evidence"]
        )
    if isinstance(result.get("native_xp"), Mapping):
        result["native_xp"] = _normalized_native_xp(result["native_xp"])
    database = result.get("database")
    if isinstance(database, Mapping):
        normalized_database = {
            key: copy.deepcopy(item)
            for key, item in database.items()
            if key != "workers"
        }
        workers = database.get("workers")
        if isinstance(workers, list):
            normalized_database["workers"] = [
                {
                    "cpuset_cpus": worker.get("cpuset_cpus"),
                }
                for worker in workers
                if isinstance(worker, Mapping)
            ]
        result["database"] = normalized_database
    return result


def _normalized_hybench(value: Mapping[str, Any]) -> Dict[str, Any]:
    ignored = {
        "base_props_file",
        "hybench_dir",
        "launcher",
        "props_output_dir",
        "statement_file",
        "native_jar_file",
    }
    payload = {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in ignored
    }
    return payload


def calibration_compatibility_payload(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    algorithm = cfg.get("algorithm_c") or {}
    if not isinstance(algorithm, Mapping):
        raise ValueError("algorithm_c config missing")
    workload_contract = algorithm.get("workload_contract") or {}
    if not isinstance(workload_contract, Mapping):
        raise ValueError("algorithm_c workload contract missing")
    validation = (
        ((cfg.get("binary_plan_tuning") or {}).get("validation") or {})
        if isinstance(cfg.get("binary_plan_tuning") or {}, Mapping)
        else {}
    )
    hybench = validation.get("hybench_xp") or {}
    docker_runtime = validation.get("docker_runtime") or {}
    executor_telemetry = validation.get("executor_telemetry") or {}
    restore_barrier = validation.get("shared_disk_restore_barrier") or {}
    payload = {
        "contract": COMPATIBILITY_CONTRACT,
        "scene": algorithm.get("scene"),
        "benchmark": algorithm.get("benchmark"),
        "target_resource": workload_contract.get("target_resource"),
        "active_params": copy.deepcopy(algorithm.get("active_params") or []),
        "initial_params": copy.deepcopy(algorithm.get("initial_params") or {}),
        "expected_plan_query_count": algorithm.get("expected_plan_query_count"),
        "resource_estimation_contract": algorithm.get(
            "resource_estimation_contract"
        ),
        "resource_estimation_contract_version": algorithm.get(
            "resource_estimation_contract_version"
        ),
        "workload_contract": normalized_workload_contract(workload_contract),
        "metrics_plan_estimator": copy.deepcopy(
            ((cfg.get("metrics") or {}).get("plan_estimator") or {})
        ),
        "plan_capture": copy.deepcopy(cfg.get("plan_capture") or {}),
        "param_ranges": copy.deepcopy(cfg.get("param_ranges") or {}),
        "validation": {
            "hybench_xp": (
                _normalized_hybench(hybench)
                if isinstance(hybench, Mapping)
                else {}
            ),
            "docker_runtime": copy.deepcopy(docker_runtime),
            "executor_telemetry": copy.deepcopy(executor_telemetry),
            "shared_disk_restore_barrier": copy.deepcopy(restore_barrier),
        },
    }
    # Preserve compatibility with releases published before support-domain
    # abstention existed.  Missing and explicit false have identical
    # semantics; true remains signature-changing and cannot reuse a legacy
    # calibration.
    if algorithm.get("target_support_abstention_enabled") is True:
        payload["target_support_abstention_enabled"] = True
    return payload


def calibration_compatibility_sha256(cfg: Mapping[str, Any]) -> str:
    return canonical_sha256(calibration_compatibility_payload(cfg))


def verify_artifact_internal_sha(artifact: Mapping[str, Any]) -> str:
    expected = str(artifact.get("artifact_sha256") or "")
    payload = copy.deepcopy(dict(artifact))
    payload.pop("artifact_sha256", None)
    actual = canonical_sha256(payload)
    if not expected or expected != actual:
        raise RuntimeError("calibration artifact internal SHA mismatch")
    return expected


def required_release_sample_counts(
    admission: Mapping[str, Any],
) -> Dict[str, int]:
    """Return sample minima encoded by the artifact's admission contract.

    New resource-quality artifacts serialize their 20/10 minima explicitly.
    Legacy artifacts retain their historical requirements and cannot be
    silently reinterpreted under the stricter model contract.
    """

    contract = admission.get("scored_resource_admission_contract")
    if contract in {
        STRUCTURAL_ADMISSION_CONTRACT,
        RESOURCE_QUALITY_ADMISSION_CONTRACT,
    }:
        minimum_train_count = int(admission.get("minimum_train_count") or 0)
        minimum_holdout_count = int(admission.get("minimum_holdout_count") or 0)
        required_train = (
            20 if contract == RESOURCE_QUALITY_ADMISSION_CONTRACT else 7
        )
        required_holdout = (
            10 if contract == RESOURCE_QUALITY_ADMISSION_CONTRACT else 3
        )
        if (
            minimum_train_count < required_train
            or minimum_holdout_count < required_holdout
        ):
            raise RuntimeError(
                "calibration admission sample minima are invalid"
            )
        return {
            "train_count": minimum_train_count,
            "holdout_count": minimum_holdout_count,
        }
    return {"train_count": 10, "holdout_count": 5}


def publish_calibration_release(
    *,
    cfg: Mapping[str, Any],
    source_dir: Path,
    artifact_path: Path,
    release_root: Path = DEFAULT_RELEASE_ROOT,
) -> Dict[str, Any]:
    """Publish a complete calibration as an immutable content-addressed release."""
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact_sha = verify_artifact_internal_sha(artifact)
    admission = artifact.get("formal_admission") or {}
    fit = artifact.get("fit") or {}
    if admission.get("ready") is not True:
        raise RuntimeError("cannot publish calibration that failed formal admission")
    required_counts = required_release_sample_counts(admission)
    if (
        int(fit.get("train_count") or 0) < required_counts["train_count"]
        or int(fit.get("holdout_count") or 0)
        < required_counts["holdout_count"]
    ):
        raise RuntimeError(
            "cannot publish calibration without admission-contract sample "
            f"counts train={required_counts['train_count']} "
            f"holdout={required_counts['holdout_count']}"
        )
    prediction_path = source_dir / "calibration_plan_predictions.json"
    replay_path = source_dir / "calibration_replays.jsonl"
    contract_path = source_dir / "workload_contract.json"
    config_path = source_dir / "optimizer_config.json"
    required_paths = (prediction_path, replay_path, contract_path, config_path)
    missing = [str(path) for path in required_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"calibration release evidence missing: {missing}")
    predictions = json.loads(prediction_path.read_text(encoding="utf-8"))
    baseline_fingerprint = str(predictions.get("baseline_plan_fingerprint") or "")
    baseline_coverage = predictions.get("baseline_plan_coverage") or {}
    if not baseline_fingerprint or baseline_coverage.get("complete") is not True:
        raise RuntimeError("calibration release baseline plan evidence incomplete")

    compatibility_payload = calibration_compatibility_payload(cfg)
    compatibility_sha = canonical_sha256(compatibility_payload)
    scene = str((cfg.get("algorithm_c") or {}).get("scene") or "")
    if not scene:
        raise RuntimeError("calibration release scene missing")
    release_dir = release_root / scene / compatibility_sha / artifact_sha
    release_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "experience_table.json": artifact_path,
        "calibration_plan_predictions.json": prediction_path,
        "calibration_replays.jsonl": replay_path,
        "workload_contract.json": contract_path,
        "optimizer_config.json": config_path,
    }
    evidence: Dict[str, Any] = {}
    for destination_name, source_path in files.items():
        destination = release_dir / destination_name
        if destination.is_file():
            if file_sha256(destination) != file_sha256(source_path):
                raise RuntimeError(f"immutable calibration release conflict: {destination}")
        else:
            shutil.copy2(source_path, destination)
        evidence[destination_name] = {
            "sha256": file_sha256(destination),
            "size_bytes": destination.stat().st_size,
        }
    manifest = {
        "contract": RELEASE_CONTRACT,
        "immutable": True,
        "published_at_epoch_s": time.time(),
        "scene": scene,
        "benchmark": (cfg.get("algorithm_c") or {}).get("benchmark"),
        "target_resource": (
            ((cfg.get("algorithm_c") or {}).get("workload_contract") or {}).get(
                "target_resource"
            )
        ),
        "compatibility_contract": COMPATIBILITY_CONTRACT,
        "compatibility_sha256": compatibility_sha,
        "compatibility_payload": compatibility_payload,
        "artifact_internal_sha256": artifact_sha,
        "baseline_plan_fingerprint": baseline_fingerprint,
        "baseline_plan_coverage": copy.deepcopy(baseline_coverage),
        "fit": {
            "train_count": int(fit.get("train_count") or 0),
            "holdout_count": int(fit.get("holdout_count") or 0),
            "holdout_outer_rounds": copy.deepcopy(
                fit.get("holdout_outer_rounds") or []
            ),
        },
        "required_release_sample_counts": required_counts,
        "formal_admission": copy.deepcopy(admission),
        "source_dir": str(source_dir),
        "evidence": evidence,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    manifest_path = release_dir / "release_manifest.json"
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing_without_time = copy.deepcopy(existing)
        manifest_without_time = copy.deepcopy(manifest)
        for value in (existing_without_time, manifest_without_time):
            value.pop("published_at_epoch_s", None)
            value.pop("manifest_sha256", None)
        if existing_without_time != manifest_without_time:
            raise RuntimeError(f"immutable calibration manifest conflict: {manifest_path}")
        manifest = existing
    else:
        write_json_atomic(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path)}


def verify_release_manifest(
    manifest_path: Path,
    *,
    cfg: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("contract") != RELEASE_CONTRACT or manifest.get("immutable") is not True:
        raise RuntimeError("calibration release manifest contract mismatch")
    expected_manifest_sha = str(manifest.get("manifest_sha256") or "")
    payload = copy.deepcopy(manifest)
    payload.pop("manifest_sha256", None)
    if not expected_manifest_sha or canonical_sha256(payload) != expected_manifest_sha:
        raise RuntimeError("calibration release manifest SHA mismatch")
    if cfg is not None:
        expected_compatibility = calibration_compatibility_sha256(cfg)
        if str(manifest.get("compatibility_sha256") or "") != expected_compatibility:
            raise RuntimeError("calibration release compatibility mismatch")
    release_dir = manifest_path.parent
    evidence = manifest.get("evidence") or {}
    for name, row in evidence.items():
        path = release_dir / str(name)
        if not path.is_file():
            raise FileNotFoundError(path)
        if file_sha256(path) != str((row or {}).get("sha256") or ""):
            raise RuntimeError(f"calibration release evidence SHA mismatch: {path}")
    artifact = json.loads(
        (release_dir / "experience_table.json").read_text(encoding="utf-8")
    )
    if verify_artifact_internal_sha(artifact) != str(
        manifest.get("artifact_internal_sha256") or ""
    ):
        raise RuntimeError("calibration release artifact identity mismatch")
    return {**manifest, "manifest_path": str(manifest_path)}


def find_compatible_calibration_release(
    cfg: Mapping[str, Any],
    *,
    release_root: Path = DEFAULT_RELEASE_ROOT,
) -> Optional[Dict[str, Any]]:
    algorithm = cfg.get("algorithm_c") or {}
    scene = str(algorithm.get("scene") or "")
    compatibility_sha = calibration_compatibility_sha256(cfg)
    compatibility_dir = release_root / scene / compatibility_sha
    if not compatibility_dir.is_dir():
        return None
    candidates = sorted(
        compatibility_dir.glob("*/release_manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    errors = []
    for path in candidates:
        try:
            return verify_release_manifest(path, cfg=cfg)
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError(f"all compatible calibration releases are corrupt: {errors}")
    return None


def materialize_calibration_release(
    manifest: Mapping[str, Any],
    *,
    destination: Path,
) -> Dict[str, Any]:
    manifest_path = Path(str(manifest.get("manifest_path") or ""))
    verified = verify_release_manifest(manifest_path)
    source = manifest_path.parent / "experience_table.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "manifest_path": str(manifest_path),
        "manifest_sha256": verified["manifest_sha256"],
        "artifact_internal_sha256": verified["artifact_internal_sha256"],
        "materialized_path": str(destination),
        "materialized_file_sha256": file_sha256(destination),
    }
