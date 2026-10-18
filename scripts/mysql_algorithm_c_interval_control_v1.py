"""Pure interval and batch checks for the September Algorithm C successor."""

from __future__ import annotations

import copy
import math
from typing import Any, Mapping, Sequence


POLICY_ID = "mysql_algorithm_c_actual_interval_two_valid_misses_v1"


def select_expanded_top5(
    *,
    coverage_rankings: Sequence[Mapping[str, Any]],
    local_rankings: Sequence[Mapping[str, Any]],
    incumbent_params: Mapping[str, Any],
    replayed_signatures: set[str],
    accepted_signatures: set[str],
    policy: Any,
    optimizer_params_key: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Use C-only broad coverage after miss one; fill with fresh local points."""
    pool: list[dict[str, Any]] = []
    excluded_replayed = 0
    for source, rows in (("current_interval_coverage", coverage_rankings),
                         ("trusted_gradient", local_rankings)):
        for raw in rows:
            row = copy.deepcopy(dict(raw))
            signature = optimizer_params_key(row["optimized_params"])
            if signature in replayed_signatures:
                excluded_replayed += 1
                continue
            row["algorithm_c_first_miss_source"] = source
            pool.append(row)
    admitted, audit = policy.admit_partial_topk(
        pool,
        incumbent_params=incumbent_params,
        previously_accepted_signatures=accepted_signatures,
    )
    for rank, row in enumerate(admitted, 1):
        row["rank"] = rank
    return admitted, {
        "contract": POLICY_ID,
        "trigger": "one_complete_valid_rejected_batch",
        "coverage_generated": len(coverage_rankings),
        "local_generated": len(local_rankings),
        "excluded_previously_replayed": excluded_replayed,
        "admission": audit,
        "qps_used_for_generation_or_ranking": False,
        "selected_count": len(admitted),
    }


def require_complete_batch(
    *, selected_count: int, rows: Sequence[Mapping[str, Any]],
    technical_exclusions: Sequence[Mapping[str, Any]],
    physical_valid: Any,
) -> None:
    """Only a complete, valid Top-5 may grant acceptance or one miss."""
    if technical_exclusions or len(rows) != selected_count or any(
        not physical_valid(row) for row in rows
    ):
        raise RuntimeError("Algorithm C candidate batch incomplete or technically invalid")


def require_binary_progress(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
    active_params: Sequence[str],
) -> dict[str, Any]:
    """Reject fake shrinkage, expanded intervals, and nonfinite bounds."""
    changed: list[str] = []
    widths: dict[str, dict[str, float]] = {}
    for name in active_params:
        old = before[name]
        new = after[name]
        lo, hi = float(old["lower"]), float(old["upper"])
        new_lo, new_hi = float(new["lower"]), float(new["upper"])
        if not all(math.isfinite(value) and value > 0 for value in
                   (lo, hi, new_lo, new_hi)) or lo > hi or new_lo > new_hi:
            raise RuntimeError(f"invalid Algorithm C interval: {name}")
        tolerance = 1e-12 * max(1.0, abs(lo), abs(hi))
        if new_lo < lo - tolerance or new_hi > hi + tolerance:
            raise RuntimeError(f"Algorithm C interval expanded during shrink: {name}")
        old_width = math.log(hi) - math.log(lo)
        new_width = math.log(new_hi) - math.log(new_lo)
        widths[name] = {"before": old_width, "after": new_width}
        if new_width < old_width - 1e-12:
            changed.append(name)
    if not changed:
        raise RuntimeError("Algorithm C shrink made no actual interval progress")
    return {"contract": POLICY_ID, "strictly_shrunk": changed, "log_widths": widths}


def retained_complement_regions(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
    active_params: Sequence[str],
    *, resource_potential: float = 0.0,
) -> list[dict[str, Any]]:
    """Save the slabs excluded by an accepted subset shrink for later visits."""
    regions: list[dict[str, Any]] = []
    for name in active_params:
        old_lo = float(before[name]["lower"])
        old_hi = float(before[name]["upper"])
        new_lo = float(after[name]["lower"])
        new_hi = float(after[name]["upper"])
        for side, lo, hi in (("lower", old_lo, new_lo),
                             ("upper", new_hi, old_hi)):
            if hi <= lo or math.log(hi / lo) < 1e-12:
                continue
            box = copy.deepcopy(dict(before))
            box[name] = copy.deepcopy(dict(before[name]))
            box[name]["lower"] = lo
            box[name]["upper"] = hi
            regions.append({
                "parameter": name, "side": side, "ranges": box,
                "resource_potential": float(resource_potential),
                "log_span": math.log(hi / lo),
            })
    return regions


def take_retained_region(
    queue: Sequence[Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
    initial: Mapping[str, Mapping[str, Any]],
    incumbent: Mapping[str, Any],
    active_params: Sequence[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    """Open one promising retained box and keep every bound in the initial domain."""
    if not queue:
        return copy.deepcopy(dict(current)), [], None
    ordered = sorted(
        enumerate(queue),
        key=lambda item: (
            -float(item[1].get("resource_potential") or 0.0),
            -float(item[1].get("log_span") or 0.0), item[0],
        ),
    )
    index, selected = ordered[0]
    selected_ranges = selected["ranges"]
    expanded = copy.deepcopy(dict(current))
    for name in active_params:
        low = max(
            float(initial[name]["lower"]),
            min(float(current[name]["lower"]), float(selected_ranges[name]["lower"]),
                float(incumbent[name])),
        )
        high = min(
            float(initial[name]["upper"]),
            max(float(current[name]["upper"]), float(selected_ranges[name]["upper"]),
                float(incumbent[name])),
        )
        if low > high:
            raise RuntimeError(f"retained Algorithm C region has invalid bounds: {name}")
        expanded[name] = copy.deepcopy(dict(current[name]))
        expanded[name]["lower"] = low
        expanded[name]["upper"] = high
    remaining = [copy.deepcopy(dict(row)) for position, row in enumerate(queue)
                 if position != index]
    return expanded, remaining, copy.deepcopy(dict(selected))
