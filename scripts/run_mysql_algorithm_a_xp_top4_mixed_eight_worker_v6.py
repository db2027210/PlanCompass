#!/usr/bin/env python3
"""XP8 Algorithm-A v6 with zero-aware same-worker normalization."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v5.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v5"
new = "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v6"
if text.count(old) != 1:
    raise RuntimeError("XP8 A v6 predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp8_v6_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


def _find_base(value: Any, seen: set[int] | None = None):
    seen = seen or set()
    if id(value) in seen:
        return None
    seen.add(id(value))
    if isinstance(value, dict):
        candidate = value.get("base")
        if getattr(candidate, "__name__", "") == "scripts.run_mysql_algorithm_a_async_scene_v3":
            return candidate
        for child in value.values():
            if isinstance(child, dict):
                found = _find_base(child, seen)
                if found is not None:
                    return found
    return None


base = _find_base(implementation)
if base is None:
    raise RuntimeError("XP8 A v6 core module differs")


def normalize_to_worker_baseline(
    row: Mapping[str, Any], worker_baseline: Mapping[str, Any],
    canonical: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(row))
    worker_qps = float(worker_baseline.get("db_qps") or 0.0)
    canonical_qps = float(canonical.get("db_qps") or 0.0)
    if worker_qps <= 0.0 or canonical_qps <= 0.0:
        raise RuntimeError("Algorithm A same-worker baseline QPS is non-positive")
    normalized["db_qps"] = canonical_qps * float(row["db_qps"]) / worker_qps
    normalized["resource_means"] = {}
    zero_fallback: dict[str, str] = {}
    for name in ("cpu", "memory", "io"):
        worker_value = float(worker_baseline["resource_means"][name])
        candidate_value = float(row["resource_means"][name])
        canonical_value = float(canonical["resource_means"][name])
        if worker_value < 0.0 or candidate_value < 0.0 or canonical_value < 0.0:
            raise RuntimeError(f"Algorithm A resource is negative: {name}")
        if worker_value > 0.0:
            value = canonical_value * candidate_value / worker_value
        elif candidate_value == 0.0:
            # Both observations are below cgroup counter resolution, so the
            # only supported ratio is one on the canonical comparison scale.
            value = canonical_value
            zero_fallback[name] = "both_zero_canonical_ratio_one"
        else:
            # A positive candidate following an exact-zero baseline cannot be
            # ratio-scaled.  Resource means are already normalized to the same
            # frozen envelope, so retain the observed absolute utilization.
            value = candidate_value
            zero_fallback[name] = "positive_candidate_raw_envelope_utilization"
        normalized["resource_means"][name] = value
    normalized["same_worker_normalization"] = {
        "worker_id": row.get("worker_id"),
        "raw_db_qps": row.get("db_qps"),
        "raw_resource_means": copy.deepcopy(row.get("resource_means")),
        "worker_baseline_db_qps": worker_baseline.get("db_qps"),
        "worker_baseline_resource_means": copy.deepcopy(
            worker_baseline.get("resource_means")
        ),
        "canonical_baseline_db_qps": canonical.get("db_qps"),
        "canonical_baseline_resource_means": copy.deepcopy(
            canonical.get("resource_means")
        ),
        "zero_baseline_fallback": zero_fallback,
    }
    return normalized


base.normalize_to_worker_baseline = normalize_to_worker_baseline


def main() -> int:
    return int(implementation["main"]())


if __name__ == "__main__":
    raise SystemExit(main())

