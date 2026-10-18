#!/usr/bin/env python3
"""Fail-closed autonomous controller for the PostgreSQL XP formal campaign.

The controller is dry-run by default. Real local process execution requires both
``--execute`` and a SHA-bound armed manifest. It never supports SSH or MySQL.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA = "xp_autonomous_controller_state_v1"
CONTROLLER_CONTRACT = "xp_formal_accepted10_autonomous_v1"
CPU_AFFINITY = "32-51,288-307"
FORBIDDEN_CPU_AFFINITY = ("184-199", "440-455")
AUDIT_FIELDS = (
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
)
PARAM_NAMES = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
TECHNICAL_TERMINAL_EVENTS = {
    "optimizer_process_exception",
    "optimizer_process_signal",
    "optimizer_process_exit",
}


class ContractFailure(RuntimeError):
    """A fail-closed contract violation."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def fingerprint(candidate: Mapping[str, Any]) -> str:
    value = candidate.get("three_level_fingerprint")
    if isinstance(value, dict):
        value = value.get("id")
    if not isinstance(value, str) or not value.startswith("three-level-sha256:"):
        raise ContractFailure("candidate lacks a valid three-level fingerprint")
    return value


def parameter_signature(params: Mapping[str, Any]) -> Tuple[Tuple[str, float], ...]:
    if set(params) != set(PARAM_NAMES):
        raise ContractFailure("candidate parameter vector is incomplete")
    return tuple(sorted((name, round(float(params[name]), 12)) for name in PARAM_NAMES))


def latest_history(root: Path) -> Path:
    histories = sorted(
        path
        for path in root.glob("optimizer_history_*.jsonl")
        if path.is_file() and not path.name.endswith("_db_monitor.csv")
    )
    if len(histories) != 1:
        raise ContractFailure(
            f"expected exactly one optimizer history in {root}, found {len(histories)}"
        )
    return histories[0]


