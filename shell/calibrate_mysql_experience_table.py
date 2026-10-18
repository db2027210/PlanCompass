#!/usr/bin/env python3
"""Build a versioned MySQL experience table from complete formal OAT evidence.

The generator is intentionally offline and fail-closed.  It never connects to a
database, accepts PostgreSQL evidence, or fills missing effects from a prior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
from typing import Any, Mapping, Sequence


RESOURCES = ("cpu", "memory", "io")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUEST_SCHEMA = "mysql_t_calibration_request_v1"
POINT_SCHEMA = "mysql_formal_point_evidence_v1"
OUTPUT_SCHEMA = "mysql_experience_table_t_v1"


class CalibrationError(ValueError):
    """The evidence cannot support a formal experience table."""

    def __init__(self, reasons: str | Sequence[str]) -> None:
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("; ".join(self.reasons))


def _reject_constant(value: str) -> None:
    raise CalibrationError(f"non-finite JSON number is forbidden: {value}")


def _load_json(path: Path) -> Mapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise CalibrationError(f"JSON artifact is absent or a symlink: {path}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=_reject_constant
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise CalibrationError(f"JSON artifact root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _finite_number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        suffix = " and positive" if positive else ""
        raise CalibrationError(f"{label} must be finite{suffix}")
    return number


def _exact_keys(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CalibrationError(f"{label} must be an object")
    if set(value) != expected:
        raise CalibrationError(
            f"{label} keys differ: missing={sorted(expected - set(value))} "
            f"extra={sorted(set(value) - expected)}"
        )
    return value


def _resolve_checked_pointer(pointer: Any, base: Path, label: str) -> tuple[Path, str]:
    if not isinstance(pointer, Mapping):
        raise CalibrationError(f"{label} pointer must be an object")
    raw_path = pointer.get("path")
    expected = pointer.get("sha256")
    if not isinstance(raw_path, str) or not raw_path:
        raise CalibrationError(f"{label}.path is required")
    if not isinstance(expected, str) or SHA256_RE.fullmatch(expected) is None:
        raise CalibrationError(f"{label}.sha256 must be 64 lowercase hex characters")
    path = Path(raw_path)
    path = path if path.is_absolute() else base / path
    if not path.is_file() or path.is_symlink():
        raise CalibrationError(f"{label} is absent or a symlink: {path}")
    actual = _sha256(path)
    if actual != expected:
        raise CalibrationError(
            f"{label} SHA-256 mismatch: expected={expected} actual={actual}"
        )
    return path, actual


def _validate_binding(request: Mapping[str, Any], base: Path) -> dict[str, Any]:
    if request.get("schema_version") != REQUEST_SCHEMA:
        raise CalibrationError(f"schema_version must be {REQUEST_SCHEMA}")
    binding = request.get("binding")
    if not isinstance(binding, Mapping):
        raise CalibrationError("binding must be an object")
    if binding.get("database") != "mysql":
        raise CalibrationError("binding.database must be exactly mysql; PG T reuse is forbidden")

    strings = ("scenario", "benchmark", "benchmark_version", "version")
    for field in strings:
        if not isinstance(binding.get(field), str) or not binding[field]:
            raise CalibrationError(f"binding.{field} is required")

    workload = binding.get("workload")
    estimator = binding.get("estimator")
    parameter_space = binding.get("parameter_space")
    for label, value in (
        ("workload", workload), ("estimator", estimator),
        ("parameter_space", parameter_space),
    ):
        if not isinstance(value, Mapping):
            raise CalibrationError(f"binding.{label} must be an object")
    if not isinstance(workload.get("freeze_id"), str) or not workload["freeze_id"]:
        raise CalibrationError("binding.workload.freeze_id is required")
    if not isinstance(estimator.get("id"), str) or not estimator["id"]:
        raise CalibrationError("binding.estimator.id is required")
    if not isinstance(estimator.get("version"), str) or not estimator["version"]:
        raise CalibrationError("binding.estimator.version is required")
    if not isinstance(parameter_space.get("version"), str) or not parameter_space["version"]:
        raise CalibrationError("binding.parameter_space.version is required")

    workload_path, workload_sha = _resolve_checked_pointer(
        workload.get("artifact"), base, "binding.workload.artifact"
    )
    estimator_path, estimator_sha = _resolve_checked_pointer(
        estimator.get("artifact"), base, "binding.estimator.artifact"
    )
    space_path, space_sha = _resolve_checked_pointer(
        parameter_space.get("artifact"), base, "binding.parameter_space.artifact"
    )

    active = parameter_space.get("active_params")
    if (
        not isinstance(active, list) or not active
        or any(not isinstance(item, str) or not item for item in active)
        or len(set(active)) != len(active)
    ):
        raise CalibrationError("active_params must be a non-empty unique string list")
    ranges = _exact_keys(parameter_space.get("ranges"), set(active), "parameter_space.ranges")
    normalized_ranges: dict[str, dict[str, float]] = {}
    for param in active:
        bounds = _exact_keys(ranges[param], {"lower", "upper"}, f"ranges.{param}")
        lower = _finite_number(bounds["lower"], f"ranges.{param}.lower", positive=True)
        upper = _finite_number(bounds["upper"], f"ranges.{param}.upper", positive=True)
        if lower >= upper:
            raise CalibrationError(f"ranges.{param} requires lower < upper")
        normalized_ranges[param] = {"lower": lower, "upper": upper}

    return {
        "database": "mysql",
        "scenario": binding["scenario"],
        "benchmark": binding["benchmark"],
        "benchmark_version": binding["benchmark_version"],
        "version": binding["version"],
        "workload": {
            "freeze_id": workload["freeze_id"],
            "artifact_path": str(workload_path),
            "sha256": workload_sha,
        },
        "estimator": {
            "id": estimator["id"], "version": estimator["version"],
            "artifact_path": str(estimator_path), "sha256": estimator_sha,
        },
        "parameter_space": {
            "version": parameter_space["version"],
            "artifact_path": str(space_path), "sha256": space_sha,
            "active_params": list(active), "ranges": normalized_ranges,
        },
    }


def _validate_point(point: Mapping[str, Any], binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    expected = {
        "schema_version": POINT_SCHEMA,
        "database": "mysql",
        "engine_family": "mysql",
        "scenario": binding["scenario"],
        "benchmark": binding["benchmark"],
        "benchmark_version": binding["benchmark_version"],
        "workload_freeze_id": binding["workload"]["freeze_id"],
        "workload_registry_sha256": binding["workload"]["sha256"],
        "estimator_id": binding["estimator"]["id"],
        "estimator_version": binding["estimator"]["version"],
        "estimator_sha256": binding["estimator"]["sha256"],
        "parameter_space_version": binding["parameter_space"]["version"],
        "parameter_space_sha256": binding["parameter_space"]["sha256"],
    }
    for field, value in expected.items():
        if point.get(field) != value:
            raise CalibrationError(f"{label}.{field} does not match frozen MySQL binding")
    if point.get("formal_point") is not True or point.get("completeness") != "COMPLETE":
        raise CalibrationError(f"{label} is not a COMPLETE formal point")
    if not isinstance(point.get("mysql_version"), str) or not point["mysql_version"]:
        raise CalibrationError(f"{label}.mysql_version is required")
    if point.get("artifact_checksums_verified") is not True:
        raise CalibrationError(f"{label} did not verify artifact checksums")
    if not isinstance(point.get("point_id"), str) or not point["point_id"]:
        raise CalibrationError(f"{label}.point_id is required")
    if not isinstance(point.get("anchor_id"), str) or not point["anchor_id"]:
        raise CalibrationError(f"{label}.anchor_id is required")
    repeat_id = point.get("repeat_id")
    if isinstance(repeat_id, bool) or not isinstance(repeat_id, int) or repeat_id < 1:
        raise CalibrationError(f"{label}.repeat_id must be a positive integer")

    active = binding["parameter_space"]["active_params"]
    if point.get("active_params") != active:
        raise CalibrationError(f"{label}.active_params differs from binding order/content")
    if point.get("ranges") != binding["parameter_space"]["ranges"]:
        raise CalibrationError(f"{label}.ranges differs from binding")
    param_set = set(active)
    normalized_params: dict[str, float] = {}
    for field in ("params_requested", "params_effective", "params_readback"):
        values = _exact_keys(point.get(field), param_set, f"{label}.{field}")
        current = {
            param: _finite_number(values[param], f"{label}.{field}.{param}", positive=True)
            for param in active
        }
        if field == "params_requested":
            normalized_params = current
        elif current != normalized_params:
            raise CalibrationError(f"{label} requested/effective/readback parameters differ")
    for param, value in normalized_params.items():
        bounds = binding["parameter_space"]["ranges"][param]
        if not bounds["lower"] <= value <= bounds["upper"]:
            raise CalibrationError(f"{label}.{param} is outside frozen range")

    window = point.get("resource_window")
    if not isinstance(window, Mapping):
        raise CalibrationError(f"{label}.resource_window is required")
    if (
        window.get("latest_window_seconds") != 180
        or window.get("latest_window_count") != 6
        or window.get("stable") is not True
    ):
        raise CalibrationError(f"{label} lacks the frozen latest-six-window stability decision")
    coverage = _finite_number(window.get("sample_coverage"), f"{label}.sample_coverage")
    if coverage < 0.90 or coverage > 1.0:
        raise CalibrationError(f"{label}.sample_coverage must be within [0.90, 1.0]")
    utilization = _exact_keys(window.get("utilization"), set(RESOURCES), f"{label}.utilization")
    resources = {
        resource: _finite_number(utilization[resource], f"{label}.utilization.{resource}")
        for resource in RESOURCES
    }
    if any(value < 0 or value > 1 for value in resources.values()):
        raise CalibrationError(f"{label} resource utilization must be within [0, 1]")

    envelope = point.get("resource_envelope")
    if not isinstance(envelope, Mapping):
        raise CalibrationError(f"{label}.resource_envelope is required")
    for field in ("version", "sha256", "readback_sha256"):
        value = envelope.get(field)
        if not isinstance(value, str) or not value:
            raise CalibrationError(f"{label}.resource_envelope.{field} is required")
    if any(SHA256_RE.fullmatch(envelope[field]) is None for field in ("sha256", "readback_sha256")):
        raise CalibrationError(f"{label} resource envelope hashes are malformed")
    correctness = point.get("correctness")
    if not isinstance(correctness, Mapping) or correctness.get("passed") is not True:
        raise CalibrationError(f"{label}.correctness.passed must be true")
    execution = point.get("execution")
    if not isinstance(execution, Mapping) or execution != {
        "exit_code": 0, "timeout": False, "oom": False, "mysql_restart": False,
    }:
        raise CalibrationError(f"{label}.execution is not a clean formal completion")

    return {
        "point_id": point["point_id"], "anchor_id": point["anchor_id"],
        "repeat_id": repeat_id, "evidence_class": point.get("evidence_class"),
        "params": normalized_params, "resources": resources, "coverage": coverage,
        "envelope": dict(envelope),
    }


def _median(values: Sequence[float]) -> float:
    return float(statistics.median(values))


def _effect_summary(
    effects: list[float], minimum_effect: float, minimum_sign_consistency: float,
    maximum_relative_mad: float, minimum_total: int,
) -> dict[str, Any]:
    magnitude = _median([abs(value) for value in effects])
    signed = _median(effects)
    max_abs = max(abs(value) for value in effects)
    mad = _median([abs(value - signed) for value in effects])
    relative_mad = mad / max(abs(signed), 1e-12)
    observed_signed = signed
    if max_abs <= minimum_effect:
        direction = "neutral"
        consistency = 1.0
        signed = 0.0
    else:
        if abs(signed) < minimum_effect:
            raise CalibrationError("ambiguous effect: median is below threshold but observations are not neutral")
        sign = 1 if signed > 0 else -1
        consistency = sum((value > 0) == (sign > 0) for value in effects) / len(effects)
        if consistency < minimum_sign_consistency:
            raise CalibrationError(f"sign consistency {consistency:.6g} is below threshold")
        if relative_mad > maximum_relative_mad:
            raise CalibrationError(f"relative MAD {relative_mad:.6g} exceeds threshold")
        direction = "increase" if sign > 0 else "decrease"
    high = (
        len(effects) >= max(10, minimum_total + 4)
        and consistency >= 0.90 and relative_mad <= 0.50
    )
    return {
        "direction": direction,
        "signed_effect_full_range": signed,
        "observed_median_effect_full_range": observed_signed,
        "magnitude_full_range": magnitude,
        "confidence": {
            "level": "HIGH" if high else "MODERATE",
            "contrast_count": len(effects),
            "sign_consistency": consistency,
            "median_absolute_deviation": mad,
            "relative_mad": relative_mad,
            "decision": "SUPPORTED",
        },
    }


def build_artifact(request: Mapping[str, Any], request_path: Path) -> dict[str, Any]:
    """Validate evidence and return an output artifact, or raise CalibrationError."""
    base = request_path.resolve().parent
    binding = _validate_binding(request, base)
    policy = request.get("calibration_policy")
    if not isinstance(policy, Mapping):
        raise CalibrationError("calibration_policy must be an object")
    min_side = policy.get("minimum_contrasts_per_side")
    if isinstance(min_side, bool) or not isinstance(min_side, int) or min_side < 3:
        raise CalibrationError("minimum_contrasts_per_side must be an integer >= 3")
    min_sign = _finite_number(policy.get("minimum_sign_consistency"), "minimum_sign_consistency")
    max_mad = _finite_number(policy.get("maximum_relative_mad"), "maximum_relative_mad")
    min_effect = _finite_number(policy.get("neutral_effect_threshold"), "neutral_effect_threshold")
    if not 0.8 <= min_sign <= 1 or max_mad <= 0 or min_effect <= 0:
        raise CalibrationError("invalid fail-closed calibration thresholds")

    contrasts = request.get("contrasts")
    if not isinstance(contrasts, list):
        raise CalibrationError("contrasts must be a list")
    point_cache: dict[tuple[str, str], tuple[dict[str, Any], str, str]] = {}
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
        param: {"lower": [], "upper": []}
        for param in binding["parameter_space"]["active_params"]
    }
    contrast_ids: set[str] = set()
    evidence_index: dict[str, dict[str, str]] = {}
    reasons: list[str] = []

    def load_point(pointer: Any, label: str) -> tuple[dict[str, Any], str, str]:
        path, digest = _resolve_checked_pointer(pointer, base, label)
        key = (str(path), digest)
        if key not in point_cache:
            point_cache[key] = (_validate_point(_load_json(path), binding, label), str(path), digest)
        return point_cache[key]

    for index, contrast in enumerate(contrasts):
        label = f"contrasts[{index}]"
        try:
            if not isinstance(contrast, Mapping):
                raise CalibrationError(f"{label} must be an object")
            cid = contrast.get("contrast_id")
            param = contrast.get("parameter")
            if not isinstance(cid, str) or not cid or cid in contrast_ids:
                raise CalibrationError(f"{label}.contrast_id is absent or duplicated")
            contrast_ids.add(cid)
            if param not in grouped:
                raise CalibrationError(f"{label}.parameter is not active")
            anchor, anchor_path, anchor_sha = load_point(contrast.get("anchor"), f"{label}.anchor")
            treatment, treatment_path, treatment_sha = load_point(
                contrast.get("treatment"), f"{label}.treatment"
            )
            if anchor["evidence_class"] != "frozen_anchor":
                raise CalibrationError(f"{label} anchor is not frozen_anchor evidence")
            if treatment["evidence_class"] != "oat_treatment":
                raise CalibrationError(f"{label} treatment is not oat_treatment evidence")
            if anchor["anchor_id"] != treatment["anchor_id"]:
                raise CalibrationError(f"{label} anchor_id differs")
            if anchor["repeat_id"] != treatment["repeat_id"]:
                raise CalibrationError(f"{label} repeat_id differs")
            if anchor["envelope"] != treatment["envelope"]:
                raise CalibrationError(f"{label} resource envelope/readback differs")
            changed = [name for name in anchor["params"] if anchor["params"][name] != treatment["params"][name]]
            if changed != [param]:
                raise CalibrationError(f"{label} is not single-parameter OAT: changed={changed}")
            bounds = binding["parameter_space"]["ranges"][param]
            anchor_value = anchor["params"][param]
            treatment_value = treatment["params"][param]
            if treatment_value == bounds["lower"] and treatment_value < anchor_value:
                side = "lower"
            elif treatment_value == bounds["upper"] and treatment_value > anchor_value:
                side = "upper"
            else:
                raise CalibrationError(f"{label} treatment must be the frozen lower or upper bound around anchor")
            log_step = math.log(treatment_value / anchor_value)
            full_log_range = math.log(bounds["upper"] / bounds["lower"])
            effects = {
                resource: (
                    (treatment["resources"][resource] - anchor["resources"][resource])
                    / log_step * full_log_range
                )
                for resource in RESOURCES
            }
            grouped[param][side].append({
                "contrast_id": cid, "repeat_id": anchor["repeat_id"],
                "anchor_point_id": anchor["point_id"],
                "treatment_point_id": treatment["point_id"], "effects": effects,
                "minimum_coverage": min(anchor["coverage"], treatment["coverage"]),
            })
            evidence_index[anchor_sha] = {"path": anchor_path, "sha256": anchor_sha}
            evidence_index[treatment_sha] = {"path": treatment_path, "sha256": treatment_sha}
        except CalibrationError as exc:
            reasons.extend(exc.reasons)

    table: dict[str, Any] = {}
    effect_evidence: dict[str, Any] = {}
    for param, sides in grouped.items():
        for side in ("lower", "upper"):
            repeats = [row["repeat_id"] for row in sides[side]]
            if len(sides[side]) < min_side:
                reasons.append(
                    f"{param}.{side} has {len(sides[side])} contrasts; requires {min_side}"
                )
            if len(repeats) != len(set(repeats)):
                reasons.append(f"{param}.{side} repeats are not independent/unique")
        if reasons:
            continue
        rows = sides["lower"] + sides["upper"]
        resource_table: dict[str, Any] = {}
        for resource in RESOURCES:
            try:
                resource_table[resource] = _effect_summary(
                    [row["effects"][resource] for row in rows], min_effect,
                    min_sign, max_mad, 2 * min_side,
                )
                resource_table[resource]["confidence"]["minimum_sample_coverage"] = min(
                    row["minimum_coverage"] for row in rows
                )
            except CalibrationError as exc:
                reasons.extend(f"{param}.{resource}: {reason}" for reason in exc.reasons)
        effect_evidence[param] = resource_table
        table[param] = {
            resource: resource_table[resource]["signed_effect_full_range"]
            for resource in RESOURCES
        }

    if reasons:
        raise CalibrationError(sorted(set(reasons)))

    request_sha = _sha256(request_path)
    return {
        "schema_version": OUTPUT_SCHEMA,
        "database": "mysql",
        "scenario": binding["scenario"],
        "benchmark": binding["benchmark"],
        "benchmark_version": binding["benchmark_version"],
        "version": binding["version"],
        "workload_freeze_id": binding["workload"]["freeze_id"],
        "workload_registry_sha256": binding["workload"]["sha256"],
        "estimator": {
            "id": binding["estimator"]["id"],
            "version": binding["estimator"]["version"],
            "sha256": binding["estimator"]["sha256"],
        },
        "parameter_space": {
            "version": binding["parameter_space"]["version"],
            "sha256": binding["parameter_space"]["sha256"],
            "active_params": binding["parameter_space"]["active_params"],
            "ranges": binding["parameter_space"]["ranges"],
        },
        "resource_columns": list(RESOURCES),
        "table": table,
        "effect_evidence": effect_evidence,
        "calibration": {
            "method": "paired_two_sided_formal_oat_log_slope_v1",
            "source": "real_measurement",
            "real_measurement": True,
            "magnitude_unit": "utilization_fraction_change_across_frozen_parameter_range",
            "policy": {
                "minimum_contrasts_per_side": min_side,
                "minimum_sign_consistency": min_sign,
                "maximum_relative_mad": max_mad,
                "neutral_effect_threshold": min_effect,
            },
            "contrast_count": len(contrasts),
        },
        "provenance": {
            "request_sha256": request_sha,
            "evidence_class": "mysql_complete_formal_two_sided_oat",
            "source": "real_measurement",
            "real_measurement": True,
            "cross_engine_prior_used": False,
            "point_artifacts": [evidence_index[key] for key in sorted(evidence_index)],
        },
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="print machine-readable status")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        request = _load_json(args.request)
        artifact = build_artifact(request, args.request)
        payload = _canonical_bytes(artifact)
        digest = hashlib.sha256(payload).hexdigest()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload)
        sidecar = args.output.with_name(args.output.name + ".sha256")
        sidecar.write_text(f"{digest}  {args.output.name}\n", encoding="ascii")
        status = {
            "status": "CREATED", "artifact": str(args.output),
            "canonical_sha256": digest, "sidecar": str(sidecar),
        }
        print(json.dumps(status, sort_keys=True) if args.json else f"CREATED {args.output} sha256={digest}")
        return 0
    except CalibrationError as exc:
        status = {"status": "INSUFFICIENT_EVIDENCE", "reasons": exc.reasons}
        print(json.dumps(status, sort_keys=True) if args.json else "\n".join(status["reasons"]), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
