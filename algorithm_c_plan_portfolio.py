"""QPS-blind, staged plan exploration inside a caller-supplied parameter box.

This module generates and ranks offline candidates only. It cannot accept a
physical result, change a search interval, reset misses, or terminate a run.
"""

from __future__ import annotations

import copy
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

CONTRACT = "algorithm_c_resource_plan_portfolio_v2"
PRIMES = (2, 3, 5, 7, 11, 13, 17)


@dataclass(frozen=True)
class PortfolioPolicy:
    local_points: int = 16
    global_points: int = 32
    refinement_points: int = 16
    miss_local_points: int = 8
    miss_global_points: int = 48
    miss_refinement_points: int = 8
    max_workers: int = 4

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 1 for value in asdict(self).values()):
            raise ValueError("portfolio counts and max_workers must be positive integers")


def configured_policy(algorithm_cfg: Mapping[str, Any]) -> Optional[PortfolioPolicy]:
    config = algorithm_cfg.get("inner_search") or {}
    if not config or config.get("enabled") is False:
        return None
    if config.get("contract") != CONTRACT or config.get("enabled") is not True:
        raise ValueError("explicit valid plan-portfolio inner-search contract required")
    unknown = set(config) - {"enabled", "contract", "policy"}
    if unknown:
        raise ValueError(f"unknown portfolio configuration fields: {sorted(unknown)}")
    return PortfolioPolicy(**(config.get("policy") or {}))


def identity(params: Mapping[str, float]) -> Tuple[Tuple[str, str], ...]:
    return tuple((name, float(value).hex()) for name, value in sorted(params.items()))


def plan_id(row: Mapping[str, Any]) -> str:
    return str((row.get("three_level_fingerprint") or {}).get("plan_level") or "")


def score_key(row: Mapping[str, Any]) -> Tuple[float, int]:
    # Deliberately read no QPS, acceptance labels or outer resource observations.
    score = float(row["candidate_score"])
    if not math.isfinite(score):
        raise ValueError("non-finite portfolio resource score")
    return score, -int(row.get("canonical_subset_order") or 0)


def radical_inverse(index: int, base: int) -> float:
    result, weight = 0.0, 1.0 / base
    while index:
        index, digit = divmod(index, base)
        result += digit * weight
        weight /= base
    return result


class ParameterBox:
    def __init__(self, bounds: Mapping[str, Tuple[float, float]], log_params: Sequence[str]) -> None:
        self.names = tuple(bounds)
        self.logs = set(log_params)
        self.bounds = {}
        if not 0 < len(self.names) <= len(PRIMES):
            raise ValueError("portfolio supports one to seven parameters")
        for name, (low, high) in bounds.items():
            if not all(math.isfinite(float(v)) for v in (low, high)) or low > high:
                raise ValueError(f"invalid parameter bounds: {name}")
            if name in self.logs and low <= 0:
                raise ValueError(f"logarithmic parameter must be positive: {name}")
            self.bounds[name] = (float(low), float(high))

    def encode(self, params: Mapping[str, float]) -> Dict[str, float]:
        result = {}
        for name, (low, high) in self.bounds.items():
            value = float(params[name])
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"parameter outside caller interval: {name}")
            if name in self.logs:
                low, high, value = math.log(low), math.log(high), math.log(value)
            result[name] = (value - low) / (high - low) if high > low else 0.0
        return result

    def decode(self, coordinates: Mapping[str, float]) -> Dict[str, float]:
        result = {}
        for name, (low, high) in self.bounds.items():
            coordinate = min(1.0, max(0.0, float(coordinates[name])))
            if name in self.logs:
                value = math.exp(math.log(low) + coordinate * (math.log(high) - math.log(low)))
            else:
                value = low + coordinate * (high - low)
            result[name] = min(high, max(low, value))
        return result

    def distance(self, left: Mapping[str, float], right: Mapping[str, float]) -> float:
        a, b = self.encode(left), self.encode(right)
        return sum((a[name] - b[name]) ** 2 for name in self.names)


def diverse_order(
    rows: Sequence[Dict[str, Any]],
    *,
    seen_plans: Sequence[str] = (),
    target_abstained: bool = False,
) -> List[Dict[str, Any]]:
    """Allocate each five-slot block to resource score, plans and coverage.

    Input rows must already satisfy caller admission gates. Exact identities
    are deduplicated, not physical-plan structures. Missing diversity slots
    fall back to the resource ordering without fabricating candidates. An
    abstained target uses 1/2/2 slots instead of the ordinary 3/1/1 mix.
    """
    remaining, identities = [], set()
    for row in sorted(rows, key=score_key, reverse=True):
        key = identity(row["optimized_params"])
        if key not in identities:
            identities.add(key)
            remaining.append(row)
    selected, known = [], set(seen_plans)
    resource_slots, plan_slots, coverage_slots = (1, 2, 2) if target_abstained else (3, 1, 1)
    while remaining:
        block = remaining[:resource_slots]
        del remaining[:resource_slots]
        selected.extend(block)
        known.update(plan_id(row) for row in block)
        for _ in range(plan_slots):
            if not remaining:
                break
            selected_plans = {plan_id(row) for row in selected}
            different = next((i for i, row in enumerate(remaining)
                              if plan_id(row) and plan_id(row) not in selected_plans), 0)
            novel_index = next((i for i, row in enumerate(remaining)
                                if plan_id(row) and plan_id(row) not in known), different)
            chosen = remaining.pop(novel_index)
            selected.append(chosen)
            known.add(plan_id(chosen))
        for _ in range(coverage_slots):
            if not remaining:
                break
            sparse_index = max(range(len(remaining)), key=lambda i: (
                float(remaining[i].get("portfolio_coverage_distance") or 0.0), score_key(remaining[i]),
            ))
            chosen = remaining.pop(sparse_index)
            selected.append(chosen)
            known.add(plan_id(chosen))
    return selected


