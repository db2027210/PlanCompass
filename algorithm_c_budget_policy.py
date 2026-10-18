"""Bounded, recoverable search regions and QPS evidence for Algorithm C pilots."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

Box = Dict[str, Tuple[float, float]]
CONTRACT = "algorithm_c_budgeted_revisitable_regions_v1"
ACTIVE_TIME_CONTRACT = "algorithm_c_controller_active_wall_excluding_host_outage_v1"
OUTER_CONFIRMATION_CONTRACT = "algorithm_c_balanced_outer_qps_confirmation_v1"


def box_identity(box: Mapping[str, Tuple[float, float]]) -> str:
    return hashlib.sha256(json.dumps(box, sort_keys=True).encode()).hexdigest()[:20]


def complement_boxes(before: Box, retained: Box) -> List[Box]:
    """Partition before-minus-retained into slabs, keeping all dimension combinations.

    Closed boxes share only their boundaries; exact replay identity de-duplication
    handles those boundaries. No positive-volume part of the original box is lost.
    """
    if set(before) != set(retained):
        raise ValueError("region dimensions differ")
    remainder = copy.deepcopy(before)
    pieces: List[Box] = []
    for name in sorted(before):
        low, high = before[name]
        keep_low, keep_high = retained[name]
        if not all(math.isfinite(x) for x in (low, high, keep_low, keep_high)):
            raise ValueError("non-finite region")
        if not low <= keep_low <= keep_high <= high:
            raise ValueError(f"retained region is not a subset: {name}")
        if low < keep_low:
            slab = copy.deepcopy(remainder)
            slab[name] = (low, keep_low)
            pieces.append(slab)
        if keep_high < high:
            slab = copy.deepcopy(remainder)
            slab[name] = (keep_high, high)
            pieces.append(slab)
        remainder[name] = (keep_low, keep_high)
    return pieces


def actual_widths(box: Box, original: Box) -> Dict[str, float]:
    result = {}
    for name, (low, high) in box.items():
        lo, hi = original[name]
        transform = (lambda x: x) if name == "parallel_setup_cost" else math.log
        denominator = transform(hi) - transform(lo)
        result[name] = (transform(high) - transform(low)) / denominator if denominator else 0.0
    return result


@dataclass(frozen=True)
class Budget:
    max_search_replays: int
    max_wall_s: float
    max_planning_s: float
    finalization_reserve_s: float
    batch_reserve_s: float

    def __post_init__(self) -> None:
        values = (self.max_wall_s, self.max_planning_s, self.finalization_reserve_s, self.batch_reserve_s)
        if self.max_search_replays < 1 or not all(math.isfinite(x) and x > 0 for x in values):
            raise ValueError("budgets must be finite and positive")
        if self.finalization_reserve_s >= self.max_wall_s:
            raise ValueError("wall budget cannot accommodate finalization")

    def stop_reason(self, *, used: int, wall_s: float, planning_s: float) -> Optional[str]:
        if used >= self.max_search_replays:
            return "search_replay_budget_exhausted"
        if planning_s >= self.max_planning_s:
            return "planning_budget_exhausted"
        if wall_s + self.finalization_reserve_s + self.batch_reserve_s >= self.max_wall_s:
            return "wall_budget_reserved_for_finalization"
        return None


def resume_active_time_accounting(
    prior_trial: Mapping[str, Any],
    *,
    observed_activity_epoch_s: float,
    resume_epoch_s: float,
) -> Dict[str, Any]:
    """Close the previous controller session and exclude the inactive gap.

    ``observed_activity_epoch_s`` is the latest durable state/log timestamp from
    the prior process.  It is a conservative observable boundary rather than an
    assertion that an unjournaled workload completed at exactly that instant.
    """
    original_start = float(prior_trial.get("started_at_epoch_s") or 0.0)
    prior_session_start = float(
        prior_trial.get("active_session_started_at_epoch_s") or original_start
    )
    prior_completed = float(prior_trial.get("completed_active_wall_s") or 0.0)
    prior_excluded = float(prior_trial.get("host_outage_excluded_s") or 0.0)
    values = (
        original_start,
        prior_session_start,
        prior_completed,
        prior_excluded,
        observed_activity_epoch_s,
        resume_epoch_s,
    )
    if not all(math.isfinite(value) and value >= 0.0 for value in values):
        raise ValueError("active-time accounting values must be finite and non-negative")
    if original_start <= 0.0 or prior_session_start < original_start:
        raise ValueError("active-time accounting lacks a valid original session start")
    if resume_epoch_s < prior_session_start:
        raise ValueError("resume time predates the prior session")
    observed_end = min(resume_epoch_s, max(prior_session_start, observed_activity_epoch_s))
    completed = prior_completed + observed_end - prior_session_start
    excluded = prior_excluded + resume_epoch_s - observed_end
    sessions = copy.deepcopy(list(prior_trial.get("active_time_sessions") or []))
    sessions.append({
        "session_started_at_epoch_s": prior_session_start,
        "observed_activity_end_epoch_s": observed_end,
        "observed_active_s": observed_end - prior_session_start,
        "inactive_gap_excluded_s": resume_epoch_s - observed_end,
        "end_is_latest_durable_activity_not_process_exit": True,
    })
    return {
        "active_time_contract": ACTIVE_TIME_CONTRACT,
        "completed_active_wall_s": completed,
        "host_outage_excluded_s": excluded,
        "active_session_started_at_epoch_s": resume_epoch_s,
        "active_time_sessions": sessions,
        "wall_budget_uses_active_controller_time": True,
    }


def evidence_identity(params: Mapping[str, Any]) -> str:
    return json.dumps({k: format(float(v), ".15g") for k, v in sorted(params.items())}, sort_keys=True)


def admit_feasible_evidence(
    archive: Dict[str, Dict[str, Any]],
    *,
    params: Mapping[str, float],
    decision: Mapping[str, Any],
    source: Mapping[str, Any],
) -> bool:
    """Archive a canonical-baseline feasible observation, not a search acceptance.

    The caller must supply a technically valid, normalized real replay and its
    full canonical-baseline semantic decision. Source labels de-duplicate cached
    redecisions. This pool never enters resource prediction or gradient ranking.
    """
    qps = float(decision.get("db_qps") or 0.0)
    if not (decision.get("accepted") is True and decision.get("non_target_safe") is True
            and decision.get("qps_up") is True and math.isfinite(qps) and qps > 0):
        return False
    identity = evidence_identity(params)
    row = archive.setdefault(identity, {"params": dict(params), "observations": []})
    label = str(source.get("label") or "")
    if not label:
        raise ValueError("feasible evidence requires a physical source label")
    if any(x["label"] == label for x in row["observations"]):
        return False
    row["observations"].append({**dict(source), "qps": qps, "decision": copy.deepcopy(dict(decision))})
    return True


def best_feasible(archive: Mapping[str, Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """Choose an observed finalist; independent validation is still required."""
    eligible = []
    for identity, row in archive.items():
        observations = row.get("observations") or []
        if observations:
            log_mean = sum(math.log(x["qps"]) for x in observations) / len(observations)
            eligible.append((log_mean, identity, row))
    if not eligible:
        return None
    log_mean, identity, row = max(eligible, key=lambda x: (x[0], x[1]))
    return {"identity": identity, "params": dict(row["params"]), "observed_qps": math.exp(log_mean),
            "observations": copy.deepcopy(row["observations"]), "independently_validated": False}


def balanced_outer_qps_confirmation(
    candidate_rows: List[Mapping[str, Any]],
    baseline_rows: List[Mapping[str, Any]],
    *,
    static_floor_pct: float,
    confidence_z: float,
) -> Dict[str, Any]:
    """Estimate a balanced, replicated outer-only QPS confidence gate.

    Both candidate and baseline must contain the same two workers with at
    least two independent full-workload observations per worker. The result
    is intentionally unsuitable for inner candidate scoring: it only decides
    whether an already generated C candidate may update the outer interval.
    """

    def grouped_logs(rows: List[Mapping[str, Any]]) -> Dict[str, List[float]]:
        grouped: Dict[str, List[float]] = {}
        for row in rows:
            worker = str(row.get("worker_key") or "").strip()
            qps = float(row.get("db_qps") or 0.0)
            if worker and math.isfinite(qps) and qps > 0.0:
                grouped.setdefault(worker, []).append(math.log(qps))
        return grouped

    candidate = grouped_logs(candidate_rows)
    baseline = grouped_logs(baseline_rows)
    errors: List[str] = []
    workers = sorted(set(candidate) & set(baseline))
    if len(workers) != 2 or set(candidate) != set(baseline):
        errors.append("confirmation_requires_the_same_two_workers")
    for worker in workers:
        if len(candidate[worker]) < 2:
            errors.append(f"candidate_repeat_count_below_2:{worker}")
        if len(baseline[worker]) < 2:
            errors.append(f"baseline_repeat_count_below_2:{worker}")
    if not math.isfinite(static_floor_pct) or static_floor_pct <= 0.0:
        errors.append("static_floor_pct_must_be_positive")
    if not math.isfinite(confidence_z) or confidence_z <= 0.0:
        errors.append("confidence_z_must_be_positive")
    if errors:
        return {
            "contract": OUTER_CONFIRMATION_CONTRACT,
            "ready": False,
            "errors": errors,
            "qps_used_for_inner_scoring": False,
            "external_baseline_information_used": False,
        }

    worker_log_gains: Dict[str, float] = {}
    worker_candidate_sigma_log: Dict[str, float] = {}
    worker_baseline_sigma_log: Dict[str, float] = {}
    variance_of_balanced_mean = 0.0
    worker_weight = 1.0 / len(workers)
    for worker in workers:
        candidate_values = candidate[worker]
        baseline_values = baseline[worker]
        candidate_mean = sum(candidate_values) / len(candidate_values)
        baseline_mean = sum(baseline_values) / len(baseline_values)
        candidate_sigma = math.sqrt(
            sum((value - candidate_mean) ** 2 for value in candidate_values)
            / (len(candidate_values) - 1)
        )
        baseline_sigma = math.sqrt(
            sum((value - baseline_mean) ** 2 for value in baseline_values)
            / (len(baseline_values) - 1)
        )
        worker_log_gains[worker] = candidate_mean - baseline_mean
        worker_candidate_sigma_log[worker] = candidate_sigma
        worker_baseline_sigma_log[worker] = baseline_sigma
        variance_of_balanced_mean += worker_weight ** 2 * (
            candidate_sigma ** 2 / len(candidate_values)
            + baseline_sigma ** 2 / len(baseline_values)
        )
    mean_log_gain = sum(worker_log_gains.values()) / len(workers)
    standard_error_log = math.sqrt(max(0.0, variance_of_balanced_mean))
    static_floor_log = math.log1p(static_floor_pct / 100.0)
    effective_required_log_gain = static_floor_log + confidence_z * standard_error_log
    lower_confidence_log_gain = mean_log_gain - confidence_z * standard_error_log
    result = {
        "contract": OUTER_CONFIRMATION_CONTRACT,
        "ready": True,
        "errors": [],
        "worker_count": len(workers),
        "candidate_repetitions_per_worker": {
            worker: len(candidate[worker]) for worker in workers
        },
        "baseline_repetitions_per_worker": {
            worker: len(baseline[worker]) for worker in workers
        },
        "worker_log_gains": worker_log_gains,
        "worker_candidate_sigma_log": worker_candidate_sigma_log,
        "worker_baseline_sigma_log": worker_baseline_sigma_log,
        "mean_log_gain": mean_log_gain,
        "standard_error_log": standard_error_log,
        "confidence_z": confidence_z,
        "static_floor_pct": static_floor_pct,
        "effective_min_gain_pct": math.expm1(effective_required_log_gain) * 100.0,
        "observed_gain_pct": math.expm1(mean_log_gain) * 100.0,
        "lower_confidence_gain_pct": math.expm1(lower_confidence_log_gain) * 100.0,
        "passed": lower_confidence_log_gain > static_floor_log,
        "aggregation": "equal_worker_weight_geometric_mean_with_welch_standard_error",
        "candidate_physical_replays": len(candidate_rows),
        "baseline_physical_replays": len(baseline_rows),
        "qps_used_for_inner_scoring": False,
        "qps_used_for_outer_interval_control": True,
        "external_baseline_information_used": False,
    }
    result["model_id"] = hashlib.sha256(
        json.dumps(result, sort_keys=True).encode()
    ).hexdigest()
    return result
