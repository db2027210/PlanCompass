"""Fail-closed contracts shared by the formal PostgreSQL benchmark lanes."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any, Dict, List, Mapping, Tuple


PG5_CAMPAIGN_ID = "pg5_common_outer_bgallowed_20260722_v1"
PG5_SCORING_SHA256 = (
    "47db6e60190b79b44419b76f6823d08b59f9e0058917977f468ed8926475f588"
)
PG5_R0_GATE_SHA256 = (
    "c6c034087393c12da5fa055a0cff8666db0dd70edc2bd39eb83c4146d0d9481e"
)
PG5_IO_UTILIZATION_SHA256 = (
    "35eb6f304627ef915f4cfb463b64e472a760325834df7978ec67cac85c342064"
)
PG5_STABILITY_GATE_SHA256 = (
    "ef368b7e40ebf13e3ee75fef4144bb8d8ce8893ac9510c42823e16f854b488e2"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _diff_paths(expected: Any, observed: Any, prefix: str = "scoring") -> List[str]:
    if isinstance(expected, Mapping) and isinstance(observed, Mapping):
        issues: List[str] = []
        expected_keys = set(expected)
        observed_keys = set(observed)
        for key in sorted(expected_keys - observed_keys):
            issues.append(f"{prefix}.{key}:missing")
        for key in sorted(observed_keys - expected_keys):
            issues.append(f"{prefix}.{key}:unexpected")
        for key in sorted(expected_keys & observed_keys):
            issues.extend(_diff_paths(expected[key], observed[key], f"{prefix}.{key}"))
        return issues
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            observed_number = float(observed)
        except (TypeError, ValueError):
            return [f"{prefix}:expected={expected!r},observed={observed!r}"]
        if math.isfinite(observed_number) and math.isclose(
            float(expected), observed_number, rel_tol=0.0, abs_tol=1e-12
        ):
            return []
    elif expected == observed:
        return []
    return [f"{prefix}:expected={expected!r},observed={observed!r}"]


def validate_common_scoring_contract(
    scoring: Mapping[str, Any],
    contract_config: Mapping[str, Any],
) -> Dict[str, Any]:
    """Validate one lane's scoring against the byte-frozen common contract."""
    raw_path = contract_config.get("path")
    raw_sha256 = str(contract_config.get("sha256") or "").strip().lower()
    if not raw_path:
        raise ValueError("formal scoring_contract.path is required")
    if raw_sha256 != PG5_SCORING_SHA256:
        raise ValueError(
            "formal scoring contract configured SHA mismatch: "
            f"expected={PG5_SCORING_SHA256}, configured={raw_sha256 or None}"
        )
    path = Path(str(raw_path)).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"formal scoring contract not found: {path}")
    actual_sha256 = _sha256(path)
    if actual_sha256 != PG5_SCORING_SHA256:
        raise ValueError(
            "formal scoring contract file SHA mismatch: "
            f"expected={PG5_SCORING_SHA256}, actual={actual_sha256}"
        )
    with path.open("r", encoding="utf-8") as handle:
        expected = json.load(handle)
    if not isinstance(expected, dict):
        raise ValueError("formal scoring contract must contain a JSON object")
    issues = _diff_paths(expected, dict(scoring))
    if issues:
        raise ValueError(
            "formal lane scoring differs from common contract: " + "; ".join(issues)
        )
    return {
        "passed": True,
        "path": str(path),
        "sha256": actual_sha256,
        "campaign_id": PG5_CAMPAIGN_ID,
    }


def validate_campaign_file_contract(
    contract_config: Mapping[str, Any],
    *,
    expected_sha256: str,
    contract_name: str,
) -> Dict[str, Any]:
    """Validate the path and byte hash of a shared campaign contract."""
    raw_path = contract_config.get("path")
    configured_sha256 = str(contract_config.get("sha256") or "").strip().lower()
    if not raw_path:
        raise ValueError(f"formal {contract_name}.path is required")
    if configured_sha256 != expected_sha256:
        raise ValueError(
            f"formal {contract_name} configured SHA mismatch: "
            f"expected={expected_sha256}, configured={configured_sha256 or None}"
        )
    path = Path(str(raw_path)).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"formal {contract_name} not found: {path}")
    actual_sha256 = _sha256(path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"formal {contract_name} file SHA mismatch: "
            f"expected={expected_sha256}, actual={actual_sha256}"
        )
    return {
        "passed": True,
        "contract_name": contract_name,
        "path": str(path),
        "sha256": actual_sha256,
        "campaign_id": PG5_CAMPAIGN_ID,
    }


def evaluate_r0_single_bottleneck_gate(
    utilization_pct: Mapping[str, Any],
    target_resource: str,
) -> Tuple[bool, List[Dict[str, Any]]]:
    """Apply the campaign R0 gate: target >80, non-targets in [30, 50]."""
    target = str(target_resource).strip().lower()
    if target not in {"cpu", "memory", "io"}:
        raise ValueError(f"invalid R0 target resource: {target_resource!r}")
    checks: List[Dict[str, Any]] = []
    for resource in ("cpu", "memory", "io"):
        raw_value = utilization_pct.get(resource)
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = math.nan
        finite = math.isfinite(value)
        is_target = resource == target
        passed = finite and (value > 80.0 if is_target else 30.0 <= value <= 50.0)
        checks.append(
            {
                "resource": resource,
                "role": "target" if is_target else "non_target",
                "metric_value_pct": value if finite else None,
                "required_min_exclusive": 80.0 if is_target else None,
                "required_min_inclusive": None if is_target else 30.0,
                "required_max_inclusive": None if is_target else 50.0,
                "passed": bool(passed),
            }
        )
    return all(check["passed"] for check in checks), checks


