#!/usr/bin/env python3
"""Module."""


from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "formal10_autonomous_controller_v1"
ALLOWED_PHASES = {
    "bootstrap",
    "diagnostic",
    "formal",
    "complete",
    "failed_closed",
}
REQUIRED_CANDIDATE_FIELDS = {
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
}


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    payload = _canonical_json(value)
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _finite_float(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"{name} must be finite")
    return number


def _load_adapter(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("formal10_scenario_adapter", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("observe", "prepare", "run_batch", "restore_and_verify_baseline"):
        if not callable(getattr(module, name, None)):
            raise RuntimeError(f"adapter missing callable {name}: {path}")
    return module


def _rankdata(values: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for offset in range(cursor, end):
            ranks[indexed[offset][0]] = rank
        cursor = end
    return ranks


def _spearman(predicted: Sequence[float], actual: Sequence[float]) -> float:
    if len(predicted) != len(actual) or len(predicted) < 2:
        raise RuntimeError("Spearman requires at least two paired values")
    left = _rankdata([float(value) for value in predicted])
    right = _rankdata([float(value) for value in actual])
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left[index] - left_mean) * (right[index] - right_mean)
        for index in range(len(left))
    )
    left_norm = sum((value - left_mean) ** 2 for value in left)
    right_norm = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_norm * right_norm)
    if denominator <= 0.0:
        return 0.0
    return numerator / denominator


def _candidate_fingerprint(candidate: Mapping[str, Any]) -> str:
    fingerprint = candidate.get("three_level_fingerprint")
    if isinstance(fingerprint, Mapping):
        fingerprint = fingerprint.get("id")
    value = str(fingerprint or "")
    if not value.startswith("three-level-sha256:"):
        raise RuntimeError("candidate lacks valid three-level fingerprint")
    return value


def _validate_candidate(candidate: Mapping[str, Any]) -> None:
    missing = sorted(REQUIRED_CANDIDATE_FIELDS - set(candidate))
    if missing:
        raise RuntimeError(f"candidate contract fields missing: {missing}")
    _candidate_fingerprint(candidate)
    role = str(candidate.get("selection_role") or candidate.get("role") or "")
    if role not in {"trusted", "uncertain_probe"}:
        raise RuntimeError(f"invalid candidate role: {role}")
    if role == "trusted" and not bool(
        candidate.get("exact_plan_regime_validated", False)
    ):
        raise RuntimeError("trusted candidate has unvalidated exact plan regime")


def _normalized_resource_ratios(point: Mapping[str, Any]) -> List[float]:
    predicted = point.get("predicted_resource") or {}
    actual = point.get("actual_resource") or {}
    ratios: List[float] = []
    for resource in ("cpu", "io", "memory"):
        predicted_value = _finite_float(predicted.get(resource), f"predicted {resource}")
        actual_value = _finite_float(actual.get(resource), f"actual {resource}")
        if predicted_value <= 0.0 or actual_value < 0.0:
            raise RuntimeError(f"invalid {resource} prediction pair")
        ratios.append(actual_value / predicted_value)
    return ratios


def _gradient_reversal_rate(points: Sequence[Mapping[str, Any]]) -> float:
    signs: Dict[str, List[int]] = {}
    for point in points:
        gradients = point.get("g_j") or {}
        if not isinstance(gradients, Mapping):
            continue
        for name, raw_value in gradients.items():
            value = _finite_float(raw_value, f"g_j.{name}")
            sign = 1 if value > 0.0 else (-1 if value < 0.0 else 0)
            if sign:
                signs.setdefault(str(name), []).append(sign)
    comparisons = 0
    reversals = 0
    for values in signs.values():
        for previous, current in zip(values, values[1:]):
            comparisons += 1
            reversals += int(previous != current)
    return reversals / comparisons if comparisons else 0.0


