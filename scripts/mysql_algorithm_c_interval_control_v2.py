"""PG-aligned interval regions for MySQL Algorithm C.

This successor preserves the v1 candidate and batch checks while making the
retained-region queue match PostgreSQL Algorithm C: complements form a
non-overlapping partition, identities are stable, queued/visited regions are
not admitted again, and first-miss selection uses resource potential followed
by creation order.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from scripts.mysql_algorithm_c_interval_control_v1 import (
    POLICY_ID as V1_POLICY_ID,
    require_binary_progress,
    require_complete_batch,
    select_expanded_top5,
)


POLICY_ID = "mysql_algorithm_c_pg_interval_regions_v2"


def region_identity(ranges: Mapping[str, Mapping[str, Any]]) -> str:
    canonical = {
        str(name): {
            "lower": float(specification["lower"]),
            "upper": float(specification["upper"]),
        }
        for name, specification in sorted(ranges.items())
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]


def retained_complement_regions(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
    active_params: Sequence[str],
    *,
    resource_potential: float = 0.0,
    created_outer_attempt: int = 0,
) -> list[dict[str, Any]]:
    """Partition ``before - after`` into PG-equivalent non-overlapping slabs."""
    if set(before) != set(after):
        raise ValueError("region dimensions differ")
    remainder = copy.deepcopy(dict(before))
    regions: list[dict[str, Any]] = []
    for name in sorted(active_params):
        if name not in before or name not in after:
            raise ValueError(f"missing active interval: {name}")
        old_lo = float(before[name]["lower"])
        old_hi = float(before[name]["upper"])
        new_lo = float(after[name]["lower"])
        new_hi = float(after[name]["upper"])
        if not all(
            math.isfinite(value) and value > 0.0
            for value in (old_lo, old_hi, new_lo, new_hi)
        ):
            raise ValueError(f"non-finite or non-positive interval: {name}")
        if not old_lo <= new_lo <= new_hi <= old_hi:
            raise ValueError(f"retained region is not a subset: {name}")
        for side, lower, upper in (
            ("lower", old_lo, new_lo),
            ("upper", new_hi, old_hi),
        ):
            if upper <= lower or math.log(upper / lower) < 1e-12:
                continue
            box = copy.deepcopy(remainder)
            box[name] = copy.deepcopy(dict(before[name]))
            box[name]["lower"] = lower
            box[name]["upper"] = upper
            regions.append({
                "id": region_identity(box),
                "parameter": name,
                "side": side,
                "ranges": box,
                "resource_potential": float(resource_potential),
                "log_span": math.log(upper / lower),
                "created_outer_attempt": int(created_outer_attempt),
            })
        remainder[name] = copy.deepcopy(dict(after[name]))
    return regions


def append_new_regions(
    queue: Sequence[Mapping[str, Any]],
    visited_region_ids: Sequence[str],
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Append only region identities absent from both queue and history."""
    result = [copy.deepcopy(dict(row)) for row in queue]
    known = {str(row.get("id") or "") for row in result}
    known.update(str(value) for value in visited_region_ids)
    added: list[str] = []
    skipped: list[str] = []
    for raw in candidates:
        row = copy.deepcopy(dict(raw))
        identity = str(row.get("id") or "")
        ranges = row.get("ranges")
        if not identity and isinstance(ranges, Mapping):
            identity = region_identity(ranges)
            row["id"] = identity
        if not identity:
            raise ValueError("retained region identity required")
        if identity in known:
            skipped.append(identity)
            continue
        known.add(identity)
        added.append(identity)
        result.append(row)
    return result, {
        "contract": POLICY_ID,
        "source_contract": V1_POLICY_ID,
        "added_region_ids": added,
        "duplicate_region_ids_skipped": skipped,
        "queued_region_count": len(result),
    }


def take_retained_region(
    queue: Sequence[Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
    initial: Mapping[str, Mapping[str, Any]],
    incumbent: Mapping[str, Any],
    active_params: Sequence[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    """Open the best unvisited region using PostgreSQL's stable priority."""
    if not queue:
        return copy.deepcopy(dict(current)), [], None
    ordered = sorted(
        enumerate(queue),
        key=lambda item: (
            -float(item[1].get("resource_potential") or 0.0),
            int(item[1].get("created_outer_attempt") or 0),
            item[0],
        ),
    )
    index, selected = ordered[0]
    selected_ranges = selected.get("ranges")
    if not isinstance(selected_ranges, Mapping):
        raise ValueError("retained region ranges required")
    expanded = copy.deepcopy(dict(current))
    for name in active_params:
        lower = max(
            float(initial[name]["lower"]),
            min(
                float(current[name]["lower"]),
                float(selected_ranges[name]["lower"]),
                float(incumbent[name]),
            ),
        )
        upper = min(
            float(initial[name]["upper"]),
            max(
                float(current[name]["upper"]),
                float(selected_ranges[name]["upper"]),
                float(incumbent[name]),
            ),
        )
        if lower > upper:
            raise RuntimeError(f"retained Algorithm C region has invalid bounds: {name}")
        expanded[name] = copy.deepcopy(dict(current[name]))
        expanded[name]["lower"] = lower
        expanded[name]["upper"] = upper
    remaining = [
        copy.deepcopy(dict(row))
        for position, row in enumerate(queue)
        if position != index
    ]
    result = copy.deepcopy(dict(selected))
    result.setdefault("id", region_identity(selected_ranges))
    return expanded, remaining, result


def policy_contract() -> dict[str, Any]:
    return {
        "policy_id": POLICY_ID,
        "source_candidate_and_batch_contract": V1_POLICY_ID,
        "retained_complement_partition": "non_overlapping_ordered_slabs",
        "region_identity_deduplication": True,
        "queued_and_visited_regions_not_readmitted": True,
        "first_miss_region_priority": "resource_potential_then_oldest_creation",
    }


__all__ = [
    "POLICY_ID",
    "append_new_regions",
    "policy_contract",
    "region_identity",
    "require_binary_progress",
    "require_complete_batch",
    "retained_complement_regions",
    "select_expanded_top5",
    "take_retained_region",
]
