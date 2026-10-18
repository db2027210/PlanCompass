"""Deterministic, benchmark-free candidate generation for plan-basin escape."""

from __future__ import annotations

from dataclasses import dataclass, replace
import copy
import hashlib
import json
import math
import random
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


ESCAPE_BATCH_NAMES = (
    "local",
    "radius_2x",
    "endpoints",
    "reverse_orthogonal",
    "stratified_boundary",
)
MISSING_EXACT_FINGERPRINT = "__missing_exact_plan_fingerprint__"


@dataclass(frozen=True)
class PlanBasinEscapeConfig:
    enabled: bool = False
    min_distinct_plans: int = 2
    radius_multiplier: float = 2.0
    stratified_probe_count: int = 16
    boundary_fraction: float = 0.2
    seed: int = 1729


@dataclass(frozen=True)
class PlanBasinEscapeState:
    batch_index: int = 0
    accepted_shrinks: int = 0
    status: str = "local"
    basin_exhausted: bool = False

    @property
    def batch_name(self) -> str:
        return ESCAPE_BATCH_NAMES[min(max(self.batch_index, 0), 4)]


def state_as_dict(state: PlanBasinEscapeState) -> Dict[str, Any]:
    return {
        "batch_index": state.batch_index,
        "batch_name": state.batch_name,
        "accepted_shrinks": state.accepted_shrinks,
        "status": state.status,
        "basin_exhausted": state.basin_exhausted,
    }


