#!/usr/bin/env python3
"""Freeze MySQL T from complete offline trained-estimator endpoint evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from calibrate_mysql_experience_table import (
    CalibrationError, OUTPUT_SCHEMA, RESOURCES, _canonical_bytes, _exact_keys,
    _finite_number, _load_json, _resolve_checked_pointer, _sha256,
)


REQUEST_SCHEMA = "mysql_estimator_t_freeze_request_v1"
EVIDENCE_SCHEMA = "mysql_trained_estimator_endpoint_evidence_v1"
EXPLAIN_SCHEMA = "mysql_explain_bundle_v1"
FEATURE_SCHEMA = "mysql_trained_feature_bundle_v1"


def _binding(request: Mapping[str, Any], base: Path) -> dict[str, Any]:
    if request.get("schema_version") != REQUEST_SCHEMA:
        raise CalibrationError(f"schema_version must be {REQUEST_SCHEMA}")
    raw = request.get("binding")
    if not isinstance(raw, Mapping) or raw.get("database") != "mysql":
        raise CalibrationError("binding.database must be exactly mysql")
    for field in ("scenario", "benchmark", "benchmark_version", "version"):
        if not isinstance(raw.get(field), str) or not raw[field]:
            raise CalibrationError(f"binding.{field} is required")

    def artifact(parent: Any, name: str, id_fields: Sequence[str]) -> dict[str, Any]:
        if not isinstance(parent, Mapping):
            raise CalibrationError(f"binding.{name} must be an object")
        result: dict[str, Any] = {}
        for field in id_fields:
            if not isinstance(parent.get(field), str) or not parent[field]:
                raise CalibrationError(f"binding.{name}.{field} is required")
            result[field] = parent[field]
        path, digest = _resolve_checked_pointer(parent.get("artifact"), base, f"binding.{name}.artifact")
        result.update(path=str(path), sha256=digest)
        return result

    workload = artifact(raw.get("workload"), "workload", ("freeze_id",))
    registry = artifact(raw.get("sql_registry"), "sql_registry", ("id",))
    estimator_raw = raw.get("estimator")
    estimator = artifact(estimator_raw, "estimator", ("id", "version"))
    model_raw = estimator_raw.get("model_artifacts") if isinstance(estimator_raw, Mapping) else None
    model_raw = _exact_keys(model_raw, set(RESOURCES), "binding.estimator.model_artifacts")
    models: dict[str, dict[str, str]] = {}
    for resource in RESOURCES:
        path, digest = _resolve_checked_pointer(
            model_raw[resource], base, f"binding.estimator.model_artifacts.{resource}"
        )
        models[resource] = {"path": str(path), "sha256": digest}
    estimator["model_artifacts"] = models

    space_raw = raw.get("parameter_space")
    space = artifact(space_raw, "parameter_space", ("version",))
    active = space_raw.get("active_params") if isinstance(space_raw, Mapping) else None
    if (
        not isinstance(active, list) or not active or len(active) != len(set(active))
        or any(not isinstance(item, str) or not item for item in active)
    ):
        raise CalibrationError("active_params must be a non-empty unique string list")
    ranges_raw = _exact_keys(space_raw.get("ranges"), set(active), "parameter_space.ranges")
    ranges: dict[str, dict[str, float]] = {}
    for param in active:
        bounds = _exact_keys(ranges_raw[param], {"lower", "upper"}, f"ranges.{param}")
        lower = _finite_number(bounds["lower"], f"ranges.{param}.lower", positive=True)
        upper = _finite_number(bounds["upper"], f"ranges.{param}.upper", positive=True)
        if lower >= upper:
            raise CalibrationError(f"ranges.{param} requires lower < upper")
        ranges[param] = {"lower": lower, "upper": upper}
    space.update(active_params=list(active), ranges=ranges)
    evidence_path, evidence_sha = _resolve_checked_pointer(
        request.get("endpoint_evidence"), base, "endpoint_evidence"
    )
    result = {
        "database": "mysql", "scenario": raw["scenario"],
        "benchmark": raw["benchmark"], "benchmark_version": raw["benchmark_version"],
        "version": raw["version"], "workload": workload, "sql_registry": registry,
        "estimator": estimator, "parameter_space": space,
        "endpoint_evidence": {
            "path": str(evidence_path.relative_to(base)),
            "resolved_path": str(evidence_path),
            "sha256": evidence_sha,
        },
    }
    workload_doc = _load_json(Path(workload["path"]))
    registry_doc = _load_json(Path(registry["path"]))
    space_doc = _load_json(Path(space["path"]))
    freeze_id = workload["freeze_id"]
    benchmark = raw["benchmark"]
    if workload_doc.get("freeze_id") != freeze_id:
        raise CalibrationError("workload artifact freeze_id differs from binding")
    if (
        registry_doc.get("schema_version") != "mysql_plan_workload_registry_v1"
        or registry_doc.get("immutable") is not True
        or registry_doc.get("freeze_id") != freeze_id
        or registry_doc.get("benchmark") != benchmark
    ):
        raise CalibrationError("SQL registry freeze/benchmark/immutability binding mismatch")
    if space_doc.get("freeze_id") != freeze_id or space_doc.get("immutable") is not True:
        raise CalibrationError("parameter-space freeze/immutability binding mismatch")
    embedded = space_doc.get("estimator")
    if not isinstance(embedded, Mapping) or (
        str(Path(str(embedded.get("path") or "")).resolve()) != estimator["path"]
        or str(embedded.get("sha256") or "").lower() != estimator["sha256"]
    ):
        raise CalibrationError("parameter-space embedded estimator identity is stale or mismatched")
    candidates = space_doc.get("benchmark_candidate_parameters")
    definitions = space_doc.get("all_parameter_definitions")
    if not isinstance(candidates, Mapping) or candidates.get(benchmark) != active:
        raise CalibrationError("active_params do not exactly match frozen benchmark candidate order")
    if not isinstance(definitions, Mapping):
        raise CalibrationError("parameter-space artifact lacks parameter definitions")
    for param in active:
        row = definitions.get(param)
        if not isinstance(row, Mapping) or {
            "lower": _finite_number(row.get("lower"), f"definitions.{param}.lower", positive=True),
            "upper": _finite_number(row.get("upper"), f"definitions.{param}.upper", positive=True),
        } != ranges[param]:
            raise CalibrationError(f"frozen range differs from request: {param}")
    return result


def _params(value: Any, binding: Mapping[str, Any], label: str) -> dict[str, float]:
    active = binding["parameter_space"]["active_params"]
    raw = _exact_keys(value, set(active), label)
    result = {
        param: _finite_number(raw[param], f"{label}.{param}", positive=True)
        for param in active
    }
    for param, current in result.items():
        bounds = binding["parameter_space"]["ranges"][param]
        if not bounds["lower"] <= current <= bounds["upper"]:
            raise CalibrationError(f"{label}.{param} is outside frozen range")
    return result


def _prediction(value: Any, label: str) -> dict[str, float]:
    raw = _exact_keys(value, set(RESOURCES), label)
    result = {resource: _finite_number(raw[resource], f"{label}.{resource}") for resource in RESOURCES}
    if any(number < 0 for number in result.values()):
        raise CalibrationError(f"{label} predictions must be non-negative")
    return result


def _verified_endpoint(
    raw: Any, binding: Mapping[str, Any], base: Path, query_ids: list[str], label: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise CalibrationError(f"{label} must be an object")
    params = _params(raw.get("params"), binding, f"{label}.params")
    prediction = _prediction(raw.get("prediction"), f"{label}.prediction")
    explain_path, explain_sha = _resolve_checked_pointer(raw.get("explain_artifact"), base, f"{label}.explain")
    feature_path, feature_sha = _resolve_checked_pointer(raw.get("feature_artifact"), base, f"{label}.feature")
    explain = _load_json(explain_path)
    feature = _load_json(feature_path)
    common = {
        "database": "mysql", "scenario": binding["scenario"],
        "benchmark": binding["benchmark"], "benchmark_version": binding["benchmark_version"],
    }
    for field, expected in common.items():
        if explain.get(field) != expected or feature.get(field) != expected:
            raise CalibrationError(f"{label} EXPLAIN/feature {field} binding mismatch")
    if explain.get("schema_version") != EXPLAIN_SCHEMA or feature.get("schema_version") != FEATURE_SCHEMA:
        raise CalibrationError(f"{label} EXPLAIN/feature schema mismatch")
    if explain.get("params") != params or feature.get("params") != params:
        raise CalibrationError(f"{label} EXPLAIN/feature params mismatch")
    if explain.get("query_ids") != query_ids or feature.get("query_ids") != query_ids:
        raise CalibrationError(f"{label} does not cover the exact frozen SQL registry order")
    if feature.get("prediction") != prediction:
        raise CalibrationError(f"{label} declared prediction differs from feature artifact")
    if feature.get("estimator_sha256") != binding["estimator"]["sha256"]:
        raise CalibrationError(f"{label} estimator implementation checksum mismatch")
    expected_models = {
        resource: binding["estimator"]["model_artifacts"][resource]["sha256"]
        for resource in RESOURCES
    }
    if feature.get("model_artifact_sha256") != expected_models:
        raise CalibrationError(f"{label} model artifact checksum binding mismatch")
    if feature.get("feature_extraction_complete") is not True:
        raise CalibrationError(f"{label} feature extraction is incomplete")
    return {
        "params": params, "prediction": prediction,
        "explain": {"path": str(explain_path.relative_to(base)), "sha256": explain_sha},
        "feature": {"path": str(feature_path.relative_to(base)), "sha256": feature_sha},
    }


def build_estimator_artifact(request: Mapping[str, Any], request_path: Path) -> dict[str, Any]:
    base = request_path.resolve().parent
    binding = _binding(request, base)
    evidence = _load_json(Path(binding["endpoint_evidence"]["resolved_path"]))
    expected = {
        "schema_version": EVIDENCE_SCHEMA,
        "source": "trained_estimator_experience", "database": "mysql",
        "scenario": binding["scenario"], "benchmark": binding["benchmark"],
        "benchmark_version": binding["benchmark_version"],
        "workload_freeze_id": binding["workload"]["freeze_id"],
        "workload_registry_sha256": binding["workload"]["sha256"],
        "sql_registry_sha256": binding["sql_registry"]["sha256"],
        "estimator_sha256": binding["estimator"]["sha256"],
        "parameter_space_sha256": binding["parameter_space"]["sha256"],
    }
    for field, value in expected.items():
        if evidence.get(field) != value:
            raise CalibrationError(f"endpoint_evidence.{field} does not match frozen binding")
    if evidence.get("active_params") != binding["parameter_space"]["active_params"]:
        raise CalibrationError("endpoint evidence active_params mismatch")
    if evidence.get("ranges") != binding["parameter_space"]["ranges"]:
        raise CalibrationError("endpoint evidence ranges mismatch")

    coverage = evidence.get("query_coverage")
    if not isinstance(coverage, Mapping):
        raise CalibrationError("query_coverage is required")
    expected_ids = coverage.get("expected_query_ids")
    covered_ids = coverage.get("covered_query_ids")
    if (
        not isinstance(expected_ids, list) or not expected_ids
        or any(not isinstance(item, str) or not item for item in expected_ids)
        or len(expected_ids) != len(set(expected_ids))
        or covered_ids != expected_ids or coverage.get("missing_query_ids") != []
        or coverage.get("coverage_ratio") != 1.0
    ):
        raise CalibrationError("exact 100% SQL registry coverage is required")

    anchor = _verified_endpoint(evidence.get("anchor"), binding, base, expected_ids, "anchor")
    for param, bounds in binding["parameter_space"]["ranges"].items():
        if not bounds["lower"] < anchor["params"][param] < bounds["upper"]:
            raise CalibrationError(f"anchor.{param} must be strictly inside its frozen range")
    parameters = _exact_keys(
        evidence.get("parameters"), set(binding["parameter_space"]["active_params"]),
        "endpoint_evidence.parameters",
    )
    tolerance = _finite_number(request.get("sensitivity_tolerance"), "sensitivity_tolerance")
    if tolerance <= 0:
        raise CalibrationError("sensitivity_tolerance must be positive")

    table: dict[str, dict[str, float]] = {}
    effects: dict[str, Any] = {}
    endpoint_index: dict[str, Any] = {"anchor": anchor}
    for param in binding["parameter_space"]["active_params"]:
        pair = _exact_keys(parameters[param], {"lower", "upper"}, f"parameters.{param}")
        lower = _verified_endpoint(pair["lower"], binding, base, expected_ids, f"parameters.{param}.lower")
        upper = _verified_endpoint(pair["upper"], binding, base, expected_ids, f"parameters.{param}.upper")
        for side, point in (("lower", lower), ("upper", upper)):
            changed = [name for name in anchor["params"] if anchor["params"][name] != point["params"][name]]
            if changed != [param]:
                raise CalibrationError(f"{param}.{side} is not single-parameter OAT: changed={changed}")
            if point["params"][param] != binding["parameter_space"]["ranges"][param][side]:
                raise CalibrationError(f"{param}.{side} is not the frozen range endpoint")
        deltas = {
            resource: upper["prediction"][resource] - lower["prediction"][resource]
            for resource in RESOURCES
        }
        parameter_detected = any(abs(value) > tolerance for value in deltas.values())
        table[param] = {
            resource: value if abs(value) > tolerance else 0.0
            for resource, value in deltas.items()
        }
        effects[param] = {
            resource: {
                "direction": "increase" if value > tolerance else "decrease" if value < -tolerance else "neutral",
                "signed_effect_full_range": table[param][resource],
                "observed_median_effect_full_range": value,
                "observed_delta": value,
                "tolerance": tolerance,
                "magnitude_full_range": abs(value),
                "evidence_status": "DETECTED_EFFECT" if abs(value) > tolerance else "NO_DETECTABLE_EFFECT",
                "parameter_effect_status": "DETECTED_EFFECT" if parameter_detected else "NO_DETECTABLE_EFFECT",
                "confidence": {
                    "level": "MODEL_BACKED" if parameter_detected else "MODEL_BACKED_LOW_SENSITIVITY",
                    "source": "trained_estimator_experience",
                    "real_measurement": False, "query_coverage_ratio": 1.0,
                    "endpoint_feature_coverage": "COMPLETE",
                    "decision": "SUPPORTED" if abs(value) > tolerance else "NO_DETECTABLE_EFFECT",
                },
            }
            for resource, value in deltas.items()
        }
        endpoint_index[param] = {"lower": lower, "upper": upper}

    models = {
        resource: binding["estimator"]["model_artifacts"][resource]["sha256"]
        for resource in RESOURCES
    }
    return {
        "schema_version": OUTPUT_SCHEMA, "database": "mysql",
        "scenario": binding["scenario"], "benchmark": binding["benchmark"],
        "benchmark_version": binding["benchmark_version"], "version": binding["version"],
        "workload_freeze_id": binding["workload"]["freeze_id"],
        "workload_registry_sha256": binding["workload"]["sha256"],
        "sql_registry": {"id": binding["sql_registry"]["id"], "sha256": binding["sql_registry"]["sha256"]},
        "estimator": {
            "id": binding["estimator"]["id"], "version": binding["estimator"]["version"],
            "sha256": binding["estimator"]["sha256"], "model_artifact_sha256": models,
        },
        "parameter_space": {
            "version": binding["parameter_space"]["version"],
            "sha256": binding["parameter_space"]["sha256"],
            "active_params": binding["parameter_space"]["active_params"],
            "ranges": binding["parameter_space"]["ranges"],
        },
        "resource_columns": list(RESOURCES), "table": table, "effect_evidence": effects,
        "calibration": {
            "method": "trained_estimator_two_sided_oat_v1",
            "source": "trained_estimator_experience", "real_measurement": False,
            "sensitivity_tolerance": tolerance, "query_coverage_ratio": 1.0,
        },
        "provenance": {
            "request_sha256": _sha256(request_path),
            "evidence_class": "mysql_trained_estimator_complete_endpoint_features",
            "source": "trained_estimator_experience", "real_measurement": False,
            "cross_engine_prior_used": False,
            "endpoint_evidence": {
                "path": binding["endpoint_evidence"]["path"],
                "sha256": binding["endpoint_evidence"]["sha256"],
            },
            "endpoint_artifacts": endpoint_index,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        artifact = build_estimator_artifact(_load_json(args.request), args.request)
        payload = _canonical_bytes(artifact)
        digest = hashlib.sha256(payload).hexdigest()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload)
        sidecar = args.output.with_name(args.output.name + ".sha256")
        sidecar.write_text(f"{digest}  {args.output.name}\n", encoding="ascii")
        result = {"status": "CREATED", "artifact": str(args.output), "canonical_sha256": digest, "sidecar": str(sidecar)}
        print(json.dumps(result, sort_keys=True) if args.json else f"CREATED {args.output} sha256={digest}")
        return 0
    except CalibrationError as exc:
        result = {"status": "INSUFFICIENT_EVIDENCE", "reasons": exc.reasons}
        print(json.dumps(result, sort_keys=True) if args.json else "\n".join(exc.reasons), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
