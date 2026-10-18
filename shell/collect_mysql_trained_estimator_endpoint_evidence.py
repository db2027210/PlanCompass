#!/usr/bin/env python3
"""Collect complete, serial MySQL trained-estimator OAT endpoint evidence.

The production CLI is deliberately fail-closed.  It applies one frozen point
at a time, captures the exact feature object consumed by the existing trained
estimator, and publishes the resulting directory only after every endpoint and
the final MySQL restore succeed.  Tests can inject an adapter and estimator
factory without opening a database connection.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, MutableMapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calibrate_mysql_experience_table import (  # noqa: E402
    CalibrationError,
    RESOURCES,
    _canonical_bytes,
    _exact_keys,
    _finite_number,
    _load_json,
    _resolve_checked_pointer,
    _sha256,
)
from mysql_plan_clustering import validate_canonical_plan_bundle  # noqa: E402


REQUEST_SCHEMA = "mysql_trained_estimator_endpoint_collection_request_v1"
EVIDENCE_SCHEMA = "mysql_trained_estimator_endpoint_evidence_v1"
EXPLAIN_SCHEMA = "mysql_explain_bundle_v1"
FEATURE_SCHEMA = "mysql_trained_feature_bundle_v1"
FREEZE_REQUEST_SCHEMA = "mysql_estimator_t_freeze_request_v1"
_SECRET_KEY = re.compile(r"(?:password|passwd|pwd|secret|token|credential|private[_-]?key)", re.I)
_SAFE_PARAM = re.compile(r"[^A-Za-z0-9_.-]+")


def _reject_secrets(value: Any, label: str = "request") -> None:
    """Reject credential-shaped fields before any artifact can be written."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if _SECRET_KEY.search(str(key)):
                raise CalibrationError(f"{label} contains forbidden credential field: {key}")
            _reject_secrets(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secrets(child, f"{label}[{index}]")


def _checked_artifact(
    value: Any, base: Path, label: str, id_fields: Sequence[str] = (),
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CalibrationError(f"{label} must be an object")
    result: dict[str, Any] = {}
    for field in id_fields:
        item = value.get(field)
        if not isinstance(item, str) or not item:
            raise CalibrationError(f"{label}.{field} is required")
        result[field] = item
    path, digest = _resolve_checked_pointer(value.get("artifact"), base, f"{label}.artifact")
    result["artifact"] = {"path": str(path.resolve()), "sha256": digest}
    return result


def _validated_request(request: Mapping[str, Any], request_path: Path) -> dict[str, Any]:
    _reject_secrets(request)
    if request.get("schema_version") != REQUEST_SCHEMA:
        raise CalibrationError(f"schema_version must be {REQUEST_SCHEMA}")
    raw = request.get("binding")
    if not isinstance(raw, Mapping) or raw.get("database") != "mysql":
        raise CalibrationError("binding.database must be exactly mysql")
    strings: dict[str, str] = {}
    for field in ("scenario", "benchmark", "benchmark_version", "version"):
        value = raw.get(field)
        if not isinstance(value, str) or not value:
            raise CalibrationError(f"binding.{field} is required")
        strings[field] = value
    base = request_path.resolve().parent
    workload = _checked_artifact(raw.get("workload"), base, "binding.workload", ("freeze_id",))
    registry = _checked_artifact(raw.get("sql_registry"), base, "binding.sql_registry", ("id",))
    estimator_raw = raw.get("estimator")
    estimator = _checked_artifact(estimator_raw, base, "binding.estimator", ("id", "version"))
    models_raw = estimator_raw.get("model_artifacts") if isinstance(estimator_raw, Mapping) else None
    models_raw = _exact_keys(models_raw, set(RESOURCES), "binding.estimator.model_artifacts")
    estimator["model_artifacts"] = {
        resource: {
            "path": str(path.resolve()),
            "sha256": digest,
        }
        for resource in RESOURCES
        for path, digest in [
            _resolve_checked_pointer(
                models_raw[resource], base, f"binding.estimator.model_artifacts.{resource}"
            )
        ]
    }
    space_raw = raw.get("parameter_space")
    space = _checked_artifact(space_raw, base, "binding.parameter_space", ("version",))
    active = space_raw.get("active_params") if isinstance(space_raw, Mapping) else None
    if (
        not isinstance(active, list)
        or not active
        or len(active) != len(set(active))
        or any(not isinstance(item, str) or not item for item in active)
    ):
        raise CalibrationError("binding.parameter_space.active_params must be unique strings")
    ranges_raw = _exact_keys(
        space_raw.get("ranges"), set(active), "binding.parameter_space.ranges"
    )
    ranges: dict[str, dict[str, float]] = {}
    for param in active:
        bounds = _exact_keys(ranges_raw[param], {"lower", "upper"}, f"ranges.{param}")
        lower = _finite_number(bounds["lower"], f"ranges.{param}.lower", positive=True)
        upper = _finite_number(bounds["upper"], f"ranges.{param}.upper", positive=True)
        if lower >= upper:
            raise CalibrationError(f"ranges.{param} requires lower < upper")
        ranges[param] = {"lower": lower, "upper": upper}
    space.update(active_params=list(active), ranges=ranges)
    anchor_raw = _exact_keys(request.get("anchor"), set(active), "anchor")
    anchor = {
        param: _finite_number(anchor_raw[param], f"anchor.{param}", positive=True)
        for param in active
    }
    for param in active:
        if not ranges[param]["lower"] < anchor[param] < ranges[param]["upper"]:
            raise CalibrationError(f"anchor.{param} must be strictly inside its frozen range")
    config_path, config_sha = _resolve_checked_pointer(
        request.get("trained_estimator_config"), base, "trained_estimator_config"
    )
    tolerance = _finite_number(request.get("sensitivity_tolerance"), "sensitivity_tolerance")
    if tolerance <= 0:
        raise CalibrationError("sensitivity_tolerance must be positive")
    binding = {
        "database": "mysql",
        **strings,
        "workload": workload,
        "sql_registry": registry,
        "estimator": estimator,
        "parameter_space": space,
    }
    _verify_frozen_binding_documents(binding)
    return {
        "binding": binding,
        "anchor": anchor,
        "trained_estimator_config": {
            "path": str(config_path.resolve()), "sha256": config_sha,
        },
        "sensitivity_tolerance": tolerance,
    }


def _registry_query_ids(path: Path) -> list[str]:
    registry = _load_json(path)
    rows = registry.get("queries")
    if not isinstance(rows, list) or not rows:
        raise CalibrationError("SQL registry must contain a non-empty queries list")
    result: list[str] = []
    for order, row in enumerate(rows, 1):
        if not isinstance(row, Mapping) or row.get("order") != order:
            raise CalibrationError(f"SQL registry order is not canonical at row {order}")
        query_id = row.get("query_id")
        if not isinstance(query_id, str) or not query_id:
            raise CalibrationError(f"SQL registry query_id is missing at row {order}")
        result.append(query_id)
    if len(result) != len(set(result)):
        raise CalibrationError("SQL registry query_id values are not unique")
    return result


def _verify_frozen_binding_documents(binding: Mapping[str, Any]) -> None:
    """Cross-check request claims against the contents of all frozen pointers."""

    workload = _load_json(Path(binding["workload"]["artifact"]["path"]))
    registry = _load_json(Path(binding["sql_registry"]["artifact"]["path"]))
    space = _load_json(Path(binding["parameter_space"]["artifact"]["path"]))
    freeze_id = binding["workload"]["freeze_id"]
    benchmark = binding["benchmark"]
    if workload.get("freeze_id") != freeze_id:
        raise CalibrationError("workload artifact freeze_id differs from binding.workload.freeze_id")
    if (
        registry.get("schema_version") != "mysql_plan_workload_registry_v1"
        or registry.get("immutable") is not True
        or registry.get("freeze_id") != freeze_id
        or registry.get("benchmark") != benchmark
    ):
        raise CalibrationError("SQL registry freeze/benchmark/immutability binding mismatch")
    if space.get("freeze_id") != freeze_id or space.get("immutable") is not True:
        raise CalibrationError("parameter-space freeze/immutability binding mismatch")
    embedded = space.get("estimator")
    if not isinstance(embedded, Mapping) or (
        str(Path(str(embedded.get("path") or "")).resolve())
        != binding["estimator"]["artifact"]["path"]
        or str(embedded.get("sha256") or "").lower()
        != binding["estimator"]["artifact"]["sha256"]
    ):
        raise CalibrationError("parameter-space embedded estimator identity is stale or mismatched")
    candidates = space.get("benchmark_candidate_parameters")
    definitions = space.get("all_parameter_definitions")
    active = binding["parameter_space"]["active_params"]
    if not isinstance(candidates, Mapping) or candidates.get(benchmark) != active:
        raise CalibrationError("active_params do not exactly match frozen benchmark candidate order")
    if not isinstance(definitions, Mapping):
        raise CalibrationError("parameter-space artifact lacks parameter definitions")
    for param in active:
        row = definitions.get(param)
        expected = binding["parameter_space"]["ranges"][param]
        if not isinstance(row, Mapping) or {
            "lower": _finite_number(row.get("lower"), f"definitions.{param}.lower", positive=True),
            "upper": _finite_number(row.get("upper"), f"definitions.{param}.upper", positive=True),
        } != expected:
            raise CalibrationError(f"frozen range differs from request: {param}")


def _finite_tree(value: Any, label: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _finite_tree(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _finite_tree(child, f"{label}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        raise CalibrationError(f"{label} contains a non-finite number")


def _capture_estimator_call(
    estimator: Any, params: Mapping[str, float], query_ids: Sequence[str],
) -> tuple[dict[str, float], dict[str, Any], dict[str, Any]]:
    captured: MutableMapping[str, Any] = {}
    provider = getattr(estimator, "provider", None)
    original_collect = getattr(provider, "collect", None)
    if callable(original_collect):
        def collecting_once() -> Any:
            if captured:
                raise CalibrationError("trained estimator requested feature collection more than once")
            current = original_collect()
            captured["features"] = current
            return current

        provider.collect = collecting_once
    try:
        raw_result = estimator(None, dict(params))
    finally:
        if callable(original_collect):
            provider.collect = original_collect
    if not isinstance(raw_result, Mapping):
        raise CalibrationError("trained estimator result must be an object")
    metadata = raw_result.get("_metadata")
    if not isinstance(metadata, Mapping):
        raise CalibrationError("trained estimator result lacks _metadata")
    features = captured.get("features", metadata.get("features"))
    if not isinstance(features, Mapping):
        raise CalibrationError("trained estimator did not expose complete consumed features")
    bundle = features.get("canonical_plan_bundle")
    if not isinstance(bundle, Mapping):
        bundle = metadata.get("canonical_plan_bundle")
    validation = validate_canonical_plan_bundle(bundle)
    if not validation.get("valid"):
        raise CalibrationError(
            "canonical plan bundle validation failed: " + ",".join(validation.get("reasons") or [])
        )
    if list(bundle.get("registry_query_ids") or []) != list(query_ids):
        raise CalibrationError("canonical plan bundle does not match exact SQL registry order")
    if bundle.get("registry_authority_verified") is not True:
        raise CalibrationError("canonical plan bundle lacks verified registry authority")
    if bundle.get("full_explain_preserved") is not True:
        raise CalibrationError("canonical plan bundle does not preserve full raw EXPLAIN")
    prediction = {
        resource: _finite_number(raw_result.get(resource), f"prediction.{resource}")
        for resource in RESOURCES
    }
    if any(value < 0 for value in prediction.values()):
        raise CalibrationError("trained estimator predictions must be non-negative")
    full_features = copy.deepcopy(dict(features))
    full_features.pop("canonical_plan_bundle", None)
    _finite_tree(full_features, "features")
    return prediction, full_features, copy.deepcopy(dict(bundle))


def _write_canonical(path: Path, value: Mapping[str, Any]) -> dict[str, str]:
    _reject_secrets(value, "output")
    payload = _canonical_bytes(value)
    digest = hashlib.sha256(payload).hexdigest()
    temporary = path.with_name(path.name + ".partial")
    sidecar = path.with_name(path.name + ".sha256")
    temporary_sidecar = sidecar.with_name(sidecar.name + ".partial")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    with temporary_sidecar.open("w", encoding="ascii") as handle:
        handle.write(f"{digest}  {path.name}\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_sidecar, sidecar)
    return {"path": path.name, "sha256": digest}


def _normalized_freeze_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        key: binding[key]
        for key in ("database", "scenario", "benchmark", "benchmark_version", "version")
    }
    result["workload"] = {
        "freeze_id": binding["workload"]["freeze_id"],
        "artifact": dict(binding["workload"]["artifact"]),
    }
    result["sql_registry"] = {
        "id": binding["sql_registry"]["id"],
        "artifact": dict(binding["sql_registry"]["artifact"]),
    }
    result["estimator"] = {
        "id": binding["estimator"]["id"],
        "version": binding["estimator"]["version"],
        "artifact": dict(binding["estimator"]["artifact"]),
        "model_artifacts": copy.deepcopy(binding["estimator"]["model_artifacts"]),
    }
    result["parameter_space"] = {
        "version": binding["parameter_space"]["version"],
        "artifact": dict(binding["parameter_space"]["artifact"]),
        "active_params": list(binding["parameter_space"]["active_params"]),
        "ranges": copy.deepcopy(binding["parameter_space"]["ranges"]),
    }
    return result


def _verify_production_config(validated: Mapping[str, Any], config: Mapping[str, Any]) -> None:
    if config.get("type") != "trained_models":
        raise CalibrationError("trained_estimator_config.type must be trained_models")
    binding = validated["binding"]
    artifacts = config.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise CalibrationError("trained estimator config lacks model artifacts")
    for resource in RESOURCES:
        path = Path(str(artifacts.get(resource) or "")).resolve()
        expected = binding["estimator"]["model_artifacts"][resource]
        if str(path) != expected["path"] or not path.is_file() or _sha256(path) != expected["sha256"]:
            raise CalibrationError(f"trained estimator {resource} model binding mismatch")
    feature_source = config.get("feature_source")
    if not isinstance(feature_source, Mapping):
        raise CalibrationError("trained estimator config lacks feature_source")
    if (
        str(feature_source.get("workload_freeze_id") or "") != binding["workload"]["freeze_id"]
        or str(feature_source.get("workload_registry_sha256") or "").lower()
        != binding["sql_registry"]["artifact"]["sha256"]
        or str(Path(str(feature_source.get("workload_registry_path") or "")).resolve())
        != binding["sql_registry"]["artifact"]["path"]
        or feature_source.get("require_strict_plan_registry") is not True
    ):
        raise CalibrationError("trained estimator feature_source frozen registry binding mismatch")
    benchmark = str(config.get("benchmark") or "").upper()
    supported = {
        "H", "H_TPCH", "TPCH", "C", "C_TPCC", "TPCC",
        "CH", "CH_HYBRID", "HYBRID",
    }
    if benchmark not in supported:
        raise CalibrationError(
            "existing trained estimator has no benchmark route for "
            f"{config.get('benchmark')!r}; supported routes are H/C/CH"
        )
    bound_benchmark = str(validated["binding"]["benchmark"]).lower().replace("-", "_")
    if "job" in bound_benchmark:
        raise CalibrationError(
            "JOB endpoint collection is unsupported: existing trained estimator has no JOB route"
        )
    configured_queries = feature_source.get("queries") or []
    for order, row in enumerate(configured_queries, 1):
        sql = str(row.get("sql") if isinstance(row, Mapping) else row).strip()
        from trained_model_estimator import is_read_only_model_query

        if not is_read_only_model_query(sql):
            raise CalibrationError(
                "write-transaction registry cannot be represented as complete trained-estimator "
                f"EXPLAIN coverage (feature query {order}); a separately frozen, authoritative "
                "read-only representative registry is required"
            )


def collect_endpoint_evidence(
    request: Mapping[str, Any],
    request_path: Path,
    output_dir: Path,
    *,
    adapter_factory: Callable[[Mapping[str, Any]], Any] | None = None,
    estimator_factory: Callable[[Any, Mapping[str, Any]], Any] | None = None,
    runtime_config: Mapping[str, Any] | None = None,
    progress_path: Path | None = None,
) -> dict[str, Any]:
    """Collect a complete endpoint directory, with injectable no-DB factories."""

    validated = _validated_request(request, request_path)
    if output_dir.exists() or output_dir.is_symlink():
        raise CalibrationError(f"output directory already exists: {output_dir}")
    estimator_config = _load_json(Path(validated["trained_estimator_config"]["path"]))
    _reject_secrets(estimator_config, "trained_estimator_config")
    runtime = dict(runtime_config or {})
    _reject_secrets(runtime, "runtime_config")
    injected = adapter_factory is not None or estimator_factory is not None
    if injected and (adapter_factory is None or estimator_factory is None):
        raise CalibrationError("adapter_factory and estimator_factory must be injected together")
    if not injected:
        from mysql_cost_adapter import MySQLCLIClient, MySQLCostAdapter
        from trained_model_estimator import build_trained_model_estimator

        implementation = Path(sys.modules[build_trained_model_estimator.__module__].__file__).resolve()
        expected_impl = validated["binding"]["estimator"]["artifact"]
        if str(implementation) != expected_impl["path"] or _sha256(implementation) != expected_impl["sha256"]:
            raise CalibrationError("imported trained estimator implementation binding mismatch")
        _verify_production_config(validated, estimator_config)

        def adapter_factory(current: Mapping[str, Any]) -> Any:
            client_cfg = current.get("mysql_client")
            if not isinstance(client_cfg, Mapping):
                raise CalibrationError("runtime_config.mysql_client is required")
            if not str(client_cfg.get("defaults_extra_file") or ""):
                raise CalibrationError("runtime MySQL access requires defaults_extra_file")
            client = MySQLCLIClient(client_cfg)
            adapter_cfg = current.get("adapter") or {}
            return MySQLCostAdapter(
                client,
                engine_name=str(adapter_cfg.get("engine_name", "default")),
                device_type=int(adapter_cfg.get("device_type", 0)),
            )

        estimator_factory = build_trained_model_estimator

    assert adapter_factory is not None and estimator_factory is not None
    query_ids = _registry_query_ids(Path(validated["binding"]["sql_registry"]["artifact"]["path"]))
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.staging.", dir=output_dir.parent))
    adapter: Any = None
    snapshot: Any = None
    lock_handle: Any = None
    primary_error: BaseException | None = None
    endpoint_rows: dict[str, Any] = {}
    completed_endpoints = 0
    collection_started = time.monotonic()

    def progress(value: Mapping[str, Any]) -> None:
        if progress_path is None: return
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        with progress_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(value), sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush(); os.fsync(handle.fileno())
    try:
        if not injected:
            lock_value = runtime.get("serialization_lock_file")
            if not isinstance(lock_value, str) or not Path(lock_value).is_absolute():
                raise CalibrationError(
                    "runtime_config.serialization_lock_file must be an absolute per-MySQL lock path"
                )
            lock_path = Path(lock_value)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            if lock_path.is_symlink():
                raise CalibrationError("serialization lock path must not be a symlink")
            lock_handle = lock_path.open("a+", encoding="ascii")
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CalibrationError("another endpoint collector holds the MySQL serialization lock") from exc
        adapter = adapter_factory(runtime)
        snapshot = adapter.snapshot()
        estimator = estimator_factory(adapter, estimator_config)
        binding = validated["binding"]
        total_endpoints = 1 + 2 * len(binding["parameter_space"]["active_params"])

        def collect_one(label: str, params: Mapping[str, float], ordinal: int) -> dict[str, Any]:
            nonlocal completed_endpoints
            started = time.monotonic()
            progress({"event": "endpoint_start", "ordinal": ordinal, "total": total_endpoints, "label": label, "completed": completed_endpoints})
            try:
                prediction, features, bundle = _capture_estimator_call(estimator, params, query_ids)
            except BaseException as exc:
                progress({"event": "endpoint_failed", "ordinal": ordinal, "total": total_endpoints, "label": label, "error_type": type(exc).__name__})
                raise
            common = {
                "database": "mysql",
                "scenario": binding["scenario"],
                "benchmark": binding["benchmark"],
                "benchmark_version": binding["benchmark_version"],
                "params": dict(params),
                "query_ids": list(query_ids),
            }
            stem = f"endpoint_{ordinal:03d}_{_SAFE_PARAM.sub('_', label)}"
            explain = {
                "schema_version": EXPLAIN_SCHEMA,
                **common,
                "registry_order_exact": True,
                "raw_explain_captures": copy.deepcopy(bundle["query_captures"]),
                "raw_explain_audit_bundle": copy.deepcopy(bundle["raw_explain_audit_bundle"]),
                "raw_explain_bundle_sha256": bundle["raw_explain_bundle_sha256"],
                "canonical_plan_bundle": bundle,
                "canonical_plan_bundle_sha256": bundle["canonical_plan_bundle_sha256"],
            }
            feature = {
                "schema_version": FEATURE_SCHEMA,
                **common,
                "features": features,
                "prediction": prediction,
                "estimator_sha256": binding["estimator"]["artifact"]["sha256"],
                "model_artifact_sha256": {
                    resource: binding["estimator"]["model_artifacts"][resource]["sha256"]
                    for resource in RESOURCES
                },
                "canonical_plan_bundle_sha256": bundle["canonical_plan_bundle_sha256"],
                "feature_extraction_complete": True,
            }
            explain_pointer = _write_canonical(staging / f"{stem}.explain.json", explain)
            feature_pointer = _write_canonical(staging / f"{stem}.features.json", feature)
            completed_endpoints += 1
            elapsed = time.monotonic() - collection_started
            progress({"event": "endpoint_complete", "ordinal": ordinal, "total": total_endpoints, "label": label, "completed": completed_endpoints, "endpoint_seconds": time.monotonic() - started, "elapsed_seconds": elapsed, "eta_seconds": elapsed / completed_endpoints * (total_endpoints - completed_endpoints)})
            return {
                "params": dict(params),
                "prediction": prediction,
                "explain_artifact": explain_pointer,
                "feature_artifact": feature_pointer,
            }

        ordinal = 1
        endpoint_rows["anchor"] = collect_one("anchor", validated["anchor"], ordinal)
        parameters: dict[str, Any] = {}
        for param in binding["parameter_space"]["active_params"]:
            parameters[param] = {}
            for side in ("lower", "upper"):
                ordinal += 1
                point = dict(validated["anchor"])
                point[param] = binding["parameter_space"]["ranges"][param][side]
                parameters[param][side] = collect_one(f"{param}_{side}", point, ordinal)
        endpoint_rows["parameters"] = parameters
    except BaseException as exc:
        primary_error = exc
    finally:
        if adapter is not None and snapshot is not None:
            try:
                adapter.restore(snapshot)
            except BaseException as restore_exc:
                if primary_error is None:
                    primary_error = CalibrationError(f"MySQL restore failed: {type(restore_exc).__name__}")
                else:
                    primary_error = CalibrationError(
                        f"collection failed and MySQL restore also failed: {type(primary_error).__name__}; "
                        f"{type(restore_exc).__name__}"
                    )
        if lock_handle is not None:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                lock_handle.close()
    if primary_error is not None:
        shutil.rmtree(staging, ignore_errors=True)
        if isinstance(primary_error, CalibrationError):
            raise primary_error
        detail = re.sub(r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+", r"\1=<redacted>", str(primary_error))
        detail = re.sub(r"/tmp/\.[^ /]+\.credentials\.[^ /]+", "<redacted_external_defaults_dir>", detail)
        raise CalibrationError(
            f"endpoint collection failed: {type(primary_error).__name__}: {detail[:1000]}"
        ) from primary_error

    binding = validated["binding"]
    collector_path = Path(__file__).resolve()
    collector_identity = {
        "path": str(collector_path),
        "sha256": _sha256(collector_path),
    }
    evidence = {
        "schema_version": EVIDENCE_SCHEMA,
        "source": "trained_estimator_experience",
        "database": "mysql",
        "scenario": binding["scenario"],
        "benchmark": binding["benchmark"],
        "benchmark_version": binding["benchmark_version"],
        "workload_freeze_id": binding["workload"]["freeze_id"],
        "workload_registry_sha256": binding["workload"]["artifact"]["sha256"],
        "sql_registry_sha256": binding["sql_registry"]["artifact"]["sha256"],
        "estimator_sha256": binding["estimator"]["artifact"]["sha256"],
        "parameter_space_sha256": binding["parameter_space"]["artifact"]["sha256"],
        "collector": collector_identity,
        "active_params": list(binding["parameter_space"]["active_params"]),
        "ranges": copy.deepcopy(binding["parameter_space"]["ranges"]),
        "query_coverage": {
            "expected_query_ids": list(query_ids),
            "covered_query_ids": list(query_ids),
            "missing_query_ids": [],
            "coverage_ratio": 1.0,
        },
        "collection_execution": {
            "cost_apply_and_explain_max_concurrency": 1,
            "serial_endpoint_order": ["anchor"] + [
                f"{param}:{side}"
                for param in binding["parameter_space"]["active_params"]
                for side in ("lower", "upper")
            ],
            "original_mysql_state_restored_before_publish": True,
        },
        "anchor": endpoint_rows["anchor"],
        "parameters": endpoint_rows["parameters"],
    }
    try:
        evidence_pointer = _write_canonical(staging / "endpoint_evidence.json", evidence)
        freeze_request = {
            "schema_version": FREEZE_REQUEST_SCHEMA,
            "binding": _normalized_freeze_binding(binding),
            "endpoint_evidence": evidence_pointer,
            "sensitivity_tolerance": validated["sensitivity_tolerance"],
        }
        freeze_pointer = _write_canonical(staging / "freeze_request.json", freeze_request)
        request_binding = {
            "collector": collector_identity,
            "collection_request": {
                "path": str(request_path.resolve()), "sha256": _sha256(request_path.resolve()),
            },
            "trained_estimator_config": validated["trained_estimator_config"],
            "endpoint_evidence": evidence_pointer,
            "freeze_request": freeze_pointer,
        }
        _write_canonical(staging / "collection_manifest.json", request_binding)
        os.replace(staging, output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "status": "CREATED",
        "output_dir": str(output_dir),
        "endpoint_count": 1 + 2 * len(binding["parameter_space"]["active_params"]),
        "endpoint_evidence": str(output_dir / "endpoint_evidence.json"),
        "freeze_request": str(output_dir / "freeze_request.json"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--runtime-config", type=Path)
    parser.add_argument("--progress-jsonl", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        request = _load_json(args.request)
        runtime = _load_json(args.runtime_config) if args.runtime_config else {}
        result = collect_endpoint_evidence(
            request, args.request, args.output_dir, runtime_config=runtime,
            progress_path=args.progress_jsonl,
        )
        print(json.dumps(result, sort_keys=True) if args.json else f"CREATED {args.output_dir}")
        return 0
    except CalibrationError as exc:
        result = {"status": "INSUFFICIENT_EVIDENCE", "reasons": exc.reasons}
        print(json.dumps(result, sort_keys=True) if args.json else "\n".join(exc.reasons), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
