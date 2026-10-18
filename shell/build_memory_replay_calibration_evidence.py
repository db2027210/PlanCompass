"""Build Memory replay-calibration evidence dict from a prior-run optimizer JSONL.

RC#1c offline loader (override §311-354 item 1 + §8 #4 + iter98 §6 next-iter
priority #1).  The validate_resource_model_quality_gate Memory branch has had
an RC#1b direction-ratio gate since iter94-95 (see
shell/test_memory_replay_calibration_gate_rc1b.py), but the gate is only
meaningful when the caller supplies historical real-replay calibration
evidence.  This loader extracts that evidence from the optimizer JSONL of a
prior run by scanning `feedback_estimation_error` events.

For every `feedback_estimation_error` event in the jsonl, the loader pulls:
  - `estimated_resource.memory` (model prediction)
  - `actual_resource.memory`    (real replay measurement)

and classifies vs a baseline-memory reference (default = first event's
estimated memory, which on v6 is the model intercept 3.3809 GiB):
  - predicted_down:  est.memory <= baseline - delta
  - actual_down:     act.memory <= baseline - delta
  - predicted_down_actual_up: predicted_down AND act.memory >  baseline

Output JSON shape (fed into
gate_cfg.memory_replay_calibration.evidence):
  {
    "predicted_down_count":           int,
    "actual_down_count":              int,
    "predicted_down_actual_up_count": int,
    "predicted_values":               [float, ...],
    "actual_values":                  [float, ...],
    "baseline_memory":                float,
    "delta":                          float,
    "calibration_count":              int,   # = len(predicted_values)
    "source_run_id":                  str,
    "build_loader_version":           "rc1c-v1"
  }

Run:
  python3 shell/build_memory_replay_calibration_evidence.py \
      --jsonl history/pg_benchmark_bottleneck_matrix/<run_id>/memory/hybench_ap_optimizer_history_*.jsonl \
      --out   history/pg_benchmark_bottleneck_matrix/<run_id>/memory/memory_replay_calibration_evidence.json \
      [--baseline-memory 3.3809] [--delta 0.001] [--source-run-id <run_id>]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from typing import Any, Dict, List, Optional


_LOADER_VERSION = "rc1c-v1"


def _is_feedback_estimation_error_event(parsed: Dict[str, Any]) -> bool:
    ev = str(parsed.get("event") or "")
    return "feedback_estimation_error" in ev


def _extract_memories(parsed: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Pull est/actual memory floats from a feedback_estimation_error event."""
    est = parsed.get("estimated_resource")
    act = parsed.get("actual_resource")
    if not isinstance(est, dict) or not isinstance(act, dict):
        return None
    est_mem = est.get("memory")
    act_mem = act.get("memory")
    if est_mem is None or act_mem is None:
        return None
    try:
        est_f = float(est_mem)
        act_f = float(act_mem)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(est_f) and math.isfinite(act_f)):
        return None
    return {"est": est_f, "act": act_f}


def _derive_run_id_from_jsonl_path(jsonl_path: str) -> str:
    """history/pg_benchmark_bottleneck_matrix/<RUN_ID>/memory/<...>.jsonl → <RUN_ID>."""
    # Walk up two parents: file -> memory -> RUN_ID
    p = os.path.abspath(jsonl_path)
    parent = os.path.dirname(os.path.dirname(p))
    return os.path.basename(parent) or "unknown"