def state_from_dict(raw: Mapping[str, Any]) -> PlanBasinEscapeState:
    if not isinstance(raw, Mapping):
        raise ValueError("plan-basin escape state must be an object")
    try:
        batch_index = int(raw.get("batch_index", 0))
        accepted_shrinks = int(raw.get("accepted_shrinks", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("plan-basin escape state counters must be integers") from exc
    if not 0 <= batch_index < len(ESCAPE_BATCH_NAMES):
        raise ValueError("plan-basin escape batch_index is out of range")
    if accepted_shrinks < 0:
        raise ValueError("plan-basin escape accepted_shrinks cannot be negative")
    basin_exhausted = raw.get("basin_exhausted", False)
    if not isinstance(basin_exhausted, bool):
        raise ValueError("plan-basin escape basin_exhausted must be boolean")
    status = str(raw.get("status") or ESCAPE_BATCH_NAMES[batch_index])
    if basin_exhausted and status != "basin_exhausted":
        raise ValueError("exhausted plan-basin state must use basin_exhausted status")
    return PlanBasinEscapeState(
        batch_index=batch_index,
        accepted_shrinks=accepted_shrinks,
        status=status,
        basin_exhausted=basin_exhausted,
    )


def parse_plan_basin_escape_config(
    raw: Optional[Mapping[str, Any]],
) -> PlanBasinEscapeConfig:
    """Parse optimizer.gradient.plan_basin_escape with fail-closed defaults."""
    if raw is None:
        return PlanBasinEscapeConfig()
    if not isinstance(raw, Mapping):
        raise ValueError("optimizer.gradient.plan_basin_escape must be an object")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("plan_basin_escape.enabled must be boolean")

    def _positive_int(name: str, default: int) -> int:
        value = raw.get(name, default)
        if isinstance(value, bool):
            raise ValueError(f"plan_basin_escape.{name} must be a positive integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"plan_basin_escape.{name} must be a positive integer"
            ) from exc
        if parsed <= 0:
            raise ValueError(f"plan_basin_escape.{name} must be a positive integer")
        return parsed

    min_distinct_plans = _positive_int("min_distinct_plans", 2)
    stratified_probe_count = _positive_int("stratified_probe_count", 16)
    radius_multiplier = float(raw.get("radius_multiplier", 2.0))
    if not math.isfinite(radius_multiplier) or radius_multiplier <= 1.0:
        raise ValueError("plan_basin_escape.radius_multiplier must be finite and > 1")
    boundary_fraction = float(raw.get("boundary_fraction", 0.2))
    if not math.isfinite(boundary_fraction) or not 0.0 < boundary_fraction <= 0.5:
        raise ValueError(
            "plan_basin_escape.boundary_fraction must be in (0, 0.5]"
        )
    seed = raw.get("seed", 1729)
    if isinstance(seed, bool):
        raise ValueError("plan_basin_escape.seed must be an integer")
    try:
        seed = int(seed)
    except (TypeError, ValueError) as exc:
        raise ValueError("plan_basin_escape.seed must be an integer") from exc
    return PlanBasinEscapeConfig(
        enabled=enabled,
        min_distinct_plans=min_distinct_plans,
        radius_multiplier=radius_multiplier,
        stratified_probe_count=stratified_probe_count,
        boundary_fraction=boundary_fraction,
        seed=seed,
    )


def config_as_dict(config: PlanBasinEscapeConfig) -> Dict[str, Any]:
    return {
        "enabled": config.enabled,
        "min_distinct_plans": config.min_distinct_plans,
        "radius_multiplier": config.radius_multiplier,
        "stratified_probe_count": config.stratified_probe_count,
        "boundary_fraction": config.boundary_fraction,
        "seed": config.seed,
    }


def _range_spec(raw: Any, name: str) -> Tuple[float, float, str]:
    if isinstance(raw, Mapping):
        lower = raw.get("min_value")
        upper = raw.get("max_value")
    else:
        lower = getattr(raw, "min_value", None)
        upper = getattr(raw, "max_value", None)
    try:
        lower_value = float(lower)
        upper_value = float(upper)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid range for {name}") from exc
    if (
        not math.isfinite(lower_value)
        or not math.isfinite(upper_value)
        or lower_value < 0.0
        or lower_value >= upper_value
    ):
        raise ValueError(
            f"range for {name} must satisfy 0 <= min_value < max_value"
        )
    # Planner cost parameters with a strictly positive lower bound use log
    # coordinates.  A legal zero lower bound (parallel_setup_cost) cannot be
    # logged and therefore uses a linear coordinate.  Both are normalized to
    # z in [0, 1] before basin geometry is applied.
    transform = "log" if lower_value > 0.0 else "linear"
    return lower_value, upper_value, transform


def _candidate_params(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    params = candidate.get("optimized_params")
    if not isinstance(params, Mapping):
        params = candidate.get("params")
    if not isinstance(params, Mapping):
        raise ValueError("candidate must contain optimized_params or params")
    return params


def _signature(params: Mapping[str, Any], active_params: Sequence[str]) -> str:
    payload = {name: float(params[name]).hex() for name in active_params}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _project_normalized_vector(
    values: Sequence[float],
) -> List[float]:
    return [
        min(max(float(value), 0.0), 1.0)
        for value in values
    ]


def _value_to_normalized(
    value: float,
    lower: float,
    upper: float,
    transform: str,
) -> float:
    if transform == "log":
        if value <= 0.0:
            raise ValueError("log-coordinate value must be positive")
        return (math.log(value) - math.log(lower)) / (
            math.log(upper) - math.log(lower)
        )
    return (value - lower) / (upper - lower)


def _normalized_to_value(
    normalized: float,
    lower: float,
    upper: float,
    transform: str,
) -> float:
    z = min(max(float(normalized), 0.0), 1.0)
    if z <= 0.0:
        return lower
    if z >= 1.0:
        return upper
    if transform == "log":
        return math.exp(math.log(lower) + z * (math.log(upper) - math.log(lower)))
    return lower + z * (upper - lower)


def _params_from_normalized_vector(
    incumbent: Mapping[str, Any],
    active_params: Sequence[str],
    normalized_values: Sequence[float],
    range_specs: Sequence[Tuple[float, float, str]],
) -> Dict[str, float]:
    params = {name: float(value) for name, value in incumbent.items()}
    for name, normalized, (lower, upper, transform) in zip(
        active_params, normalized_values, range_specs
    ):
        params[name] = _normalized_to_value(
            normalized, lower, upper, transform
        )
    return params


def _hadamard(order: int) -> List[List[int]]:
    matrix = [[1]]
    while len(matrix) < order:
        matrix = [row + row for row in matrix] + [
            row + [-value for value in row] for row in matrix
        ]
    return matrix


def build_escape_batches(
    *,
    incumbent_params: Mapping[str, Any],
    local_candidates: Sequence[Mapping[str, Any]],
    param_ranges: Mapping[str, Any],
    active_params: Sequence[str],
    seen_candidates: Iterable[Mapping[str, Any]] = (),
    config: Optional[PlanBasinEscapeConfig] = None,
) -> List[List[Dict[str, Any]]]:
    """Build five deterministic batches without executing or scoring them."""
    cfg = config or PlanBasinEscapeConfig()
    if not cfg.enabled:
        return [copy.deepcopy(list(local_candidates))]
    names = tuple(str(name) for name in active_params)
    if not names or len(set(names)) != len(names):
        raise ValueError("active_params must be non-empty and unique")
    for name in names:
        if name not in incumbent_params or name not in param_ranges:
            raise ValueError(f"missing incumbent or range for {name}")
    range_specs = [_range_spec(param_ranges[name], name) for name in names]
    incumbent_normalized = _project_normalized_vector(
        [
            _value_to_normalized(
                float(incumbent_params[name]), lower, upper, transform
            )
            for name, (lower, upper, transform) in zip(names, range_specs)
        ],
    )
    incumbent_projected = _params_from_normalized_vector(
        incumbent_params, names, incumbent_normalized, range_specs
    )

    def _project_params(params: Mapping[str, Any]) -> Dict[str, float]:
        normalized = _project_normalized_vector(
            [
                _value_to_normalized(
                    float(params[name]), lower, upper, transform
                )
                for name, (lower, upper, transform) in zip(names, range_specs)
            ]
        )
        return _params_from_normalized_vector(
            incumbent_params, names, normalized, range_specs
        )

    excluded = {_signature(incumbent_projected, names)}
    for candidate in seen_candidates:
        try:
            excluded.add(
                _signature(_project_params(_candidate_params(candidate)), names)
            )
        except (KeyError, TypeError, ValueError):
            continue
    emitted = set(excluded)
    batches: List[List[Dict[str, Any]]] = [[] for _ in ESCAPE_BATCH_NAMES]

    def _append(
        batch_index: int,
        normalized_values: Sequence[float],
        *,
        source: str,
        source_candidate: Optional[Mapping[str, Any]] = None,
        detail: Optional[Mapping[str, Any]] = None,
    ) -> None:
        projected = _project_normalized_vector(normalized_values)
        params = _params_from_normalized_vector(
            incumbent_params, names, projected, range_specs
        )
        signature = _signature(params, names)
        if signature in emitted:
            return
        emitted.add(signature)
        if source_candidate is not None and batch_index == 0:
            record = copy.deepcopy(dict(source_candidate))
            target_key = (
                "optimized_params" if "optimized_params" in record else "params"
            )
            record[target_key] = params
        else:
            record = {"optimized_params": params}
        record["plan_basin_escape"] = {
            "batch_index": batch_index,
            "batch_name": ESCAPE_BATCH_NAMES[batch_index],
            "source": source,
            "coordinate_space": "normalized_log_linear",
            "parameter_transforms": {
                name: transform
                for name, (_, _, transform) in zip(names, range_specs)
            },
            "parameter_signature_sha256": signature,
            **copy.deepcopy(dict(detail or {})),
        }
        batches[batch_index].append(record)

    local_normalized_vectors: List[List[float]] = []
    for candidate in local_candidates:
        params = _candidate_params(candidate)
        try:
            vector = [
                _value_to_normalized(
                    float(params[name]), lower, upper, transform
                )
                for name, (lower, upper, transform) in zip(names, range_specs)
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("local candidate has invalid active parameter") from exc
        projected = _project_normalized_vector(vector)
        local_normalized_vectors.append(projected)
        _append(
            0,
            projected,
            source="original_local_candidate",
            source_candidate=candidate,
        )
    for local_index, vector in enumerate(local_normalized_vectors):
        expanded = [
            center + cfg.radius_multiplier * (value - center)
            for center, value in zip(incumbent_normalized, vector)
        ]
        _append(
            1,
            expanded,
            source="local_direction_radius_expansion",
            detail={
                "local_candidate_index": local_index,
                "radius_multiplier": cfg.radius_multiplier,
            },
        )
    for dim, name in enumerate(names):
        for endpoint_name, endpoint_value in (("low", 0.0), ("high", 1.0)):
            vector = list(incumbent_normalized)
            vector[dim] = endpoint_value
            _append(
                2,
                vector,
                source="legal_endpoint",
                detail={"parameter": name, "endpoint": endpoint_name},
            )
    for local_index, vector in enumerate(local_normalized_vectors):
        reversed_vector = [
            center - (value - center)
            for center, value in zip(incumbent_normalized, vector)
        ]
        _append(
            3,
            reversed_vector,
            source="reverse_local_direction",
            detail={"local_candidate_index": local_index},
        )
    radii: List[float] = []
    for dim in range(len(names)):
        local_radius = max(
            (
                abs(vector[dim] - incumbent_normalized[dim])
                for vector in local_normalized_vectors
            ),
            default=0.0,
        )
        span = 1.0
        radii.append(min(max(local_radius, span / 8.0), span / 2.0))
    order = 1
    while order < len(names):
        order *= 2
    for row_index, row in enumerate(_hadamard(order)):
        for sign in (1, -1):
            vector = [
                incumbent_normalized[dim] + sign * row[dim] * radii[dim]
                for dim in range(len(names))
            ]
            _append(
                3,
                vector,
                source="hadamard_orthogonal_direction",
                detail={"hadamard_row": row_index, "sign": sign},
            )
    probe_count = max(cfg.stratified_probe_count, 2 * len(names))
    rng = random.Random(cfg.seed)
    strata_by_dim: List[List[int]] = []
    for _ in names:
        strata = list(range(probe_count))
        rng.shuffle(strata)
        strata_by_dim.append(strata)
    for probe_index in range(probe_count):
        vector = []
        sides: List[str] = []
        for dim in range(len(names)):
            lower, upper, span = 0.0, 1.0, 1.0
            fraction = (
                (strata_by_dim[dim][probe_index] + 0.5)
                / probe_count
                * cfg.boundary_fraction
            )
            low_side = (probe_index + dim) % 2 == 0
            vector.append(
                lower + span * fraction
                if low_side
                else upper - span * fraction
            )
            sides.append("low" if low_side else "high")
        _append(
            4,
            vector,
            source="seeded_stratified_boundary_probe",
            detail={
                "seed": cfg.seed,
                "probe_index": probe_index,
                "boundary_sides": sides,
            },
        )
    return batches


def fingerprint_cluster_id(candidate: Mapping[str, Any]) -> str:
    """Return exact workload-plan cluster id or one shared missing bucket."""
    for field in ("workload_plan_fingerprint", "plan_fingerprint"):
        value = candidate.get(field)
        if value not in (None, "", [], {}):
            if isinstance(value, str):
                return value
            return json.dumps(value, sort_keys=True, separators=(",", ":"))
    metadata = candidate.get("final_estimation_metadata")
    if isinstance(metadata, Mapping):
        value = metadata.get("workload_plan_fingerprint")
        if value not in (None, ""):
            return str(value)
        value = metadata.get("plan_fingerprint")
        if value not in (None, ""):
            return str(value)
    similarity_is_exact = (
        candidate.get("plan_similarity_cluster_exact") is True
        or str(candidate.get("plan_similarity_cluster_kind") or "").lower()
        == "exact"
    )
    if similarity_is_exact:
        value = candidate.get("plan_similarity_cluster_id")
        if value not in (None, ""):
            return str(value)
    return MISSING_EXACT_FINGERPRINT


def distinct_fingerprint_count(
    candidates: Sequence[Mapping[str, Any]],
) -> int:
    return len(
        {
            cluster
            for cluster in (
                fingerprint_cluster_id(candidate)
                for candidate in candidates
            )
            if cluster != MISSING_EXACT_FINGERPRINT
        }
    )


def round_robin_fingerprint_clusters(
    candidates: Sequence[Mapping[str, Any]],
    *,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Round-robin exact-plan clusters while preserving within-cluster order."""
    if limit is not None and (isinstance(limit, bool) or int(limit) < 0):
        raise ValueError("limit must be a non-negative integer or None")
    groups: Dict[str, List[Mapping[str, Any]]] = {}
    cluster_order: List[str] = []
    missing: List[Mapping[str, Any]] = []
    for candidate in candidates:
        cluster = fingerprint_cluster_id(candidate)
        if cluster == MISSING_EXACT_FINGERPRINT:
            missing.append(candidate)
            continue
        if cluster not in groups:
            groups[cluster] = []
            cluster_order.append(cluster)
        groups[cluster].append(candidate)
    ordered: List[Dict[str, Any]] = []
    offset = 0
    while True:
        emitted_this_cycle = False
        for cluster in cluster_order:
            rows = groups[cluster]
            if offset >= len(rows):
                continue
            ordered.append(copy.deepcopy(dict(rows[offset])))
            emitted_this_cycle = True
        if not emitted_this_cycle:
            break
        offset += 1
    ordered.extend(copy.deepcopy(dict(candidate)) for candidate in missing)
    target = len(candidates) if limit is None else min(int(limit), len(candidates))
    return ordered[:target]


def evaluate_escape_batch(
    candidates: Sequence[Mapping[str, Any]],
    *,
    estimate_candidate: Callable[
        [Dict[str, float]], Tuple[Mapping[str, Any], Optional[Mapping[str, Any]], Mapping[str, Any]]
    ],
    score_candidate: Callable[[Dict[str, float]], float],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Estimate one batch and admit only candidates with exact plan evidence."""
    admitted: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for position, raw_candidate in enumerate(candidates, start=1):
        candidate = copy.deepcopy(dict(raw_candidate))
        try:
            params = {
                str(name): float(value)
                for name, value in _candidate_params(candidate).items()
            }
            resource_raw, stats_raw, metadata_raw = estimate_candidate(params)
            resource = {
                str(name): float(value) for name, value in resource_raw.items()
            }
            metadata = copy.deepcopy(dict(metadata_raw))
            fingerprint = fingerprint_cluster_id(
                {"final_estimation_metadata": metadata}
            )
            if fingerprint == MISSING_EXACT_FINGERPRINT:
                failures.append(
                    {
                        "position": position,
                        "reason": "missing_exact_plan_fingerprint",
                        "plan_basin_escape": copy.deepcopy(
                            candidate.get("plan_basin_escape") or {}
                        ),
                    }
                )
                continue
            final_score = float(score_candidate(resource))
            if not math.isfinite(final_score):
                raise ValueError("candidate score is non-finite")
        except Exception as exc:
            failures.append(
                {
                    "position": position,
                    "reason": "estimate_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "plan_basin_escape": copy.deepcopy(
                        candidate.get("plan_basin_escape") or {}
                    ),
                }
            )
            continue
        candidate.update(
            {
                "optimized_params": params,
                "final_resource": resource,
                "final_resource_stats": copy.deepcopy(
                    dict(stats_raw) if isinstance(stats_raw, Mapping) else stats_raw
                ),
                "final_score": final_score,
                "final_estimation_metadata": metadata,
                "workload_plan_fingerprint": fingerprint,
            }
        )
        admitted.append(candidate)

    admitted.sort(
        key=lambda item: (
            -float(item["final_score"]),
            str(
                (item.get("plan_basin_escape") or {}).get(
                    "parameter_signature_sha256", ""
                )
            ),
        )
    )
    for rank, candidate in enumerate(admitted, start=1):
        candidate["rank"] = rank
    return admitted, {
        "input_candidate_count": len(candidates),
        "admitted_candidate_count": len(admitted),
        "failed_closed_candidate_count": len(failures),
        "distinct_exact_plan_count": distinct_fingerprint_count(admitted),
        "missing_plan_policy": "fail_closed_no_cluster",
        "failures": failures,
    }


def should_enter_escape(
    *,
    candidate_space_exhausted: bool,
    distinct_plan_count: int,
    config: PlanBasinEscapeConfig,
) -> bool:
    if not config.enabled:
        return False
    return (
        bool(candidate_space_exhausted)
        or int(distinct_plan_count) < config.min_distinct_plans
    )


def advance_escape_state(
    state: PlanBasinEscapeState,
    *,
    outcome: str,
    config: PlanBasinEscapeConfig,
) -> PlanBasinEscapeState:
    """Advance after a real outcome; only accepted increments shrink count."""
    if not config.enabled:
        return state
    normalized = str(outcome).strip().lower()
    if normalized == "accepted":
        return PlanBasinEscapeState(
            batch_index=0,
            accepted_shrinks=state.accepted_shrinks + 1,
            status="accepted_shrink",
            basin_exhausted=False,
        )
    if normalized not in {
        "rejected",
        "single_plan",
        "candidate_space_exhausted",
    }:
        raise ValueError(f"unsupported plan-basin outcome: {outcome}")
    if state.batch_index >= len(ESCAPE_BATCH_NAMES) - 1:
        return replace(state, status="basin_exhausted", basin_exhausted=True)
    next_index = state.batch_index + 1
    return replace(
        state,
        batch_index=next_index,
        status=ESCAPE_BATCH_NAMES[next_index],
        basin_exhausted=False,
    )
