"""Baseline-window uncertainty gate for real incumbent updates."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence


RESOURCE_NAMES = ("cpu", "memory", "io")


def _finite(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{field} is not numeric") from exc
    if not math.isfinite(parsed):
        raise RuntimeError(f"{field} is not finite")
    return parsed


def _samples_path(summary: Mapping[str, Any]) -> Path:
    candidates = (
        (summary.get("formal_pg_resource") or {}).get("samples_path"),
        summary.get("resource_samples_path"),
        summary.get("derived_resource_samples_path"),
        (summary.get("measurement") or {}).get(
            "derived_resource_samples_path"
        ),
        (
            (summary.get("measurement") or {}).get("formal_pg_resource")
            or {}
        ).get("samples_path"),
    )
    for raw in candidates:
        if raw:
            path = Path(str(raw)).resolve()
            if path.is_file():
                return path
    sibling = Path(str(summary.get("_summary_path") or ""))
    if sibling.is_file():
        for filename in (
            "resource_samples.jsonl",
            "derived_resource_samples.jsonl",
            "formal_resource_samples.jsonl",
        ):
            resource_samples = sibling.resolve().parent / filename
            if resource_samples.is_file():
                return resource_samples
    raise RuntimeError("baseline summary lacks a readable formal resource sample file")


def build_baseline_score_noise_gate(
    *,
    baseline_summary: Mapping[str, Any],
    capability: Mapping[str, Any],
    score_func: Callable[[Mapping[str, float]], float],
    window_seconds: int = 30,
    required_windows: int = 6,
    minimum_window_coverage: float = 0.90,
) -> Dict[str, Any]:
    """Estimate a 95% score-mean confidence half-width from baseline windows."""

    if window_seconds <= 0 or required_windows < 2:
        raise ValueError("invalid baseline noise window contract")
    sample_path = _samples_path(baseline_summary)
    rows: List[Dict[str, float]] = []
    for line_number, raw_line in enumerate(
        sample_path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        if not raw_line.strip():
            continue
        try:
            raw = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"invalid baseline resource sample at line {line_number}"
            ) from exc
        row = {
            "timestamp": _finite(
                raw.get(
                    "start_epoch",
                    raw.get("timestamp_epoch", raw.get("epoch_seconds")),
                ),
                f"sample {line_number} timestamp",
            ),
            "seconds": max(
                1e-9,
                _finite(raw.get("seconds", 1.0), f"sample {line_number} seconds"),
            ),
        }
        for name in RESOURCE_NAMES:
            row[name] = _finite(
                raw.get(f"{name}_utilization"),
                f"sample {line_number} {name}_utilization",
            )
        rows.append(row)
    if not rows:
        raise RuntimeError("baseline formal resource sample file is empty")
    rows.sort(key=lambda item: item["timestamp"])
    first_timestamp = rows[0]["timestamp"]
    windows: Dict[int, List[Dict[str, float]]] = {}
    for row in rows:
        index = int(
            max(0.0, row["timestamp"] - first_timestamp) // window_seconds
        )
        windows.setdefault(index, []).append(row)
    complete: List[Dict[str, Any]] = []
    for index in sorted(windows):
        members = windows[index]
        covered_seconds = sum(row["seconds"] for row in members)
        coverage = min(1.0, covered_seconds / float(window_seconds))
        if coverage < minimum_window_coverage:
            continue
        utilization = {
            name: sum(row[name] * row["seconds"] for row in members)
            / covered_seconds
            for name in RESOURCE_NAMES
        }
        score = float(score_func(utilization))
        if not math.isfinite(score):
            raise RuntimeError("baseline window score is not finite")
        complete.append(
            {
                "window_index": index,
                "coverage": coverage,
                "utilization": utilization,
                "score": score,
            }
        )
    retained = complete[-required_windows:]
    if len(retained) != required_windows:
        raise RuntimeError(
            "baseline noise gate lacks six complete formal windows"
        )
    scores = [float(window["score"]) for window in retained]
    sample_std = statistics.stdev(scores)
    # Student-t 97.5% quantile for df=5, matching the required six windows.
    t_critical = 2.570581835636305
    confidence_half_width = (
        t_critical * sample_std / math.sqrt(required_windows)
    )
    capability_values = {
        name: _finite(capability.get(name), f"capability.{name}")
        for name in RESOURCE_NAMES
    }
    if any(value <= 0.0 for value in capability_values.values()):
        raise RuntimeError("baseline noise gate capability must be positive")
    return {
        "contract": "baseline_window_score_noise_gate_v1",
        "samples_path": str(sample_path),
        "samples_sha256": hashlib.sha256(sample_path.read_bytes()).hexdigest(),
        "window_seconds": window_seconds,
        "window_count": required_windows,
        "minimum_window_coverage": minimum_window_coverage,
        "window_scores": scores,
        "score_sample_std": sample_std,
        "confidence_level": 0.95,
        "student_t_critical": t_critical,
        "minimum_actual_score_delta": confidence_half_width,
        "qps_used": False,
        "windows": retained,
    }


def incumbent_improvement_passes(
    *,
    actual_score: float,
    baseline_score: float,
    noise_gate: Mapping[str, Any],
) -> bool:
    threshold = _finite(
        noise_gate.get("minimum_actual_score_delta"),
        "minimum_actual_score_delta",
    )
    return bool(
        math.isfinite(actual_score)
        and math.isfinite(baseline_score)
        and actual_score - baseline_score > threshold
    )


__all__ = [
    "build_baseline_score_noise_gate",
    "incumbent_improvement_passes",
]