def build_evidence_from_jsonl(
    jsonl_path: str,
    *,
    baseline_memory: Optional[float] = None,
    delta: float = 0.001,
    source_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Stream-parse the optimizer JSONL and return the calibration evidence dict.

    Args:
        jsonl_path: Absolute or relative path to a Bonect optimizer JSONL.
        baseline_memory: Reference baseline memory (GiB).  If None, defaults
            to the first `feedback_estimation_error` event's est.memory
            (typical model intercept on v6 = 3.3809 GiB).
        delta: Absolute tolerance (GiB) for classifying predicted/actual-down.
            Default 0.001 absorbs floating point noise.
        source_run_id: Optional provenance tag.  If None, derived from path.

    Returns:
        Evidence dict in the shape documented in the module docstring.
    """
    if not os.path.isfile(jsonl_path):
        raise FileNotFoundError(f"jsonl not found: {jsonl_path}")
    if not math.isfinite(delta) or delta < 0:
        raise ValueError(f"delta must be >= 0 and finite; got {delta}")

    predicted_values: List[float] = []
    actual_values: List[float] = []
    chosen_baseline: Optional[float] = baseline_memory

    with open(jsonl_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or "feedback_estimation_error" not in line:
                continue
            try:
                parsed = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(parsed, dict) or not _is_feedback_estimation_error_event(parsed):
                continue
            mems = _extract_memories(parsed)
            if mems is None:
                continue
            est_f = mems["est"]
            act_f = mems["act"]
            if chosen_baseline is None:
                # First valid est.memory becomes the baseline reference (model intercept).
                chosen_baseline = est_f
            predicted_values.append(est_f)
            actual_values.append(act_f)

    if chosen_baseline is None:
        # No usable events found; emit an empty-evidence dict so callers can
        # still operate gracefully (gate will skip on no evidence).
        return {
            "predicted_down_count": 0,
            "actual_down_count": 0,
            "predicted_down_actual_up_count": 0,
            "predicted_values": [],
            "actual_values": [],
            "baseline_memory": None,
            "delta": delta,
            "calibration_count": 0,
            "source_run_id": source_run_id or _derive_run_id_from_jsonl_path(jsonl_path),
            "build_loader_version": _LOADER_VERSION,
            "warning": "no_feedback_estimation_error_events_with_estimated_and_actual_memory_found",
        }

    baseline = float(chosen_baseline)
    predicted_down = 0
    actual_down = 0
    predicted_down_actual_up = 0
    for est_f, act_f in zip(predicted_values, actual_values):
        pred_down = est_f <= baseline - delta
        act_down = act_f <= baseline - delta
        if pred_down:
            predicted_down += 1
            if not act_down:
                predicted_down_actual_up += 1
        if act_down:
            actual_down += 1

    return {
        "predicted_down_count": predicted_down,
        "actual_down_count": actual_down,
        "predicted_down_actual_up_count": predicted_down_actual_up,
        "predicted_values": predicted_values,
        "actual_values": actual_values,
        "baseline_memory": baseline,
        "delta": delta,
        "calibration_count": len(predicted_values),
        "source_run_id": source_run_id or _derive_run_id_from_jsonl_path(jsonl_path),
        "build_loader_version": _LOADER_VERSION,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build Memory replay-calibration evidence dict from a Bonect optimizer JSONL.",
    )
    parser.add_argument("--jsonl", required=True, help="Path to optimizer_history_*.jsonl")
    parser.add_argument(
        "--out",
        required=True,
        help="Output JSON path (e.g. <run_dir>/memory/memory_replay_calibration_evidence.json)",
    )
    parser.add_argument(
        "--baseline-memory",
        type=float,
        default=None,
        help="Reference baseline memory (GiB).  If omitted, defaults to the first event's est.memory (model intercept).",
    )
    parser.add_argument(
        "--delta",
        type=float,
        default=0.001,
        help="Absolute tolerance (GiB) for predicted/actual-down classification.  Default 0.001.",
    )
    parser.add_argument(
        "--source-run-id",
        default=None,
        help="Provenance tag.  If omitted, derived from the jsonl parent's parent directory name.",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="Print a one-line summary to stdout after writing the JSON.",
    )
    args = parser.parse_args(argv)

    evidence = build_evidence_from_jsonl(
        args.jsonl,
        baseline_memory=args.baseline_memory,
        delta=args.delta,
        source_run_id=args.source_run_id,
    )

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(evidence, fh, indent=2)

    if args.print_summary:
        print(
            f"[evidence] calibration_count={evidence['calibration_count']} "
            f"predicted_down={evidence['predicted_down_count']} "
            f"actual_down={evidence['actual_down_count']} "
            f"predicted_down_actual_up={evidence['predicted_down_actual_up_count']} "
            f"baseline_memory={evidence['baseline_memory']} "
            f"source_run_id={evidence['source_run_id']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())