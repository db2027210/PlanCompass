#!/usr/bin/env python3
"""Predict total MySQL memory from an exported plan-feature candidate JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import joblib
import numpy as np


def _causal_baseline(payload: Dict[str, Any]) -> Tuple[float, float, float]:
    """Return causal base, last observation, and positive 5-second drift."""
    recent: List[float] = [float(value) for value in payload.get("recent_memory_mb", [])]
    if recent:
        last_memory = recent[-1]
        differences = np.diff(np.asarray(recent[-7:], dtype=float))
        positive_drift = max(float(np.median(differences)), 0.0) if len(differences) else 0.0
        return last_memory + 4.0 * positive_drift, last_memory, positive_drift
    observed_base = float(payload["observed_base_memory_mb"])
    return observed_base, observed_base, 0.0


def predict(artifact_path: Path, payload: Dict[str, Any]) -> Dict[str, float]:
    artifact = joblib.load(artifact_path)
    benchmark = str(payload["benchmark"]).upper()
    observed_base, last_memory, positive_drift = _causal_baseline(payload)
    features = dict(payload.get("features", {}))
    features.setdefault("context_last_memory_mb", last_memory)
    features.setdefault("context_positive_drift_mb_per_window", positive_drift)
    features.setdefault("context_block_index", float(payload.get("block_index", 0.0)))
    features.setdefault("context_candidate_progress", float(payload.get("candidate_progress", 0.0)))
    for name in ("H", "C", "CH"):
        features[f"benchmark__{name}"] = float(benchmark == name)
    vector = np.asarray(
        [[float(features.get(column, 0.0)) for column in artifact["feature_columns"]]],
        dtype=float,
    )
    raw_dynamic = max(float(artifact["model"].predict(vector)[0]), 0.0)
    shrinkage_by_benchmark = artifact.get("dynamic_shrinkage_by_benchmark", {})
    shrinkage = float(shrinkage_by_benchmark.get(benchmark, 1.0))
    dynamic = shrinkage * raw_dynamic
    return {
        "observed_base_memory_mb": observed_base,
        "last_memory_mb": last_memory,
        "positive_drift_mb_per_5s_window": positive_drift,
        "predicted_dynamic_memory_mb": dynamic,
        "predicted_total_memory_mb": observed_base + dynamic,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path, help="JSON payload path")
    parser.add_argument(
        "--model",
        type=Path,
        default=Path(__file__).resolve().parent / "artifacts/global_memory_model.joblib",
    )
    args = parser.parse_args()
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    print(json.dumps(predict(args.model, payload), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