def evaluate_stability(
    audits: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
) -> Dict[str, Any]:
    consecutive_batches = int(thresholds.get("consecutive_batches", 3))
    minimum_trusted_points = int(thresholds.get("minimum_trusted_points", 15))
    minimum_aggregate_spearman = float(
        thresholds.get("minimum_aggregate_spearman", 0.3)
    )
    minimum_top3_hit_rate = float(thresholds.get("minimum_top3_hit_rate", 0.8))
    resource_ratio_min = float(thresholds.get("resource_ratio_min", 0.5))
    resource_ratio_max = float(thresholds.get("resource_ratio_max", 2.0))
    maximum_gradient_reversal_rate = float(
        thresholds.get("maximum_gradient_reversal_rate", 0.4)
    )

    clean_audits = [
        audit
        for audit in audits
        if int(audit.get("technical_failure_count", 0)) == 0
        and bool(audit.get("baseline_restored", False))
        and bool(audit.get("formal_state_unchanged_when_required", False))
    ]
    window = clean_audits[-consecutive_batches:]
    trusted_points: List[Mapping[str, Any]] = []
    batch_spearman: List[float] = []
    top3_hits = 0
    resource_ratios: List[float] = []

    for audit in window:
        points = [
            point
            for point in audit.get("points") or []
            if str(point.get("selection_role") or point.get("role") or "")
            == "trusted"
            and bool(point.get("valid_replay", False))
        ]
        if len(points) < 2:
            continue
        predicted = [
            _finite_float(point.get("predicted_score"), "predicted_score")
            for point in points
        ]
        actual = [
            _finite_float(point.get("actual_score"), "actual_score")
            for point in points
        ]
        rho = _spearman(predicted, actual)
        batch_spearman.append(rho)
        predicted_order = sorted(
            range(len(points)), key=lambda index: predicted[index], reverse=True
        )
        actual_best = max(range(len(points)), key=lambda index: actual[index])
        top3_hits += int(actual_best in predicted_order[:3])
        trusted_points.extend(points)
        for point in points:
            resource_ratios.extend(_normalized_resource_ratios(point))

    aggregate_spearman: Optional[float] = None
    if len(trusted_points) >= 2:
        aggregate_spearman = _spearman(
            [
                _finite_float(point.get("predicted_score"), "predicted_score")
                for point in trusted_points
            ],
            [
                _finite_float(point.get("actual_score"), "actual_score")
                for point in trusted_points
            ],
        )
    top3_hit_rate = (
        top3_hits / len(batch_spearman) if batch_spearman else 0.0
    )
    reversal_rate = _gradient_reversal_rate(trusted_points)
    checks = {
        "enough_clean_batches": len(window) >= consecutive_batches,
        "each_batch_spearman_positive": (
            len(batch_spearman) >= consecutive_batches
            and all(value > 0.0 for value in batch_spearman)
        ),
        "enough_trusted_points": len(trusted_points) >= minimum_trusted_points,
        "aggregate_spearman": (
            aggregate_spearman is not None
            and aggregate_spearman > minimum_aggregate_spearman
        ),
        "top3_hit_rate": top3_hit_rate >= minimum_top3_hit_rate,
        "resource_prediction_bound": (
            bool(resource_ratios)
            and min(resource_ratios) >= resource_ratio_min
            and max(resource_ratios) <= resource_ratio_max
        ),
        "gradient_reversal_rate": (
            reversal_rate <= maximum_gradient_reversal_rate
        ),
    }
    return {
        "stable": all(checks.values()),
        "checks": checks,
        "clean_batch_count": len(clean_audits),
        "window_batch_count": len(window),
        "trusted_point_count": len(trusted_points),
        "batch_spearman": batch_spearman,
        "aggregate_spearman": aggregate_spearman,
        "top3_hit_rate": top3_hit_rate,
        "resource_ratio_min_observed": min(resource_ratios)
        if resource_ratios
        else None,
        "resource_ratio_max_observed": max(resource_ratios)
        if resource_ratios
        else None,
        "gradient_reversal_rate": reversal_rate,
        "thresholds": copy.deepcopy(dict(thresholds)),
    }


def _validate_observation(
    current: Mapping[str, Any],
    previous: Optional[Mapping[str, Any]],
    accepted_target: int,
) -> None:
    accepted = int(current.get("accepted_count", -1))
    physical = int(current.get("physical_replay_count", -1))
    if not 0 <= accepted <= accepted_target:
        raise RuntimeError(f"accepted count outside [0,{accepted_target}]")
    if physical < 0:
        raise RuntimeError("negative physical replay count")
    if previous is not None:
        if accepted < int(previous.get("accepted_count", -1)):
            raise RuntimeError("accepted count decreased")
        if physical < int(previous.get("physical_replay_count", -1)):
            raise RuntimeError("physical replay count decreased")
    batch_active = bool(current.get("batch_active", False))
    if not batch_active and not bool(current.get("baseline_verified", False)):
        raise RuntimeError("baseline is not verified")
    if bool(current.get("formal_state_polluted", False)):
        raise RuntimeError("formal state pollution detected")


