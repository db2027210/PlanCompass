#!/usr/bin/env python3
"""Build a QPS-free log-ratio calibration artifact from real replays."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Sequence, Tuple


RESOURCES = ("cpu", "memory", "io")


def _finite_positive(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{field} is not numeric") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise RuntimeError(f"{field} must be finite and positive")
    return result


def _fit(points: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    if len(points) < 2:
        raise RuntimeError("calibration fit requires at least two points")
    xs = [math.log(raw_ratio) for raw_ratio, _ in points]
    ys = [math.log(actual_ratio) for _, actual_ratio in points]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    denominator = sum((value - x_mean) ** 2 for value in xs)
    if denominator <= 1e-15:
        raise RuntimeError("calibration raw feature ratios have no variance")
    beta = sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(xs, ys)
    ) / denominator
    alpha = y_mean - beta * x_mean
    if not math.isfinite(alpha) or not math.isfinite(beta):
        raise RuntimeError("calibration coefficients are not finite")
    return alpha, beta


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise RuntimeError("percentile input is empty")
    position = (len(ordered) - 1) * quantile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def build_artifact(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_sha256: str,
    minimum_samples: int = 12,
) -> Dict[str, Any]:
    """Fit each resource independently without reading throughput fields."""

    if len(rows) < minimum_samples:
        raise RuntimeError(
            f"calibration needs at least {minimum_samples} replay records"
        )
    resource_points: Dict[str, List[Tuple[float, float]]] = {
        name: [] for name in RESOURCES
    }
    for index, row in enumerate(rows, 1):
        if row.get("cost_parameter_free_features") is not True:
            raise RuntimeError(
                f"record {index} is not cost-parameter-free"
            )
        if row.get("qps_used") is True:
            raise RuntimeError(f"record {index} declares QPS use")
        feature = row.get("raw_feature_ratio")
        baseline = row.get("baseline_real_resource")
        actual = row.get("actual_resource")
        if not all(
            isinstance(value, Mapping)
            for value in (feature, baseline, actual)
        ):
            raise RuntimeError(f"record {index} is incomplete")
        for name in RESOURCES:
            raw_ratio = _finite_positive(
                feature.get(name),
                f"record {index}.raw_feature_ratio.{name}",
            )
            baseline_value = _finite_positive(
                baseline.get(name),
                f"record {index}.baseline_real_resource.{name}",
            )
            actual_value = _finite_positive(
                actual.get(name),
                f"record {index}.actual_resource.{name}",
            )
            resource_points[name].append(
                (raw_ratio, actual_value / baseline_value)
            )

    resources: Dict[str, Any] = {}
    for name, points in resource_points.items():
        alpha, beta = _fit(points)
        loo_errors_pp = []
        for held_out in range(len(points)):
            training = [
                point
                for index, point in enumerate(points)
                if index != held_out
            ]
            loo_alpha, loo_beta = _fit(training)
            raw_ratio, actual_ratio = points[held_out]
            predicted_ratio = math.exp(
                loo_alpha + loo_beta * math.log(raw_ratio)
            )
            loo_errors_pp.append(
                abs(predicted_ratio - actual_ratio) * 100.0
            )
        resources[name] = {
            "alpha": alpha,
            "beta": beta,
            "sample_count": len(points),
            "loo_absolute_error_p90_pp": _percentile(
                loo_errors_pp,
                0.90,
            ),
        }
    return {
        "schema_version": 1,
        "contract": "resource_ratio_log_calibration_v1",
        "qps_used": False,
        "cost_parameter_free_features": True,
        "source_records_sha256": source_sha256,
        "sample_count": len(rows),
        "resources": resources,
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: build_ratio_scale_resource_calibration.py "
            "REPLAY_RECORDS.jsonl OUTPUT.json"
        )
    source = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    source_bytes = source.read_bytes()
    rows = [
        json.loads(line)
        for line in source_bytes.decode("utf-8").splitlines()
        if line.strip()
    ]
    if not all(isinstance(row, dict) for row in rows):
        raise RuntimeError("calibration source contains a non-object record")
    artifact = build_artifact(
        rows,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(output, artifact)
    print(
        json.dumps(
            {
                "artifact_path": str(output),
                "artifact_sha256": hashlib.sha256(
                    output.read_bytes()
                ).hexdigest(),
                "sample_count": len(rows),
                "qps_used": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