def plan_representatives(rows: Sequence[Dict[str, Any]], box: ParameterBox) -> List[Dict[str, Any]]:
    """Retain quality and numeric spread in each plan; keep a real Top-10 pool."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if not plan_id(row):
            raise ValueError("portfolio candidate lacks a full-workload plan fingerprint")
        groups.setdefault(plan_id(row), []).append(row)
    quota = max(3, math.ceil(10 / max(1, len(groups))))
    result = []
    for values in groups.values():
        remaining = sorted(values, key=score_key, reverse=True)
        chosen = remaining[:2]
        del remaining[:2]
        while remaining and len(chosen) < quota:
            index = max(range(len(remaining)), key=lambda i: (
                min(box.distance(remaining[i]["optimized_params"], row["optimized_params"]) for row in chosen),
                score_key(remaining[i]),
            ))
            chosen.append(remaining.pop(index))
        result.extend(chosen)
    return sorted(result, key=score_key, reverse=True)


def search(
    *,
    bounds: Mapping[str, Tuple[float, float]],
    log_params: Sequence[str],
    incumbent: Mapping[str, float],
    directions: Mapping[str, float],
    evaluate: Callable[[Dict[str, float]], Optional[Dict[str, Any]]],
    policy: PortfolioPolicy,
    first_miss: bool = False,
    cursor: int = 1,
    seen_plans: Sequence[str] = (),
    prior_params: Sequence[Mapping[str, float]] = (),
    refinement_direction: Optional[Callable[[Dict[str, Any]], Mapping[str, float]]] = None,
    target_abstained: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Run three offline stages with an invocation-local exact-point cache.

    Cache scope is one frozen workload/model/incumbent snapshot only; no
    estimate is reused across rounds, database restores or statistics changes.
    """
    started = time.perf_counter()
    box = ParameterBox(bounds, log_params)
    center = box.encode(incumbent)
    for name in box.names:
        if not math.isfinite(float(directions.get(name, 0.0))):
            raise ValueError(f"non-finite experience direction: {name}")
    local_n, global_n, refine_n = (
        (policy.miss_local_points, policy.miss_global_points, policy.miss_refinement_points)
        if first_miss or target_abstained
        else (policy.local_points, policy.global_points, policy.refinement_points)
    )
    cursor = max(1, int(cursor))
    evaluated = {identity(incumbent)}
    candidates, errors, stage_audits = [], [], []
    duplicate_count = 0
    prior = [dict(incumbent)]
    for params in prior_params:
        try:
            box.encode(params)
            prior.append(dict(params))
        except (ValueError, KeyError, TypeError):
            continue
    # Rotate only tie-breaking, using C's persisted exploration cursor. Flat
    # gradients must not permanently favor the first parameters in the file.
    offset = (cursor - 1) % len(box.names)
    rotated_names = box.names[offset:] + box.names[:offset]
    priorities = sorted(rotated_names, key=lambda name: -abs(float(directions.get(name, 0.0))))

    def low_discrepancy() -> Dict[str, float]:
        nonlocal cursor
        coordinates = {name: radical_inverse(cursor, PRIMES[i]) for i, name in enumerate(box.names)}
        cursor += 1
        return box.decode(coordinates)

    def moved(anchor, names, sign, radius, local_directions=None):
        coordinates = box.encode(anchor)
        for name in names:
            direction = float((local_directions if local_directions is not None else directions).get(name, 0.0))
            coordinates[name] += sign * (1.0 if direction >= 0 else -1.0) * radius
        return box.decode(coordinates)

    local = []
    for sign in (1, -1):
        local.append(moved(incumbent, priorities[:3], sign, 0.10))
    for name in priorities:
        local.extend(moved(incumbent, [name], sign, 0.10) for sign in (1, -1))
    global_vectors = []
    for name in box.names:
        for endpoint in (0.0, 1.0):
            coordinates = dict(center)
            coordinates[name] = endpoint
            global_vectors.append(box.decode(coordinates))
    global_vectors.extend(low_discrepancy() for _ in range(max(0, global_n - len(global_vectors))))

    with ThreadPoolExecutor(max_workers=policy.max_workers) as executor:
        def stage(label, vectors, count):
            nonlocal duplicate_count
            tasks = []
            for params in vectors:
                if len(tasks) >= count:
                    break
                key = identity(params)
                if key in evaluated:
                    duplicate_count += 1
                    continue
                box.encode(params)
                evaluated.add(key)
                tasks.append(params)
            # Fill collisions from a deterministic stream, never by replaying
            # duplicates. A collapsed box legitimately yields fewer points.
            for _ in range(count * 4):
                if len(tasks) >= count:
                    break
                params = low_discrepancy()
                key = identity(params)
                if key in evaluated:
                    duplicate_count += 1
                    continue
                evaluated.add(key)
                tasks.append(params)
            futures = [executor.submit(evaluate, copy.deepcopy(params)) for params in tasks]
            valid = 0
            for params, future in zip(tasks, futures):
                try:
                    row = future.result()
                    if row is None:
                        continue
                    row = copy.deepcopy(row)
                    if identity(row["optimized_params"]) != identity(params):
                        raise ValueError("estimator returned a different parameter identity")
                    score_key(row)
                    if not plan_id(row):
                        raise ValueError("incomplete full-workload plan fingerprint")
                    row.update({"portfolio_contract": CONTRACT, "portfolio_stage": label,
                                "portfolio_coverage_distance": min(box.distance(params, point) for point in prior),
                                "canonical_subset_order": len(candidates) + 1})
                    prior.append(params)
                    candidates.append(row)
                    valid += 1
                except Exception as exc:
                    errors.append({"stage": label, "params": params, "error": str(exc)})
            stage_audits.append({"stage": label, "requested": count, "evaluated": len(tasks), "valid": valid})

        stage("local", local, local_n)
        stage("global", global_vectors, global_n)
        reps = plan_representatives(candidates, box)
        # Start with one seed per plan, then numeric alternatives. No QPS used.
        seeds, seed_plans = [], set()
        for row in reps:
            if plan_id(row) not in seed_plans:
                seeds.append(row)
                seed_plans.add(plan_id(row))
        seeds.extend(row for row in reps if row not in seeds)
        refinement = []
        for seed_index, row in enumerate(seeds[:4]):
            anchor = row["optimized_params"]
            seed_directions = dict(refinement_direction(row)) if refinement_direction else dict(directions)
            if not all(math.isfinite(float(seed_directions.get(name, 0.0))) for name in box.names):
                raise ValueError("non-finite refinement direction")
            seed_offset = (offset + 2 * seed_index) % len(box.names)
            seed_names = box.names[seed_offset:] + box.names[:seed_offset]
            seed_priorities = sorted(seed_names, key=lambda name: -abs(float(seed_directions.get(name, 0.0))))
            for sign in (1, -1):
                refinement.append(moved(anchor, seed_priorities[:2], sign, 0.025, seed_directions))
            other = next((item for item in seeds if plan_id(item) != plan_id(row)), None)
            if other is not None:
                left, right = box.encode(anchor), box.encode(other["optimized_params"])
                refinement.append(box.decode({name: (left[name] + right[name]) / 2 for name in box.names}))
            refinement.append(moved(anchor, seed_priorities[:1], 1, 0.05, seed_directions))
        stage("refinement", refinement, refine_n)

    retained = plan_representatives(candidates, box)
    # Select sparse-region representative against the whole generated pool,
    # not processing order, so the first submitted point has no advantage.
    for row in retained:
        others = [point for point in prior if identity(point) != identity(row["optimized_params"])]
        row["portfolio_coverage_distance"] = min(
            (box.distance(row["optimized_params"], point) for point in others), default=0.0,
        )
    ordered = diverse_order(retained, seen_plans=seen_plans, target_abstained=target_abstained)
    for rank, row in enumerate(ordered, start=1):
        row["rank"] = rank
    retained_ids = {identity(row["optimized_params"]) for row in ordered}
    return ordered, {
        "contract": CONTRACT, "policy": asdict(policy), "first_miss": first_miss,
        "target_prediction_abstained": target_abstained,
        "selection_slots": {"resource": 1, "plan": 2, "coverage": 2} if target_abstained
        else {"resource": 3, "plan": 1, "coverage": 1},
        "coordinate_tie_break_offset": offset,
        "stages": stage_audits, "offline_evaluations": sum(row["evaluated"] for row in stage_audits),
        "duplicate_evaluations_avoided": duplicate_count, "valid_candidates": len(candidates),
        "distinct_plans": len({plan_id(row) for row in candidates}), "retained_candidates": len(ordered),
        "errors": errors, "next_cursor": cursor, "wall_s": time.perf_counter() - started,
        "cache_scope": "single_inner_call_frozen_context", "qps_used": False,
        "ranges_mutated": False, "physical_replays": 0,
        "evaluated_candidates": [
            {"params": row["optimized_params"], "stage": row["portfolio_stage"],
             "resource_score": row["candidate_score"], "plan_id": plan_id(row),
             "retained": identity(row["optimized_params"]) in retained_ids}
            for row in candidates
        ],
    }
