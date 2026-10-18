"""Evidence-based concurrency control for independent MySQL replay workers.

The thresholds and discrete tiers are frozen from the 2026-08-09 HyBench XP
single-SSD study.  This module only selects a concurrency value; callers must
separately prove that workers are independent database instances.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple


DEFAULT_TIERS = (1, 4, 8, 12)


def _bounded_tiers(stable_cap: int, tiers: Sequence[int]) -> Tuple[int, ...]:
    values = sorted({max(1, int(value)) for value in tiers if int(value) <= stable_cap})
    if stable_cap not in values:
        values.append(stable_cap)
    return tuple(values)


def choose_same_ssd_parallelism(
    *,
    current: int,
    available_workers: int,
    pending_candidates: int,
    mean_util_pct: Optional[float],
    peak_util_pct: Optional[float],
    preferred: int = 8,
    stable_cap: int = 12,
    allow_stable_cap: bool = False,
    daily_mean_limit_pct: float = 75.0,
    daily_peak_limit_pct: float = 90.0,
    warning_mean_pct: float = 85.0,
    warning_peak_pct: float = 95.0,
    tiers: Sequence[int] = DEFAULT_TIERS,
) -> Dict[str, Any]:
    """Choose the next replay concurrency using the tested 1/4/8/12 tiers."""

    stable_cap = max(1, min(12, int(stable_cap)))
    preferred = max(1, min(int(preferred), stable_cap))
    tested_tiers = _bounded_tiers(stable_cap, tiers)
    current_tier = max(1, min(int(current), stable_cap))
    target_cap = stable_cap if allow_stable_cap else preferred
    desired = min(current_tier, target_cap)
    reason = "no_valid_ssd_observation_hold"

    observation_valid = mean_util_pct is not None and peak_util_pct is not None
    if observation_valid:
        mean_util = float(mean_util_pct)
        peak_util = float(peak_util_pct)
        if mean_util >= warning_mean_pct or peak_util >= warning_peak_pct:
            lower = [tier for tier in tested_tiers if tier < current_tier]
            desired = lower[-1] if lower else tested_tiers[0]
            reason = "ssd_warning_scale_down_one_tier"
        elif mean_util < daily_mean_limit_pct and peak_util < daily_peak_limit_pct:
            higher = [tier for tier in tested_tiers if current_tier < tier <= target_cap]
            desired = higher[0] if higher else min(current_tier, target_cap)
            reason = (
                "ssd_headroom_scale_up_one_tier"
                if higher
                else "preferred_or_stable_cap_hold"
            )
        else:
            desired = min(current_tier, target_cap)
            reason = "ssd_daily_headroom_not_met_hold"

    effective = min(
        max(0, int(available_workers)),
        max(0, int(pending_candidates)),
        max(1, desired),
    )
    return {
        "policy": "hybench_xp_single_ssd_20260809",
        "source": "../resource_adaptive/docs/hybench_xp_single_ssd_concurrency_summary_20260809.md",
        "observation_valid": observation_valid,
        "mean_util_pct": mean_util_pct,
        "peak_util_pct": peak_util_pct,
        "current": current_tier,
        "desired": desired,
        "effective": effective,
        "available_workers": max(0, int(available_workers)),
        "pending_candidates": max(0, int(pending_candidates)),
        "preferred": preferred,
        "stable_cap": stable_cap,
        "allow_stable_cap": bool(allow_stable_cap),
        "tiers": list(tested_tiers),
        "daily_mean_limit_pct": float(daily_mean_limit_pct),
        "daily_peak_limit_pct": float(daily_peak_limit_pct),
        "warning_mean_pct": float(warning_mean_pct),
        "warning_peak_pct": float(warning_peak_pct),
        "reason": reason,
    }
