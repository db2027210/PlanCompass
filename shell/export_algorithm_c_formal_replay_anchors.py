#!/usr/bin/env python3
"""Export immutable, local-incumbent anchors from a formal Algorithm C run.

The live controller deliberately freezes its predictor for the whole formal
campaign.  This adapter is the safe bridge to the next campaign: it unwraps
only completed physical ``base_top5`` rounds, reproduces the authoritative
same-worker normalization, and records the pre-calibration full-plan ratio.
It never writes the source campaign and never treats cached re-decisions as
new physical evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


RESOURCE_NAMES = ("cpu", "memory", "io")
ANCHOR_CONTRACT = "algorithm_c_formal_replay_local_incumbent_anchor_v1"
EXPORT_CONTRACT = "algorithm_c_formal_replay_anchor_export_v1"


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid JSONL {path}:{line_number}: {exc}") from exc
            if isinstance(value, dict):
                yield value


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def finite_positive(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{label} is not numeric: {value!r}") from exc
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise RuntimeError(f"{label} must be finite and positive: {value!r}")
    return parsed


def physical_valid(row: Mapping[str, Any]) -> bool:
    if str(row.get("status") or "").lower() != "ok":
        return False
    if row.get("technical_failure") is True:
        return False
    if int(row.get("valid_replay_count") or 0) != 1:
        return False
    stats = row.get("resource_stats")
    if not isinstance(stats, Mapping) or stats.get("resource_monitor_ok") is not True:
        return False
    completeness = row.get("workload_completeness")
    if not isinstance(completeness, Mapping) or completeness.get("passed") is not True:
        return False
    telemetry = row.get("executor_telemetry")
    if not isinstance(telemetry, Mapping) or telemetry.get("available") is not True:
        return False
    return True


def worker_key(row: Mapping[str, Any]) -> str:
    value = str(row.get("worker_key") or "").strip()
    if value:
        return value
    worker = row.get("worker")
    if isinstance(worker, Mapping):
        return str(worker.get("name") or worker.get("container") or "").strip()
    return ""


def normalize_candidate(
    validation: Mapping[str, Any],
    worker_baseline: Mapping[str, Any],
    canonical_baseline: Mapping[str, Any],
) -> Tuple[Dict[str, float], float]:
    candidate_resources = validation.get("resources")
    worker_resources = worker_baseline.get("resources")
    canonical_resources = canonical_baseline.get("resources")
    if not all(
        isinstance(value, Mapping)
        for value in (candidate_resources, worker_resources, canonical_resources)
    ):
        raise RuntimeError("same-worker normalization resource payload is incomplete")
    normalized: Dict[str, float] = {}
    for resource in RESOURCE_NAMES:
        candidate = finite_positive(candidate_resources.get(resource), f"candidate {resource}")
        worker = finite_positive(worker_resources.get(resource), f"worker baseline {resource}")
        canonical = finite_positive(canonical_resources.get(resource), f"canonical baseline {resource}")
        normalized[resource] = canonical * candidate / worker
    candidate_qps = finite_positive(validation.get("db_qps"), "candidate db_qps")
    worker_qps = finite_positive(worker_baseline.get("db_qps"), "worker baseline db_qps")
    canonical_qps = finite_positive(canonical_baseline.get("db_qps"), "canonical baseline db_qps")
    return normalized, canonical_qps * candidate_qps / worker_qps


def local_anchor(
    *,
    validation: Mapping[str, Any],
    round_index: int,
    worker_baseline: Mapping[str, Any],
    canonical_baseline: Mapping[str, Any],
    round_offset: int,
) -> Dict[str, Any]:
    ranking = validation.get("candidate_ranking")
    ranking = ranking if isinstance(ranking, Mapping) else {}
    metadata = ranking.get("final_estimation_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    params = validation.get("params")
    baseline_params = metadata.get("baseline_params")
    baseline_real = metadata.get("baseline_real")
    calibration = metadata.get("resource_prediction_calibration_audit")
    if not all(
        isinstance(value, Mapping)
        for value in (params, baseline_params, baseline_real, calibration)
    ):
        raise RuntimeError("candidate lacks local-incumbent prediction metadata")

    normalized_resources, normalized_qps = normalize_candidate(
        validation,
        worker_baseline,
        canonical_baseline,
    )
    parameter_log_deltas: Dict[str, float] = {}
    for name, baseline_raw in baseline_params.items():
        if name not in params:
            continue
        parameter_log_deltas[str(name)] = math.log(
            finite_positive(params[name], f"candidate parameter {name}")
            / finite_positive(baseline_raw, f"local baseline parameter {name}")
        )
    if not parameter_log_deltas:
        raise RuntimeError("candidate has no local parameter deltas")

    outcomes: Dict[str, float] = {}
    raw_plan_log_ratios: Dict[str, float] = {}
    for resource in RESOURCE_NAMES:
        outcomes[resource] = math.log(
            finite_positive(normalized_resources[resource], f"normalized {resource}")
            / finite_positive(baseline_real.get(resource), f"local baseline real {resource}")
        )
        resource_calibration = calibration.get(resource)
        if not isinstance(resource_calibration, Mapping):
            raise RuntimeError(f"candidate lacks {resource} calibration audit")
        raw_plan = resource_calibration.get("raw_plan_log_ratio")
        try:
            raw_plan_value = float(raw_plan)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid raw plan log ratio for {resource}") from exc
        if not math.isfinite(raw_plan_value):
            raise RuntimeError(f"non-finite raw plan log ratio for {resource}")
        raw_plan_log_ratios[resource] = raw_plan_value

    canonical_qps = finite_positive(canonical_baseline.get("db_qps"), "canonical baseline db_qps")
    outcomes["qps_outer_only"] = math.log(normalized_qps / canonical_qps)
    plan_fingerprint = str(
        metadata.get("workload_plan_fingerprint")
        or metadata.get("plan_fingerprint")
        or ranking.get("workload_plan_fingerprint")
        or ""
    ).strip()
    coverage = metadata.get("plan_fingerprint_coverage")
    if not plan_fingerprint or not isinstance(coverage, Mapping) or coverage.get("complete") is not True:
        raise RuntimeError("candidate full-plan fingerprint evidence is incomplete")

    anchor = {
        "contract": ANCHOR_CONTRACT,
        "original_round_index": int(round_index),
        "holdout_group": f"formal_round_{round_index:06d}",
        "local_baseline_params": dict(baseline_params),
        "local_baseline_resources": {
            resource: float(baseline_real[resource]) for resource in RESOURCE_NAMES
        },
        "parameter_log_deltas": parameter_log_deltas,
        "normalized_actual_resources": normalized_resources,
        "normalized_actual_db_qps": normalized_qps,
        "outcomes": outcomes,
        "raw_plan_log_ratios": raw_plan_log_ratios,
        "workload_plan_fingerprint": plan_fingerprint,
        "plan_fingerprint_coverage": dict(coverage),
        "same_worker_normalization": {
            "worker_key": worker_key(validation),
            "worker_baseline_label": worker_baseline.get("label"),
            "canonical_baseline_label": canonical_baseline.get("label"),
            "raw_candidate_resources": dict(validation.get("resources") or {}),
            "raw_candidate_db_qps": validation.get("db_qps"),
        },
        "qps_used_for_resource_prediction": False,
        "old_calibrated_prediction_used_as_input": False,
    }
    exported = dict(validation)
    exported.update(
        {
            "event": "candidate_validation",
            "round": int(round_offset) + int(round_index),
            "round_index": int(round_offset) + int(round_index),
            "formal_anchor": anchor,
        }
    )
    return exported


def completed_base_rounds(events: Sequence[Mapping[str, Any]]) -> Tuple[Dict[int, int], Dict[int, List[Mapping[str, Any]]]]:
    declared: Dict[int, int] = {}
    rows: Dict[int, List[Mapping[str, Any]]] = {}
    for event in events:
        if event.get("event") == "algorithm_c_physical_batch_start" and event.get("batch_kind") == "base_top5":
            round_value = int(event.get("round_index") or 0)
            declared[round_value] = int(event.get("candidate_count") or 0)
        elif event.get("event") == "algorithm_c_candidate_replay" and event.get("batch_kind") == "base_top5":
            round_value = int(event.get("round_index") or 0)
            validation = event.get("validation")
            if isinstance(validation, Mapping):
                rows.setdefault(round_value, []).append(validation)
    return declared, rows


def export_anchors(
    *,
    state: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    round_offset: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    baseline_by_worker = state.get("baseline_by_worker")
    canonical_baseline = state.get("canonical_baseline")
    if not isinstance(baseline_by_worker, Mapping) or not isinstance(canonical_baseline, Mapping):
        raise RuntimeError("authoritative baseline state is missing")
    declared, replay_rows = completed_base_rounds(events)
    exported: List[Dict[str, Any]] = []
    excluded_rounds: List[Dict[str, Any]] = []
    accepted_rounds: List[int] = []
    for round_index in sorted(declared):
        expected = int(declared[round_index])
        rows = replay_rows.get(round_index, [])
        if expected <= 0:
            excluded_rounds.append({"round_index": round_index, "reason": "zero_physical_candidate_round"})
            continue
        if len(rows) != expected:
            excluded_rounds.append(
                {
                    "round_index": round_index,
                    "reason": "partial_round",
                    "expected": expected,
                    "observed": len(rows),
                }
            )
            continue
        if not all(physical_valid(row) for row in rows):
            excluded_rounds.append(
                {
                    "round_index": round_index,
                    "reason": "round_contains_invalid_physical_replay",
                    "expected": expected,
                    "valid": sum(physical_valid(row) for row in rows),
                }
            )
            continue
        round_exports: List[Dict[str, Any]] = []
        try:
            for row in rows:
                key = worker_key(row)
                baseline = baseline_by_worker.get(key)
                if not key or not isinstance(baseline, Mapping):
                    raise RuntimeError(f"authoritative worker baseline missing: {key!r}")
                round_exports.append(
                    local_anchor(
                        validation=row,
                        round_index=round_index,
                        worker_baseline=baseline,
                        canonical_baseline=canonical_baseline,
                        round_offset=round_offset,
                    )
                )
        except RuntimeError as exc:
            excluded_rounds.append(
                {"round_index": round_index, "reason": "anchor_build_failed", "error": str(exc)}
            )
            continue
        exported.extend(round_exports)
        accepted_rounds.append(round_index)
    manifest = {
        "contract": EXPORT_CONTRACT,
        "anchor_contract": ANCHOR_CONTRACT,
        "source_state_version": state.get("state_version"),
        "round_offset": int(round_offset),
        "accepted_complete_rounds": accepted_rounds,
        "excluded_rounds": excluded_rounds,
        "anchor_count": len(exported),
        "worker_keys": sorted(
            {worker_key(row) for row in exported if worker_key(row)}
        ),
        "technical_failures_in_source_state": int(state.get("technical_failures") or 0),
        "cached_redecisions_exported": 0,
        "qps_used_for_resource_prediction": False,
        "old_calibrated_prediction_used_as_input": False,
        "source_campaign_mutated": False,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    return exported, manifest


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(dict(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--round-offset", type=int, default=1_000_000)
    args = parser.parse_args()
    if args.round_offset < 0:
        raise ValueError("round offset must be non-negative")
    state = read_json(args.state)
    events = list(read_jsonl(args.log))
    rows, manifest = export_anchors(
        state=state,
        events=events,
        round_offset=args.round_offset,
    )
    manifest.update(
        {
            "source_state": str(args.state.resolve()),
            "source_log": str(args.log.resolve()),
            "output": str(args.output.resolve()),
        }
    )
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    atomic_write_jsonl(args.output, rows)
    atomic_write_json(args.manifest, manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
