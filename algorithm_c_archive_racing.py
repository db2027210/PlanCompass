"""Noise-aware, worker-balanced archive racing for Algorithm C."""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


def _observations(row: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    observations = []
    for observation in row.get("observations") or []:
        qps = observation.get("qps")
        worker = str(observation.get("worker_key") or "").strip()
        if (
            isinstance(qps, (int, float))
            and math.isfinite(float(qps))
            and float(qps) > 0.0
            and worker
        ):
            observations.append(observation)
    return observations


def _log_mean(observations: Sequence[Mapping[str, Any]]) -> float:
    return sum(math.log(float(row["qps"])) for row in observations) / len(observations)


def archive_race_identities(
    archive: Mapping[str, Mapping[str, Any]],
    *,
    physical_slot_budget: int,
    worker_count: int = 2,
) -> List[str]:
    """Choose distinct observed candidates for balanced worker replay.

    Each returned identity consumes exactly ``worker_count`` physical slots in
    the caller. Ranking uses only Algorithm C's normalized live observations.
    """
    if worker_count < 1 or physical_slot_budget < 0:
        raise ValueError("race budgets must be non-negative and worker_count positive")
    candidate_count = physical_slot_budget // worker_count
    rows: List[Tuple[float, int, str]] = []
    for identity, row in archive.items():
        observations = _observations(row)
        if not observations:
            continue
        worker_coverage = len({str(item["worker_key"]) for item in observations})
        # Higher observed QPS comes first. For an exact tie, under-covered
        # candidates get evidence first; identity makes the order reproducible.
        rows.append((_log_mean(observations), -worker_coverage, str(identity)))
    rows.sort(reverse=True)
    return [identity for _mean, _coverage, identity in rows[:candidate_count]]


def balanced_race_params(
    archive: Mapping[str, Mapping[str, Any]],
    identities: Sequence[str],
    *,
    worker_count: int = 2,
) -> List[List[Dict[str, float]]]:
    """Return one same-parameter worker wave per selected archive identity."""
    if worker_count < 1:
        raise ValueError("worker_count must be positive")
    batches = []
    for identity in identities:
        row = archive.get(identity)
        if not row or not isinstance(row.get("params"), Mapping):
            raise ValueError(f"archive identity lacks parameters: {identity}")
        params = {str(name): float(value) for name, value in row["params"].items()}
        if not params or not all(math.isfinite(value) for value in params.values()):
            raise ValueError(f"archive identity has invalid parameters: {identity}")
        batches.append([copy.deepcopy(params) for _ in range(worker_count)])
    return batches


def robust_best_feasible(
    archive: Mapping[str, Mapping[str, Any]],
    *,
    expected_workers: Sequence[str],
    pooled_sigma_log: float,
    confidence_z: float = 1.645,
) -> Optional[Dict[str, Any]]:
    """Select a worker-covered candidate by a conservative log-QPS score."""
    workers = {str(worker).strip() for worker in expected_workers if str(worker).strip()}
    if not workers:
        raise ValueError("expected_workers cannot be empty")
    if not math.isfinite(pooled_sigma_log) or pooled_sigma_log < 0.0:
        raise ValueError("pooled_sigma_log must be finite and non-negative")
    if not math.isfinite(confidence_z) or confidence_z < 0.0:
        raise ValueError("confidence_z must be finite and non-negative")
    eligible = []
    for identity, row in archive.items():
        observations = _observations(row)
        covered = {str(item["worker_key"]) for item in observations}
        if not workers.issubset(covered):
            continue
        logs = [math.log(float(item["qps"])) for item in observations]
        mean = sum(logs) / len(logs)
        if len(logs) > 1:
            sample_sigma = math.sqrt(
                sum((value - mean) ** 2 for value in logs) / (len(logs) - 1)
            )
        else:
            sample_sigma = 0.0
        sigma = max(float(pooled_sigma_log), sample_sigma)
        lower_bound = mean - float(confidence_z) * sigma / math.sqrt(len(logs))
        eligible.append((lower_bound, mean, str(identity), row, observations, sigma))
    if not eligible:
        return None
    lower_bound, mean, identity, row, observations, sigma = max(
        eligible,
        key=lambda item: (item[0], item[1], item[2]),
    )
    return {
        "identity": identity,
        "params": copy.deepcopy(dict(row["params"])),
        "observed_qps_geomean": math.exp(mean),
        "log_qps_lower_confidence_bound": lower_bound,
        "pooled_or_sample_sigma_log": sigma,
        "confidence_z": float(confidence_z),
        "worker_coverage": sorted({str(item["worker_key"]) for item in observations}),
        "observation_count": len(observations),
        "observations": copy.deepcopy(observations),
        "independently_validated": False,
        "selection_contract": "algorithm_c_worker_balanced_archive_lcb_v1",
    }