def _validate_prepared(prepared: Mapping[str, Any], phase: str) -> None:
    candidates = prepared.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("prepared batch has no candidates")
    if len(candidates) > 5:
        raise RuntimeError("prepared batch exceeds physical cap 5")
    fingerprints: List[str] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise RuntimeError("candidate must be an object")
        _validate_candidate(candidate)
        fingerprints.append(_candidate_fingerprint(candidate))
    if len(set(fingerprints)) != len(fingerprints):
        raise RuntimeError("prepared batch contains duplicate fingerprints")
    if phase == "diagnostic":
        update_flags = prepared.get("formal_update_flags") or {}
        if any(bool(update_flags.get(name, False)) for name in (
            "accepted",
            "incumbent",
            "range",
            "gradient",
        )):
            raise RuntimeError("diagnostic batch permits formal state mutation")
    if bool(prepared.get("qps_used_for_inner_scoring", True)):
        raise RuntimeError("QPS must be outer-audit only")
    if not bool(prepared.get("strict_serial_replay", False)):
        raise RuntimeError("batch is not strict serial")
    if int(prepared.get("maximum_physical_calls", -1)) > 5:
        raise RuntimeError("batch physical cap exceeds 5")


def _append_event(
    audit_path: Path,
    sequence: int,
    previous_hash: str,
    event: Mapping[str, Any],
) -> str:
    record = {
        "schema_version": SCHEMA_VERSION,
        "sequence": sequence,
        "previous_record_sha256": previous_hash,
        "timestamp_epoch": time.time(),
        **copy.deepcopy(dict(event)),
    }
    record_hash = _sha256_bytes(_canonical_json(record))
    record["record_sha256"] = record_hash
    with audit_path.open("ab") as handle:
        handle.write(_canonical_json(record))
        handle.flush()
        os.fsync(handle.fileno())
    return record_hash