def evaluate_six_window_mean_stability(
    assessment: Any,
    metric_fields: Tuple[str, ...],
    *,
    coefficient_of_variation_max: float = 0.10,
    required_window_count: int = 6,
    minimum_elapsed_seconds: int = 180,
    aggregate_coverage_min: float = 0.90,
) -> Dict[str, Any]:
    """Use the loosest common early-stop gate meaningful for long queries.

    One-second samples are still collected unchanged.  Stability is evaluated
    over six consecutive complete 30-second windows: each metric is averaged
    inside each window, then the CV of those six window means is compared with
    one common 10% limit.  This avoids rejecting low-frequency workloads merely
    because their one-second series consists of zeros and bursts.
    """
    if not metric_fields or len(set(metric_fields)) != len(metric_fields):
        raise ValueError("metric_fields must be a non-empty unique tuple")
    if required_window_count <= 0:
        raise ValueError("required_window_count must be positive")
    if not 0.0 <= coefficient_of_variation_max <= 1.0:
        raise ValueError("coefficient_of_variation_max must be in [0,1]")
    if not 0.0 < aggregate_coverage_min <= 1.0:
        raise ValueError("aggregate_coverage_min must be in (0,1]")

    if isinstance(assessment, Mapping):
        payload = dict(assessment)
    elif hasattr(assessment, "to_dict"):
        payload = dict(assessment.to_dict())
    else:
        raise TypeError("assessment must be a mapping or expose to_dict()")
    windows = list(payload.get("windows") or [])
    groups: List[Dict[str, Any]] = []
    earliest_stable_stop_seconds = None

    for end_index in range(required_window_count - 1, len(windows)):
        group = windows[end_index - required_window_count + 1:end_index + 1]
        group_coverage_passed = all(
            bool(window.get("coverage_passed")) for window in group
        )
        cumulative = windows[:end_index + 1]
        cumulative_samples = sum(int(window.get("sample_count") or 0) for window in cumulative)
        cumulative_expected = sum(
            int(window.get("expected_sample_count") or 0) for window in cumulative
        )
        cumulative_coverage = (
            min(1.0, cumulative_samples / cumulative_expected)
            if cumulative_expected > 0
            else 0.0
        )
        field_checks: List[Dict[str, Any]] = []
        for metric in metric_fields:
            means: List[float] = []
            missing = False
            for window in group:
                fields = {
                    str(field.get("field")): field
                    for field in (window.get("fields") or [])
                    if isinstance(field, Mapping)
                }
                raw_mean = (fields.get(metric) or {}).get("mean")
                try:
                    mean_value = float(raw_mean)
                except (TypeError, ValueError):
                    missing = True
                    break
                if not math.isfinite(mean_value) or mean_value < 0.0:
                    missing = True
                    break
                means.append(mean_value)
            if missing or len(means) != required_window_count:
                field_checks.append(
                    {
                        "field": metric,
                        "window_means": means,
                        "coefficient_of_variation": None,
                        "limit": coefficient_of_variation_max,
                        "passed": False,
                        "reason": "missing_or_invalid_window_mean",
                    }
                )
                continue
            mean_of_means = statistics.fmean(means)
            population_std = statistics.pstdev(means)
            coefficient = (
                0.0
                if mean_of_means == 0.0 and population_std == 0.0
                else math.inf
                if mean_of_means == 0.0
                else population_std / mean_of_means
            )
            field_checks.append(
                {
                    "field": metric,
                    "window_means": means,
                    "mean_of_window_means": mean_of_means,
                    "population_std_of_window_means": population_std,
                    "coefficient_of_variation": coefficient,
                    "limit": coefficient_of_variation_max,
                    "passed": coefficient <= coefficient_of_variation_max,
                }
            )
        end_elapsed_seconds = int(float(group[-1].get("end_elapsed_seconds") or 0.0))
        group_stable = bool(
            group_coverage_passed
            and cumulative_coverage >= aggregate_coverage_min
            and end_elapsed_seconds >= minimum_elapsed_seconds
            and len(field_checks) == len(metric_fields)
            and all(check["passed"] for check in field_checks)
        )
        groups.append(
            {
                "start_window_index": int(group[0].get("window_index") or 0),
                "end_window_index": int(group[-1].get("window_index") or 0),
                "end_elapsed_seconds": end_elapsed_seconds,
                "per_window_coverage_passed": group_coverage_passed,
                "cumulative_coverage": cumulative_coverage,
                "cumulative_coverage_min": aggregate_coverage_min,
                "field_checks": field_checks,
                "stable": group_stable,
            }
        )
        if group_stable and earliest_stable_stop_seconds is None:
            earliest_stable_stop_seconds = end_elapsed_seconds

    return {
        "mode": "six_consecutive_30s_window_means_common_cv",
        "metric_fields": list(metric_fields),
        "required_window_count": required_window_count,
        "coefficient_of_variation_max": coefficient_of_variation_max,
        "minimum_elapsed_seconds": minimum_elapsed_seconds,
        "aggregate_coverage_min": aggregate_coverage_min,
        "stable": earliest_stable_stop_seconds is not None,
        "consecutive_stable_windows": (
            required_window_count if earliest_stable_stop_seconds is not None else 0
        ),
        "earliest_stable_stop_seconds": earliest_stable_stop_seconds,
        "groups": groups,
    }


__all__ = [
    "PG5_CAMPAIGN_ID",
    "PG5_IO_UTILIZATION_SHA256",
    "PG5_R0_GATE_SHA256",
    "PG5_SCORING_SHA256",
    "PG5_STABILITY_GATE_SHA256",
    "evaluate_r0_single_bottleneck_gate",
    "evaluate_six_window_mean_stability",
    "validate_campaign_file_contract",
    "validate_common_scoring_contract",
]
