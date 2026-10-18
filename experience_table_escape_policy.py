"""Deterministic plan-basin escape candidates for experience-table search.

The normal all-subset optimizer is batch one.  This module generates at most
four additional parameter batches.  It does not evaluate or accept candidates;
the existing estimator, plan clustering, and formal replay gates retain those
responsibilities.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple


ParameterSignature = Tuple[Tuple[str, str], ...]


def exact_parameter_signature(params: Mapping[str, Any]) -> ParameterSignature:
    signature: List[Tuple[str, str]] = []
    for name in sorted(params):
        value = float(params[name])
        if not math.isfinite(value):
            raise ValueError(f"candidate parameter is not finite: {name}={value!r}")
        signature.append((str(name), value.hex()))
    return tuple(signature)


def _bounds(raw: Any) -> Tuple[float, float]:
    if isinstance(raw, Mapping):
        low = float(raw["min_value"])
        high = float(raw["max_value"])
    else:
        low = float(raw.min_value)
        high = float(raw.max_value)
    if not math.isfinite(low) or not math.isfinite(high) or low <= 0.0 or low > high:
        raise ValueError(f"invalid positive parameter range: [{low}, {high}]")
    return low, high


def _unit(value: float, low: float, high: float) -> float:
    if not math.isfinite(value) or value <= 0.0 or not low <= value <= high:
        raise ValueError(
            f"parameter value must be finite, positive, and in range: "
            f"value={value!r}, range=[{low}, {high}]"
        )
    if low == high:
        return 0.0
    return (math.log(value) - math.log(low)) / (math.log(high) - math.log(low))


def _value(unit: float, low: float, high: float) -> float:
    if low == high:
        return low
    bounded = min(1.0, max(0.0, unit))
    if bounded == 0.0:
        return low
    if bounded == 1.0:
        return high
    return math.exp(math.log(low) + bounded * (math.log(high) - math.log(low)))


def _record(
    *,
    params: Mapping[str, float],
    batch_index: int,
    origin: str,
    direction: Mapping[str, float],
    requested_radius: float,
    actual_radius: float,
    boundary_hits: Sequence[str],
) -> Dict[str, Any]:
    return {
        "optimized_params": copy.deepcopy(dict(params)),
        "escape_batch_index": batch_index,
        "candidate_origin": origin,
        "escape_direction": {
            str(name): float(value) for name, value in sorted(direction.items())
        },
        "escape_radius": float(actual_radius),
        "escape_requested_radius": float(requested_radius),
        "escape_boundary_hits": sorted(str(name) for name in boundary_hits),
    }


def generate_escape_batch(
    *,
    batch_index: int,
    incumbent: Mapping[str, float],
    parameter_ranges: Mapping[str, Any],
    sensitivity_table: Mapping[str, Mapping[str, Any]],
    target_resource: str,
    base_candidates: Sequence[Mapping[str, Any]],
    seen_signatures: Iterable[ParameterSignature] = (),
    radius: float = 0.08,
    maximum_candidates: int = 128,
) -> List[Dict[str, Any]]:
    """Generate one deterministic escape batch (indices 2 through 5)."""
    if batch_index not in {2, 3, 4, 5}:
        raise ValueError("escape batch_index must be one of 2, 3, 4, 5")
    if not math.isfinite(radius) or radius <= 0.0:
        raise ValueError("escape radius must be positive and finite")
    if maximum_candidates <= 0:
        raise ValueError("maximum_candidates must be positive")

    names = tuple(str(name) for name in incumbent)
    if not names or set(names) != set(parameter_ranges):
        raise ValueError("incumbent and parameter ranges must contain the same parameters")
    bounds = {name: _bounds(parameter_ranges[name]) for name in names}
    center = {
        name: _unit(float(incumbent[name]), *bounds[name]) for name in names
    }
    seen: Set[ParameterSignature] = set(seen_signatures)
    seen.add(exact_parameter_signature(incumbent))
    for candidate in base_candidates:
        raw_params = candidate.get("optimized_params") or {}
        if not isinstance(raw_params, Mapping) or set(raw_params) != set(names):
            continue
        for name in names:
            _unit(float(raw_params[name]), *bounds[name])
        seen.add(exact_parameter_signature(raw_params))
    output: List[Dict[str, Any]] = []

    def emit(units: Mapping[str, float], origin: str, direction: Mapping[str, float], used_radius: float) -> None:
        if len(output) >= maximum_candidates:
            return
        requested_units = {
            name: float(units.get(name, center[name])) for name in names
        }
        actual_units = {
            name: min(1.0, max(0.0, requested_units[name])) for name in names
        }
        params = {
            name: _value(actual_units[name], *bounds[name])
            for name in names
        }
        signature = exact_parameter_signature(params)
        if signature in seen:
            return
        seen.add(signature)
        output.append(
            _record(
                params=params,
                batch_index=batch_index,
                origin=origin,
                direction=direction,
                requested_radius=used_radius,
                actual_radius=max(
                    abs(actual_units[name] - center[name]) for name in names
                ),
                boundary_hits=[
                    name for name in names
                    if requested_units[name] != actual_units[name]
                ],
            )
        )

    if batch_index in {2, 3}:
        factor = -1.0 if batch_index == 2 else 2.0
        origin = "reverse_local_delta" if batch_index == 2 else "expanded_local_delta"
        for candidate in base_candidates:
            raw_params = candidate.get("optimized_params") or {}
            if not isinstance(raw_params, Mapping) or set(raw_params) != set(names):
                continue
            delta = {
                name: _unit(float(raw_params[name]), *bounds[name]) - center[name]
                for name in names
            }
            units = {
                name: center[name] + factor * delta[name]
                for name in names
            }
            emit(units, origin, {name: factor * delta[name] for name in names}, radius * abs(factor))
        return output

    priority = sorted(
        names,
        key=lambda name: (
            -abs(float((sensitivity_table.get(name) or {}).get(target_resource, 0.0) or 0.0)),
            name,
        ),
    )
    if batch_index == 4:
        for name in priority:
            for endpoint in (0.0, 1.0):
                units = dict(center)
                units[name] = endpoint
                emit(
                    units,
                    "coordinate_endpoint",
                    {name: endpoint - center[name]},
                    abs(endpoint - center[name]),
                )
        return output

    for name in priority:
        preferred = -1.0 if float(
            (sensitivity_table.get(name) or {}).get(target_resource, 0.0) or 0.0
        ) > 0.0 else 1.0
        for direction_value in (preferred, -preferred):
            units = dict(center)
            units[name] = center[name] + direction_value * radius
            emit(
                units,
                "orthogonal_coordinate",
                {name: direction_value},
                radius,
            )
    return output


__all__ = ["exact_parameter_signature", "generate_escape_batch"]