def read_events(history: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line_number, raw in enumerate(
        history.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ContractFailure(
                f"invalid JSONL at {history}:{line_number}"
            ) from exc
        if not isinstance(row, dict):
            raise ContractFailure(f"non-object JSONL event at {history}:{line_number}")
        events.append(row)
    if not events:
        raise ContractFailure(f"empty optimizer history: {history}")
    return events


def find_top5_path(root: Path) -> Path:
    matches = sorted(root.glob("*offline_top5.json"))
    if len(matches) != 1:
        raise ContractFailure(f"expected one sealed Top-5 file, found {len(matches)}")
    return matches[0]


def candidate_by_params(
    candidates: Iterable[Mapping[str, Any]],
    params: Mapping[str, Any],
) -> Optional[Mapping[str, Any]]:
    wanted = parameter_signature(params)
    matches = [
        candidate
        for candidate in candidates
        if parameter_signature(candidate.get("optimized_params") or {}) == wanted
    ]
    if len(matches) > 1:
        raise ContractFailure("candidate parameter vector is ambiguous")
    return matches[0] if matches else None


def _counter(root: Path, name: str) -> int:
    path = root / "state" / name
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        raise ContractFailure(f"invalid counter: {path}") from exc


def _latest_event(events: Sequence[Mapping[str, Any]], name: str) -> Optional[Mapping[str, Any]]:
    return next((event for event in reversed(events) if event.get("event") == name), None)


def audit_batch(root: Path) -> Dict[str, Any]:
    """Audit one completed/abandoned batch without changing any formal state."""
    config_path = root / "config.json"
    config = read_json(config_path)
    formal = config.get("formal_outer_protocol") or {}
    history = latest_history(root)
    events = read_events(history)
    top5_path = find_top5_path(root)
    top5 = read_json(top5_path)
    candidates = top5.get("candidates") or []
    if len(candidates) != 5:
        raise ContractFailure("sealed candidate list must contain exactly five rows")
    selected_by_index = {
        index: candidate for index, candidate in enumerate(candidates, start=1)
    }
    accepted_start = int(formal.get("formal_search_accepted_count_start"))
    replay_start = int(formal.get("formal_search_replay_count_start"))
    accepted_now = _counter(root, "formal_search_accepted_counter.txt")
    replay_now = _counter(root, "formal_search_replay_counter.txt")
    global_now = _counter(root, "global_replay_counter.txt")
    if accepted_now < accepted_start or replay_now < replay_start:
        raise ContractFailure("formal counters moved backwards")
    if replay_now != global_now:
        raise ContractFailure("formal/global replay counters diverged")
    reservations = [
        event
        for event in events
        if event.get("event")
        in {
            "formal_initial_final_physical_call_reserved",
            "formal_feedback_physical_call_reserved",
        }
    ]
    if len(reservations) != replay_now - replay_start:
        raise ContractFailure(
            "physical reservation count does not equal replay counter delta"
        )
    if len(reservations) > 5:
        raise ContractFailure("batch exceeded physical cap5")
    for reservation in reservations:
        if (
            reservation.get("technical_failure_consumes_reserved_call") is not True
            or reservation.get("accepted_counter_updated") is not False
            or reservation.get("gradient_incumbent_or_range_updated") is not False
        ):
            raise ContractFailure("reservation violates formal state-update contract")

    completion = _latest_event(
        events, "experience_table_gradient_initial_real_replay_complete"
    )
    evaluations = list((completion or {}).get("candidate_evaluations") or [])
    valid_evaluations = [
        row
        for row in evaluations
        if row.get("candidate_actual_score") is not None
        and isinstance(row.get("candidate_final_validation"), dict)
        and bool(row.get("candidate_final_validation"))
    ]
    completed_candidate_indexes = {
        int(row.get("candidate_index"))
        for row in valid_evaluations
        if row.get("candidate_index") is not None
    }
    attempted_fingerprints: List[str] = []
    for reservation in reservations:
        index = reservation.get("candidate_index")
        if index is None:
            continue
        candidate = selected_by_index.get(int(index))
        if candidate is None:
            raise ContractFailure(
                f"reservation candidate_index={index} absent from sealed Top-5"
            )
        attempted_fingerprints.append(fingerprint(candidate))
    for evaluation in valid_evaluations:
        params = evaluation.get("candidate_optimized_params") or {}
        candidate = candidate_by_params(candidates, params)
        if candidate is None:
            raise ContractFailure("valid replay candidate absent from sealed Top-5")
        attempted_fingerprints.append(fingerprint(candidate))
    attempted_fingerprints = list(dict.fromkeys(attempted_fingerprints))

    technical_failure_count = max(0, len(reservations) - len(completed_candidate_indexes))
    terminal = next(
        (
            event
            for event in reversed(events)
            if event.get("event") in TECHNICAL_TERMINAL_EVENTS
        ),
        None,
    )
    batch_status = "completed"
    if technical_failure_count:
        batch_status = "technical_failure"
    if terminal is None and reservations:
        batch_status = "orphaned_reservation_technical_failure"
    rejected = [
        row
        for row in valid_evaluations
        if row.get("accepted_by_actual_score") is False
    ]
    accepted_evaluations = [
        row for row in valid_evaluations if row.get("accepted_by_actual_score") is True
    ]
    if len(accepted_evaluations) != accepted_now - accepted_start:
        raise ContractFailure(
            "accepted counter delta does not match accepted valid replay evidence"
        )
    for row in rejected:
        common = row.get("common_outer_acceptance") or {}
        if common.get("accepted") is True:
            raise ContractFailure("rejected candidate has accepted common-outer evidence")

    audit = {
        "schema_version": "xp_batch_audit_v1",
        "batch_root": str(root),
        "batch_root_sha_inputs": {
            "config": sha256(config_path),
            "history": sha256(history),
            "top5": sha256(top5_path),
        },
        "status": batch_status,
        "accepted_start": accepted_start,
        "accepted_end": accepted_now,
        "accepted_added": accepted_now - accepted_start,
        "formal_replay_start": replay_start,
        "formal_replay_end": replay_now,
        "physical_calls": len(reservations),
        "valid_replays": len(valid_evaluations),
        "technical_failures": technical_failure_count,
        "rejected_count": len(rejected),
        "attempted_three_level_fingerprints": attempted_fingerprints,
        "candidate_evaluations": valid_evaluations,
        "sealed_candidates": candidates,
        "terminal_event": copy.deepcopy(terminal),
        "qps_used_for_inner_scoring": False,
        "rejected_updates_forbidden": True,
        "audited_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    return audit


def _numeric_confidence(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "score", "confidence"):
            item = value.get(key)
            if isinstance(item, (int, float)) and math.isfinite(float(item)):
                return float(item)
    return None


def _rank(values: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        average = (start + 1 + end) / 2.0
        for offset in range(start, end):
            ranks[indexed[offset][0]] = average
        start = end
    return ranks


def spearman(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    if len(left) != len(right) or len(left) < 2:
        return None
    rank_left = _rank(left)
    rank_right = _rank(right)
    mean_left = sum(rank_left) / len(rank_left)
    mean_right = sum(rank_right) / len(rank_right)
    numerator = sum(
        (a - mean_left) * (b - mean_right)
        for a, b in zip(rank_left, rank_right)
    )
    denominator = math.sqrt(
        sum((a - mean_left) ** 2 for a in rank_left)
        * sum((b - mean_right) ** 2 for b in rank_right)
    )
    return numerator / denominator if denominator else None


def _resource_factor(predicted: float, actual: float) -> Optional[float]:
    if predicted <= 0.0 or actual <= 0.0:
        return None
    return max(predicted / actual, actual / predicted)


def stability_record(batch_audit: Mapping[str, Any]) -> Dict[str, Any]:
    candidates = list(batch_audit.get("sealed_candidates") or [])
    evaluations = list(batch_audit.get("candidate_evaluations") or [])
    enriched: List[Dict[str, Any]] = []
    for evaluation in evaluations:
        candidate = candidate_by_params(
            candidates,
            evaluation.get("candidate_optimized_params") or {},
        )
        if candidate is None:
            continue
        confidence = _numeric_confidence(candidate.get("confidence"))
        trust_label = str(
            candidate.get("trust_class")
            or candidate.get("candidate_class")
            or ""
        ).strip().lower()
        plan_fp = candidate.get("workload_plan_fingerprint")
        exact_regime = bool(plan_fp)
        trusted = bool(
            trust_label == "trusted"
            or (confidence is not None and confidence >= 0.70 and exact_regime)
        )
        predicted_score = candidate.get("candidate_score")
        actual_score = evaluation.get("candidate_actual_score")
        if predicted_score is None or actual_score is None:
            continue
        enriched.append({
            "candidate": candidate,
            "evaluation": evaluation,
            "trusted": trusted,
            "predicted_score": float(predicted_score),
            "actual_score": float(actual_score),
            "predicted_rank": int(candidate.get("replay_rank") or candidate.get("rank") or 0),
        })
    trusted_rows = [row for row in enriched if row["trusted"]]
    rho = spearman(
        [row["predicted_score"] for row in trusted_rows],
        [row["actual_score"] for row in trusted_rows],
    )
    top3_hit: Optional[bool] = None
    if enriched:
        actual_best = max(enriched, key=lambda row: row["actual_score"])
        top3_hit = bool(actual_best["predicted_rank"] <= 3)

    factors: Dict[str, List[float]] = {"cpu": [], "io": [], "memory": []}
    reversal_flags: List[bool] = []
    for row in trusted_rows:
        candidate = row["candidate"]
        evaluation = row["evaluation"]
        predicted = candidate.get("predicted_resource") or {}
        actual = (
            (evaluation.get("candidate_final_validation") or {}).get(
                "resource_normalized"
            )
            or {}
        )
        for resource in factors:
            try:
                factor = _resource_factor(
                    float(predicted.get(resource)),
                    float(actual.get(resource)),
                )
            except (TypeError, ValueError):
                factor = None
            if factor is not None:
                factors[resource].append(factor)
        estimated_score = evaluation.get("estimated_score")
        if estimated_score is not None:
            predicted_delta = float(row["predicted_score"]) - float(estimated_score)
            actual_delta = float(row["actual_score"]) - float(estimated_score)
            if predicted_delta != 0.0 and actual_delta != 0.0:
                reversal_flags.append(
                    math.copysign(1.0, predicted_delta)
                    != math.copysign(1.0, actual_delta)
                )
    max_factors = {
        resource: max(values) if values else None
        for resource, values in factors.items()
    }
    reversal_rate = (
        sum(1 for value in reversal_flags if value) / len(reversal_flags)
        if reversal_flags
        else None
    )
    return {
        "schema_version": "xp_stability_record_v1",
        "batch_root": batch_audit.get("batch_root"),
        "batch_status": batch_audit.get("status"),
        "trusted_count": len(trusted_rows),
        "uncertain_probe_count": len(enriched) - len(trusted_rows),
        "trusted_spearman": rho,
        "actual_best_in_predicted_top3": top3_hit,
        "exact_regime_resource_error_factor_max": max_factors,
        "gradient_sign_reversal_count": sum(reversal_flags),
        "gradient_sign_comparison_count": len(reversal_flags),
        "gradient_sign_reversal_rate": reversal_rate,
        "technical_failures": int(batch_audit.get("technical_failures") or 0),
    }


def rolling_stability_gate(
    records: Sequence[Mapping[str, Any]],
    campaign: Mapping[str, Any],
) -> Dict[str, Any]:
    stability = campaign.get("stability") or {}
    window = max(1, int(stability.get("consecutive_batch_window", 2)))
    selected = list(records[-window:])
    reasons: List[str] = []
    if len(selected) < window:
        reasons.append("insufficient_consecutive_batches")
    for offset, record in enumerate(selected):
        prefix = f"window[{offset}]"
        if int(record.get("technical_failures") or 0) != 0:
            reasons.append(f"{prefix}:technical_failure")
        if int(record.get("trusted_count") or 0) < int(
            stability.get("minimum_trusted_count", 3)
        ):
            reasons.append(f"{prefix}:trusted_count")
        rho = record.get("trusted_spearman")
        if rho is None or float(rho) <= float(stability.get("minimum_spearman", 0.0)):
            reasons.append(f"{prefix}:trusted_spearman")
        if record.get("actual_best_in_predicted_top3") is not True:
            reasons.append(f"{prefix}:top3_miss")
        factors = record.get("exact_regime_resource_error_factor_max") or {}
        max_factor = float(stability.get("maximum_resource_error_factor", 10.0))
        for resource in ("cpu", "io", "memory"):
            value = factors.get(resource)
            if value is None or float(value) >= max_factor:
                reasons.append(f"{prefix}:{resource}_prediction_error")
        reversal = record.get("gradient_sign_reversal_rate")
        if reversal is None or float(reversal) > float(
            stability.get("maximum_gradient_sign_reversal_rate", 0.5)
        ):
            reasons.append(f"{prefix}:gradient_sign_reversal")
    return {
        "schema_version": "xp_rolling_stability_gate_v1",
        "window_size": window,
        "records_used": len(selected),
        "passed": not reasons,
        "reasons": reasons,
        "manual_promotion_allowed": False,
        "failed_action": "diagnostic_fail_closed",
        "records": copy.deepcopy(selected),
    }


def _latest_ranking(events: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    event = next(
        (
            row
            for row in reversed(events)
            if row.get("event") == "experience_table_all_subset_ranking"
        ),
        None,
    )
    if event is None:
        raise ContractFailure("batch history has no candidate ranking")
    rankings = event.get("candidate_rankings") or []
    if not isinstance(rankings, list) or not rankings:
        raise ContractFailure("latest candidate ranking is empty")
    return rankings


def select_next_candidates(
    source_root: Path,
    additional_exclusions: Sequence[str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    config = read_json(source_root / "config.json")
    formal = config.get("formal_outer_protocol") or {}
    exclusions = list(
        dict.fromkeys(
            list(formal.get("excluded_prior_three_level_fingerprints") or [])
            + list(additional_exclusions)
        )
    )
    rankings = _latest_ranking(read_events(latest_history(source_root)))
    selected: List[Dict[str, Any]] = []
    seen = set(exclusions)
    for source_candidate in rankings:
        fp = fingerprint(source_candidate)
        if fp in seen:
            continue
        missing = [field for field in AUDIT_FIELDS if field not in source_candidate]
        if missing:
            raise ContractFailure(f"candidate {fp} missing audit fields: {missing}")
        parameter_signature(source_candidate.get("optimized_params") or {})
        candidate = copy.deepcopy(source_candidate)
        candidate["source_rank"] = candidate.get("rank")
        candidate["replay_rank"] = len(selected) + 1
        selected.append(candidate)
        seen.add(fp)
        if len(selected) == 5:
            break
    if len(selected) != 5:
        raise ContractFailure(
            "fewer than five novel candidates; return to diagnostic candidate generation"
        )
    return selected, exclusions


def _replace_paths(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: _replace_paths(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_paths(item, old, new) for item in value]
    if isinstance(value, str):
        return value.replace(old, new)
    return value


def create_launcher(
    *,
    target_root: Path,
    config_sha: str,
    top5_sha: str,
    guard_sha: str,
    optimizer_sha: str,
    manifest_sha: str,
) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
OUTPUT={target_root}
CONFIG="$OUTPUT/config.json"
TOP5="$OUTPUT/sealed_top5.json"
GUARD="$OUTPUT/xp_candidate_exclusion_guard.py"
OPTIMIZER="$OUTPUT/parameter_optimizer_xp_autonomous.py"
MANIFEST="$OUTPUT/batch_manifest.json"
PYTHON=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
AFFINITY={CPU_AFFINITY}

test "$AFFINITY" = "{CPU_AFFINITY}"
test "$(sha256sum "$CONFIG" | awk '{{print $1}}')" = "{config_sha}"
test "$(sha256sum "$TOP5" | awk '{{print $1}}')" = "{top5_sha}"
test "$(sha256sum "$GUARD" | awk '{{print $1}}')" = "{guard_sha}"
test "$(sha256sum "$OPTIMIZER" | awk '{{print $1}}')" = "{optimizer_sha}"
test "$(sha256sum "$MANIFEST" | awk '{{print $1}}')" = "{manifest_sha}"
test "$(jq -r '.formal_outer_protocol.candidate_replay_top_k' "$CONFIG")" = "5"
test "$(jq -r '.formal_outer_protocol.excluded_prior_three_level_fingerprints | unique | length' "$CONFIG")" -ge "5"
test "$(jq -r '.candidates | length' "$TOP5")" = "5"
! pgrep -af "$CONFIG"

EXPECTED_BASELINE=$'cpu_index_tuple_cost=0.005\\ncpu_operator_cost=0.0025\\ncpu_tuple_cost=0.01\\nparallel_setup_cost=1000\\nparallel_tuple_cost=0.1\\nrandom_page_cost=4\\nseq_page_cost=0.2'
ACTUAL_BASELINE="$(PGPASSWORD=postgres psql -h 127.0.0.1 -p 59630 -U postgres -d postgres -Atc "select name||'='||setting from pg_settings where name in ('seq_page_cost','random_page_cost','cpu_tuple_cost','cpu_index_tuple_cost','cpu_operator_cost','parallel_setup_cost','parallel_tuple_cost') order by name")"
test "$ACTUAL_BASELINE" = "$EXPECTED_BASELINE"

cd /home/anonymous/program/resource_adaptive
exec taskset -c "$AFFINITY" env OPTIMIZER_CONFIG="$CONFIG" "$PYTHON" -u "$OPTIMIZER"
"""


def build_next_batch_package(
    *,
    source_root: Path,
    package_root: Path,
    target_root: Path,
    batch_audit: Mapping[str, Any],
    accepted_count: int,
    replay_count: int,
) -> Dict[str, Any]:
    """Create a zero-call next-batch package from the latest sealed ranking."""
    if package_root.exists():
        raise ContractFailure(f"package already exists: {package_root}")
    candidates, exclusions = select_next_candidates(
        source_root,
        batch_audit.get("attempted_three_level_fingerprints") or [],
    )
    package_root.mkdir(parents=True)
    (package_root / "state").mkdir()
    (package_root / "subset_logs").mkdir()
    source_config = read_json(source_root / "config.json")
    config = _replace_paths(source_config, str(source_root), str(target_root))
    formal = config["formal_outer_protocol"]
    hard_stop = replay_count + 5
    formal.update({
        "excluded_prior_three_level_fingerprints": exclusions,
        "formal_search_accepted_count_start": accepted_count,
        "formal_search_replay_count_start": replay_count,
        "global_replay_count_start": replay_count,
        "global_replay_ordinal_at_binding": replay_count,
        "initial_real_replay_count": replay_count,
        "formal_search_attempt_safety_cap": hard_stop,
        "formal_search_maximum_replay_count": hard_stop,
        "maximum_cumulative_real_replay_count": hard_stop,
        "formal_search_replay_cumulative_tiers": [hard_stop],
        "candidate_replay_top_k": 5,
        "formal_search_initial_replay_top_k": 5,
        "formal_search_max_replay_top_k": 5,
        "skip_initial_final_real_validation": True,
        "formal_search_accepted_counter_path": str(
            target_root / "state" / "formal_search_accepted_counter.txt"
        ),
        "formal_search_replay_counter_path": str(
            target_root / "state" / "formal_search_replay_counter.txt"
        ),
        "global_replay_counter_path": str(
            target_root / "state" / "global_replay_counter.txt"
        ),
        "formal_startup_baseline_counter_path": str(
            target_root / "state" / "formal_startup_baseline_counter.txt"
        ),
    })
    config["logging"]["history_path"] = str(target_root / "optimizer_history.jsonl")
    gradient = config["optimizer"]["gradient"]
    gradient["subset_log_dir"] = str(target_root / "subset_logs")
    gradient["plan_basin_escape"]["state_path"] = str(
        target_root / "state" / "plan_basin_escape_state.json"
    )
    config["_xp_autonomous_batch"] = {
        "contract": CONTROLLER_CONTRACT,
        "source_root": str(source_root),
        "accepted_start": accepted_count,
        "replay_start": replay_count,
        "hard_stop": hard_stop,
        "physical_cap": 5,
        "serial": True,
        "qps_used_for_inner_scoring": False,
        "technical_failure_consumes_cap": True,
        "rejected_updates_forbidden": True,
        "manual_promotion_allowed": False,
    }
    config_path = package_root / "config.json"
    atomic_json(config_path, config)
    top5 = {
        "schema_version": "xp_autonomous_sealed_top5_v1",
        "source_root": str(source_root),
        "excluded_prior_three_level_fingerprints": exclusions,
        "selected_count": 5,
        "candidates": candidates,
    }
    top5_path = package_root / "sealed_top5.json"
    atomic_json(top5_path, top5)
    for name, value in (
        ("formal_search_accepted_counter.txt", accepted_count),
        ("formal_search_replay_counter.txt", replay_count),
        ("global_replay_counter.txt", replay_count),
        ("formal_startup_baseline_counter.txt", 0),
    ):
        (package_root / "state" / name).write_text(f"{value}\n", encoding="utf-8")
    shutil.copy2(
        source_root / "state" / "plan_basin_escape_state.json",
        package_root / "state" / "plan_basin_escape_state.json",
    )
    optimizer_source = source_root / "parameter_optimizer_xp_cont16b.py"
    if not optimizer_source.is_file():
        optimizer_source = source_root / "parameter_optimizer_xp_autonomous.py"
    if not optimizer_source.is_file():
        raise ContractFailure("source has no isolated skip-initial optimizer")
    optimizer_path = package_root / "parameter_optimizer_xp_autonomous.py"
    shutil.copy2(optimizer_source, optimizer_path)
    guard_source_path = source_root / "xp_candidate_exclusion_guard.py"
    guard_text = guard_source_path.read_text(encoding="utf-8")
    guard_text = guard_text.replace(str(source_root), str(target_root))
    for old_name in ("cont16b_offline_top5.json", "cont16_offline_top5.json"):
        guard_text = guard_text.replace(old_name, "sealed_top5.json")
    if guard_text.count("sealed_top5.json") != 1:
        raise ContractFailure("guard sealed Top-5 binding is ambiguous")
    guard_path = package_root / "xp_candidate_exclusion_guard.py"
    guard_path.write_text(guard_text, encoding="utf-8")
    os.chmod(guard_path, 0o700)
    manifest = {
        "schema_version": "xp_autonomous_batch_manifest_v1",
        "contract": CONTROLLER_CONTRACT,
        "source_root": str(source_root),
        "target_root": str(target_root),
        "accepted_start": accepted_count,
        "replay_start": replay_count,
        "hard_stop": hard_stop,
        "physical_cap": 5,
        "cpu_affinity": CPU_AFFINITY,
        "prepared_only_no_workload_started": True,
        "manual_promotion_allowed": False,
        "batch_audit_sha256": hashlib.sha256(
            json.dumps(batch_audit, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "files": {
            "config.json": sha256(config_path),
            "sealed_top5.json": sha256(top5_path),
            "xp_candidate_exclusion_guard.py": sha256(guard_path),
            "parameter_optimizer_xp_autonomous.py": sha256(optimizer_path),
        },
    }
    manifest_path = package_root / "batch_manifest.json"
    atomic_json(manifest_path, manifest)
    launcher_path = package_root / "launch_serial.sh"
    launcher_path.write_text(
        create_launcher(
            target_root=target_root,
            config_sha=sha256(config_path),
            top5_sha=sha256(top5_path),
            guard_sha=sha256(guard_path),
            optimizer_sha=sha256(optimizer_path),
            manifest_sha=sha256(manifest_path),
        ),
        encoding="utf-8",
    )
    os.chmod(launcher_path, 0o700)
    return {
        "package_root": str(package_root),
        "target_root": str(target_root),
        "manifest_sha256": sha256(manifest_path),
        "launcher_sha256": sha256(launcher_path),
        "selected_fingerprints": [fingerprint(candidate) for candidate in candidates],
        "physical_calls": 0,
        "workload_started": False,
    }


def zero_call_preflight(package_root: Path) -> Dict[str, Any]:
    manifest_path = package_root / "batch_manifest.json"
    manifest = read_json(manifest_path)
    files = manifest.get("files") or {}
    for name, expected in files.items():
        path = package_root / name
        if sha256(path) != expected:
            raise ContractFailure(f"package SHA mismatch: {name}")
    config = read_json(package_root / "config.json")
    top5 = read_json(package_root / "sealed_top5.json")
    formal = config.get("formal_outer_protocol") or {}
    candidates = top5.get("candidates") or []
    exclusions = set(formal.get("excluded_prior_three_level_fingerprints") or [])
    selected = [fingerprint(candidate) for candidate in candidates]
    if len(candidates) != 5 or len(set(selected)) != 5 or exclusions & set(selected):
        raise ContractFailure("sealed Top-5 novelty/uniqueness check failed")
    for candidate in candidates:
        missing = [field for field in AUDIT_FIELDS if field not in candidate]
        if missing:
            raise ContractFailure(f"sealed candidate missing audit fields: {missing}")
    if formal.get("candidate_replay_top_k") != 5:
        raise ContractFailure("physical cap5 is not configured")
    if config.get("_xp_autonomous_batch", {}).get("qps_used_for_inner_scoring") is not False:
        raise ContractFailure("QPS inner scoring is forbidden")
    launcher = (package_root / "launch_serial.sh").read_text(encoding="utf-8")
    if f"AFFINITY={CPU_AFFINITY}" not in launcher:
        raise ContractFailure("XP affinity is not exact")
    lower = launcher.lower()
    if " ssh " in f" {lower} " or "\nssh " in lower or "mysql " in lower:
        raise ContractFailure("launcher contains forbidden SSH/MySQL execution")
    syntax = subprocess.run(
        ["bash", "-n", str(package_root / "launch_serial.sh")],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if syntax.returncode != 0:
        raise ContractFailure(f"launcher syntax failed: {syntax.stderr}")
    return {
        "schema_version": "xp_zero_call_preflight_v1",
        "status": "passed",
        "physical_calls": 0,
        "workload_started": False,
        "candidate_count": 5,
        "novel_fingerprint_count": 5,
        "cpu_affinity": CPU_AFFINITY,
        "manifest_sha256": sha256(manifest_path),
    }


def initial_state(campaign: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "contract": CONTROLLER_CONTRACT,
        "phase": "batch_audit",
        "mode": "dry_run",
        "accepted_target": 10,
        "current_batch_root": str(campaign["current_batch_root"]),
        "batch_sequence": 0,
        "batch_audits": [],
        "stability_records": [],
        "rolling_stability_gate": None,
        "last_package": None,
        "last_error": None,
        "manual_promotion_allowed": False,
    }


def validate_campaign(campaign: Mapping[str, Any]) -> None:
    if campaign.get("contract") != CONTROLLER_CONTRACT:
        raise ContractFailure("campaign contract mismatch")
    if int(campaign.get("accepted_target", 0)) != 10:
        raise ContractFailure("accepted target must be 10")
    if int(campaign.get("physical_cap_per_batch", 0)) != 5:
        raise ContractFailure("physical cap per batch must be 5")
    if campaign.get("cpu_affinity") != CPU_AFFINITY:
        raise ContractFailure("XP CPU affinity mismatch")
    if campaign.get("qps_used_for_inner_scoring") is not False:
        raise ContractFailure("QPS must be outer-only")
    if campaign.get("manual_promotion_allowed") is not False:
        raise ContractFailure("manual promotion must be forbidden")


def verify_armed_manifest(
    manifest_path: Path,
    campaign_path: Path,
    controller_path: Path,
) -> Dict[str, Any]:
    manifest = read_json(manifest_path)
    if (
        manifest.get("contract") != CONTROLLER_CONTRACT
        or manifest.get("mode") != "armed"
        or manifest.get("allow_local_process_execution") is not True
        or manifest.get("cpu_affinity") != CPU_AFFINITY
        or manifest.get("manual_promotion_allowed") is not False
    ):
        raise ContractFailure("armed manifest contract is invalid")
    if manifest.get("controller_sha256") != sha256(controller_path):
        raise ContractFailure("armed manifest controller SHA mismatch")
    if manifest.get("campaign_sha256") != sha256(campaign_path):
        raise ContractFailure("armed manifest campaign SHA mismatch")
    if any(key in manifest for key in ("promote", "force_promote", "override_gate")):
        raise ContractFailure("armed manifest contains forbidden promotion override")
    return manifest


def safe_local_command(argv: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    if not argv:
        raise ContractFailure("empty local command")
    joined = " ".join(argv).lower()
    if "ssh" in joined or "mysql" in joined:
        raise ContractFailure("SSH/MySQL command is forbidden")
    command_env = os.environ.copy()
    if Path(argv[0]).name == "psql":
        command_env.setdefault("PGPASSWORD", "postgres")
    return subprocess.run(
        list(argv),
        env=command_env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


@dataclass
class Controller:
    campaign_path: Path
    state_path: Path
    execute: bool = False
    armed_manifest_path: Optional[Path] = None

    def __post_init__(self) -> None:
        self.campaign = read_json(self.campaign_path)
        validate_campaign(self.campaign)
        self.controller_path = Path(__file__).resolve()
        self.journal_path = self.state_path.with_suffix(".jsonl")
        if self.state_path.exists():
            self.state = read_json(self.state_path)
        else:
            self.state = initial_state(self.campaign)
            atomic_json(self.state_path, self.state)
        if self.state.get("schema_version") != SCHEMA:
            raise ContractFailure("controller state schema mismatch")
        if self.execute:
            if self.armed_manifest_path is None:
                raise ContractFailure("--execute requires --armed-manifest")
            verify_armed_manifest(
                self.armed_manifest_path,
                self.campaign_path,
                self.controller_path,
            )
            self.state["mode"] = "armed"
        else:
            self.state["mode"] = "dry_run"

    def persist(self, event: str, **fields: Any) -> None:
        self.state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        atomic_json(self.state_path, self.state)
        append_jsonl(
            self.journal_path,
            {
                "event": event,
                "phase": self.state.get("phase"),
                "mode": self.state.get("mode"),
                "timestamp": self.state["updated_at"],
                **fields,
            },
        )

    def fail_closed(self, reason: str, *, diagnostic: bool = False) -> Dict[str, Any]:
        self.state["phase"] = "diagnostic_fail_closed" if diagnostic else "fail_closed"
        self.state["last_error"] = reason
        self.state["manual_promotion_allowed"] = False
        self.persist("controller_fail_closed", reason=reason, diagnostic=diagnostic)
        return self.state

    def step(self) -> Dict[str, Any]:
        phase = self.state["phase"]
        if phase in {"complete", "fail_closed", "diagnostic_fail_closed"}:
            return self.state
        try:
            if phase == "batch_audit":
                audit = audit_batch(Path(self.state["current_batch_root"]))
                self.state["batch_audits"].append(audit)
                self.state["phase"] = "baseline_exact_restore"
                self.persist("batch_audit_complete", audit=audit)
                return self.state
            if phase == "baseline_exact_restore":
                if not self.execute:
                    self.state["phase"] = "baseline_exact_verify"
                    self.persist(
                        "baseline_restore_dry_run",
                        command=self.campaign.get("baseline_restore_argv"),
                        physical_calls=0,
                    )
                    return self.state
                result = safe_local_command(
                    self.campaign["baseline_restore_argv"],
                    int(self.campaign.get("baseline_restore_timeout_sec", 300)),
                )
                if result.returncode != 0:
                    return self.fail_closed("baseline exact restore failed")
                self.state["phase"] = "baseline_exact_verify"
                self.persist("baseline_exact_restore_complete")
                return self.state
            if phase == "baseline_exact_verify":
                if not self.execute:
                    self.state["phase"] = "stability_audit"
                    self.persist(
                        "baseline_verify_dry_run",
                        expected=self.campaign["baseline_expected"],
                        physical_calls=0,
                    )
                    return self.state
                result = safe_local_command(
                    self.campaign["baseline_verify_argv"],
                    int(self.campaign.get("baseline_verify_timeout_sec", 30)),
                )
                if result.returncode != 0:
                    return self.fail_closed("baseline exact verification command failed")
                observed = dict(
                    line.split("=", 1)
                    for line in result.stdout.splitlines()
                    if "=" in line
                )
                if observed != self.campaign["baseline_expected"]:
                    return self.fail_closed("baseline exact values mismatch")
                self.state["phase"] = "stability_audit"
                self.persist("baseline_exact_verify_complete", observed=observed)
                return self.state
            if phase == "stability_audit":
                record = stability_record(self.state["batch_audits"][-1])
                self.state["stability_records"].append(record)
                gate = rolling_stability_gate(
                    self.state["stability_records"], self.campaign
                )
                self.state["rolling_stability_gate"] = gate
                accepted = int(self.state["batch_audits"][-1]["accepted_end"])
                if accepted >= 10:
                    self.state["phase"] = "complete"
                    self.persist("accepted_target_reached", accepted=accepted, gate=gate)
                    return self.state
                if not gate["passed"]:
                    return self.fail_closed(
                        "rolling stability gate failed: " + ",".join(gate["reasons"]),
                        diagnostic=True,
                    )
                self.state["phase"] = "candidate_generation"
                self.persist("rolling_stability_gate_passed", gate=gate)
                return self.state
            if phase == "candidate_generation":
                audit = self.state["batch_audits"][-1]
                sequence = int(self.state.get("batch_sequence") or 0) + 1
                package_parent = Path(self.campaign["package_root"])
                package_root = package_parent / f"batch_{sequence:04d}"
                target_parent = Path(self.campaign["target_parent"])
                target_root = target_parent / f"xp_autonomous_batch_{sequence:04d}"
                package = build_next_batch_package(
                    source_root=Path(self.state["current_batch_root"]),
                    package_root=package_root,
                    target_root=target_root,
                    batch_audit=audit,
                    accepted_count=int(audit["accepted_end"]),
                    replay_count=int(audit["formal_replay_end"]),
                )
                self.state["batch_sequence"] = sequence
                self.state["last_package"] = package
                self.state["phase"] = "zero_call_preflight"
                self.persist("next_batch_generated", package=package)
                return self.state
            if phase == "zero_call_preflight":
                package_root = Path(self.state["last_package"]["package_root"])
                preflight = zero_call_preflight(package_root)
                self.state["last_package"]["zero_call_preflight"] = preflight
                self.state["phase"] = "serial_launch"
                self.persist("zero_call_preflight_passed", preflight=preflight)
                return self.state
            if phase == "serial_launch":
                if not self.execute:
                    self.persist(
                        "serial_launch_blocked_by_dry_run",
                        physical_calls=0,
                        workload_started=False,
                    )
                    return self.state
                package_root = Path(self.state["last_package"]["package_root"])
                target_root = Path(self.state["last_package"]["target_root"])
                if target_root.exists():
                    return self.fail_closed("target batch root already exists")
                shutil.copytree(package_root, target_root)
                preflight = zero_call_preflight(target_root)
                if preflight["manifest_sha256"] != self.state["last_package"][
                    "zero_call_preflight"
                ]["manifest_sha256"]:
                    return self.fail_closed("deployed package SHA changed")
                lock_path = Path(self.campaign["serial_lock_path"])
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                with lock_path.open("a+", encoding="utf-8") as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    result = safe_local_command(
                        [str(target_root / "launch_serial.sh")],
                        int(self.campaign.get("batch_timeout_sec", 21600)),
                    )
                self.state["last_package"]["launcher_returncode"] = result.returncode
                self.state["current_batch_root"] = str(target_root)
                self.state["phase"] = "batch_audit"
                self.persist(
                    "serial_launch_complete",
                    returncode=result.returncode,
                    stdout_tail=result.stdout[-4000:],
                    stderr_tail=result.stderr[-4000:],
                )
                return self.state
            raise ContractFailure(f"unknown controller phase: {phase}")
        except (ContractFailure, OSError, ValueError, subprocess.SubprocessError) as exc:
            return self.fail_closed(str(exc))

    def run(self) -> Dict[str, Any]:
        while True:
            before = self.state["phase"]
            self.step()
            after = self.state["phase"]
            if after in {"complete", "fail_closed", "diagnostic_fail_closed"}:
                return self.state
            if not self.execute and after == "serial_launch":
                return self.state
            if before == after:
                return self.fail_closed(f"state machine made no progress at {after}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--armed-manifest", type=Path)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    controller = Controller(
        campaign_path=args.campaign,
        state_path=args.state,
        execute=args.execute,
        armed_manifest_path=args.armed_manifest,
    )
    state = controller.step() if args.once else controller.run()
    print(json.dumps(state, indent=2, sort_keys=True), flush=True)
    return 0 if state.get("phase") not in {"fail_closed"} else 42


if __name__ == "__main__":
    raise SystemExit(main())
