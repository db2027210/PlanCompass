#!/usr/bin/env python3
"""Run the v85 same-worker A/B with the v87 normalized physical IO gate."""
from __future__ import annotations

import sys

import hybenchxp_memory_v85_same_vector_composition_driver as driver


IO_CAPACITY_MIB_S = 200.0


def normalized_transfer(base, cand):
    other_up = []
    if float(cand["cpu_pct"]) > float(base["cpu_pct"]) + 0.01:
        other_up.append("cpu")
    if float(cand["io_mib_s"]) > float(base["io_mib_s"]) + 0.01:
        other_up.append("io")
    sweet = []
    if 65.0 <= float(cand["cpu_pct"]) <= 80.0:
        sweet.append("cpu")
    io_pct = 100.0 * float(cand["io_mib_s"]) / IO_CAPACITY_MIB_S
    if 50.0 <= io_pct <= 75.0:
        sweet.append("io")
    intersection = sorted(set(other_up) & set(sweet))
    return {
        "other_up_resources": other_up,
        "other_sweet_spot_passed": sweet,
        "intersection": intersection,
        "intersection_nonempty": bool(intersection),
        "io_capability_mib_s": IO_CAPACITY_MIB_S,
        "candidate_normalized_io_pct": io_pct,
    }


def normalized_baseline_gate(base, aligned):
    result = driver.baseline_hard_gates(base, aligned)
    io_pct = 100.0 * float(base.get("io_mib_s") or 0.0) / IO_CAPACITY_MIB_S
    result["gates"].pop("io_lt_75", None)
    result["gates"]["normalized_physical_io_lt_80"] = io_pct < 80.0
    result["baseline_normalized_io_pct"] = io_pct
    result["io_capability_mib_s"] = IO_CAPACITY_MIB_S
    result["failed"] = [key for key, value in result["gates"].items() if not value]
    result["ok"] = not result["failed"]
    return result


driver.other_up_intersect_sweet = normalized_transfer
driver.baseline_hard_gates = normalized_baseline_gate

if __name__ == "__main__":
    sys.exit(driver.main())