def run_controller(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = _load_json(config_path)
    if config.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("controller config schema mismatch")
    accepted_target = int(config.get("accepted_target", -1))
    if accepted_target != 10:
        raise RuntimeError("accepted_target must be 10")
    scenario = str(config.get("scenario") or "")
    if not scenario:
        raise RuntimeError("scenario is required")

    adapter_path = Path(str(config.get("adapter_path") or "")).resolve()
    expected_adapter_sha = str(config.get("adapter_sha256") or "")
    if _sha256_file(adapter_path) != expected_adapter_sha:
        raise RuntimeError("adapter SHA mismatch")
    adapter = _load_adapter(adapter_path)

    controller_path = Path(__file__).resolve()
    expected_controller_sha = str(config.get("controller_sha256") or "")
    if expected_controller_sha and _sha256_file(controller_path) != expected_controller_sha:
        raise RuntimeError("controller SHA mismatch")

    armed = bool(config.get("armed", False))
    arm_token = str(config.get("arm_token_sha256") or "")
    if armed and _sha256_bytes(str(args.arm_token or "").encode("utf-8")) != arm_token:
        raise RuntimeError("armed controller token mismatch")
    if args.execute and not armed:
        raise RuntimeError("execute requested for unarmed controller")

    root = Path(str(config.get("controller_root") or "")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "controller.lock"
    state_path = root / "controller_state.json"
    audit_path = root / "controller_events.jsonl"
    lock_handle = lock_path.open("a+b")
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    persisted: Dict[str, Any] = {}
    if state_path.is_file():
        persisted = _load_json(state_path)
    sequence = int(persisted.get("sequence", 0))
    previous_hash = str(persisted.get("last_record_sha256") or "0" * 64)
    history = list(persisted.get("batch_audits") or [])
    previous_observation = persisted.get("last_observation")
    phase = str(persisted.get("phase") or "bootstrap")
    if phase not in ALLOWED_PHASES:
        raise RuntimeError(f"invalid persisted phase: {phase}")

    maximum_cycles = int(config.get("maximum_cycles", 1000))
    poll_seconds = max(1.0, float(config.get("poll_seconds", 5.0)))
    thresholds = config.get("stability_thresholds") or {}

    for cycle in range(maximum_cycles):
        observation = adapter.observe(config)
        if not isinstance(observation, Mapping):
            raise RuntimeError("adapter observe must return an object")
        _validate_observation(observation, previous_observation, accepted_target)
        accepted_count = int(observation["accepted_count"])
        if bool(observation.get("batch_active", False)):
            sequence += 1
            previous_hash = _append_event(
                audit_path,
                sequence,
                previous_hash,
                {
                    "event": "active_batch_observed",
                    "scenario": scenario,
                    "cycle": cycle,
                    "accepted_count": accepted_count,
                    "physical_replay_count": int(
                        observation["physical_replay_count"]
                    ),
                    "active_batch_id": observation.get("active_batch_id"),
                },
            )
            previous_observation = copy.deepcopy(dict(observation))
            _atomic_json(
                state_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "scenario": scenario,
                    "phase": phase,
                    "sequence": sequence,
                    "last_record_sha256": previous_hash,
                    "last_observation": previous_observation,
                    "batch_audits": history,
                },
            )
            time.sleep(poll_seconds)
            continue
        if accepted_count >= accepted_target:
            phase = "complete"
            sequence += 1
            previous_hash = _append_event(
                audit_path,
                sequence,
                previous_hash,
                {
                    "event": "accepted_target_reached",
                    "scenario": scenario,
                    "accepted_count": accepted_count,
                    "observation": observation,
                },
            )
            _atomic_json(
                state_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "scenario": scenario,
                    "phase": phase,
                    "sequence": sequence,
                    "last_record_sha256": previous_hash,
                    "last_observation": observation,
                    "batch_audits": history,
                },
            )
            return 0

        completed_audit = observation.get("completed_batch_audit")
        if isinstance(completed_audit, Mapping):
            audit_id = str(completed_audit.get("batch_id") or "")
            known_ids = {str(item.get("batch_id") or "") for item in history}
            if audit_id and audit_id not in known_ids:
                history.append(copy.deepcopy(dict(completed_audit)))

        stability = evaluate_stability(history, thresholds)
        phase = "formal" if stability["stable"] else "diagnostic"
        sequence += 1
        previous_hash = _append_event(
            audit_path,
            sequence,
            previous_hash,
            {
                "event": "next_phase_decided",
                "scenario": scenario,
                "cycle": cycle,
                "phase": phase,
                "accepted_count": accepted_count,
                "stability": stability,
            },
        )

        prepared = adapter.prepare(config, phase, observation, stability)
        if not isinstance(prepared, Mapping):
            raise RuntimeError("adapter prepare must return an object")
        _validate_prepared(prepared, phase)
        sequence += 1
        previous_hash = _append_event(
            audit_path,
            sequence,
            previous_hash,
            {
                "event": "batch_prepared",
                "scenario": scenario,
                "phase": phase,
                "prepared_manifest_sha256": prepared.get("manifest_sha256"),
                "candidate_fingerprints": [
                    _candidate_fingerprint(candidate)
                    for candidate in prepared.get("candidates") or []
                ],
                "execute": bool(args.execute),
            },
        )
        _atomic_json(
            state_path,
            {
                "schema_version": SCHEMA_VERSION,
                "scenario": scenario,
                "phase": phase,
                "sequence": sequence,
                "last_record_sha256": previous_hash,
                "last_observation": observation,
                "batch_audits": history,
                "prepared_batch": prepared,
            },
        )
        if not args.execute:
            return 0

        physical_before = int(observation["physical_replay_count"])
        result = adapter.run_batch(config, phase, prepared)
        if not isinstance(result, Mapping):
            raise RuntimeError("adapter run_batch must return an object")
        restored = adapter.restore_and_verify_baseline(config, result)
        if not isinstance(restored, Mapping) or not bool(restored.get("verified", False)):
            raise RuntimeError("baseline restore verification failed")
        after = adapter.observe(config)
        _validate_observation(after, observation, accepted_target)
        physical_delta = int(after["physical_replay_count"]) - physical_before
        if physical_delta < 0 or physical_delta > 5:
            raise RuntimeError(f"batch physical delta outside [0,5]: {physical_delta}")
        sequence += 1
        previous_hash = _append_event(
            audit_path,
            sequence,
            previous_hash,
            {
                "event": "batch_completed",
                "scenario": scenario,
                "phase": phase,
                "physical_delta": physical_delta,
                "accepted_before": accepted_count,
                "accepted_after": int(after["accepted_count"]),
                "baseline_restore": restored,
                "result": result,
            },
        )
        previous_observation = copy.deepcopy(dict(after))
        _atomic_json(
            state_path,
            {
                "schema_version": SCHEMA_VERSION,
                "scenario": scenario,
                "phase": phase,
                "sequence": sequence,
                "last_record_sha256": previous_hash,
                "last_observation": previous_observation,
                "batch_audits": history,
            },
        )
        time.sleep(poll_seconds)

    raise RuntimeError("maximum controller cycles exhausted before target")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--arm-token", default="")
    args = parser.parse_args()
    try:
        return run_controller(args)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "schema_version": SCHEMA_VERSION,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return 75


if __name__ == "__main__":
    raise SystemExit(main())
