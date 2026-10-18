#!/usr/bin/env python3
"""Prepare the predeclared 240 MiB/s Memory v89 exact-recipe configs."""
from __future__ import annotations

import json
from pathlib import Path

import hybenchxp_memory_v88_parallel_exact_recipe_ab as batch


REPO = Path("/home/anonymous/program/resource_adaptive")
PHASE_A = REPO / "report/bonect/hybenchxp_memory_fair_v87_normalized_io_phase_a_20260715.json"


def main() -> None:
    phase_a = json.loads(PHASE_A.read_text(encoding="utf-8"))
    eligible = {int(row["io_capability_mib_s"]) for row in phase_a["eligible_set"]}
    if 240 not in eligible:
        raise RuntimeError("240 MiB/s was not predeclared eligible by Phase A")
    batch.IO_CAPACITY_MIB_S = 240
    batch.XAP_BY_CASE = {1: 3, 2: 4, 3: 4, 4: 4}
    configs = {}
    for index in batch.CASES:
        generated = batch.config_for(index)
        final = REPO / f"config/binary_plan_tuning_hybenchxp_memory_fair_v89_cap240_case{index}_20260715.json"
        final.write_text(generated.read_text(encoding="utf-8"), encoding="utf-8")
        configs[index] = str(final)
    manifest = {
        "phase_a_evidence": str(PHASE_A),
        "io_capability_mib_s": 240,
        "memory_gib": 6.4,
        "cpu_cores": 10,
        "w1_exact_recipe": {"xtpclient": 1, "xapclient": 3, "weights": "65,35",
                            "sequence": "6,6,6,6,6,6,8,8,8,8", "seed": 4508333},
        "configs": configs,
        "fallback_db_qps": 114.12708344892893,
    }
    out = REPO / "report/bonect/hybenchxp_memory_fair_v89_cap240_manifest_20260715.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
