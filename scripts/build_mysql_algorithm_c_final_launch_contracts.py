#!/usr/bin/env python3
"""Publish final v7/v10 contracts for R0-to-Algorithm-C automatic launch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v6/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v7/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v9/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v10/coordination_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rebind(bindings: dict) -> None:
    for key, raw in list(bindings.items()):
        if key.endswith("_path") and key[:-5] + "_sha256" in bindings:
            path = (ROOT / str(raw)).resolve()
            if not path.is_file() or path.is_symlink():
                raise SystemExit(f"bound file absent: {path}")
            bindings[key[:-5] + "_sha256"] = sha(path)


def write(path: Path, value: dict) -> None:
    if path.exists():
        raise SystemExit(f"refusing to replace: {path}")
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n")


def main() -> int:
    opt = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    opt["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v7"
    opt["predecessor"] = {"path": str(OLD_OPT.relative_to(ROOT)), "sha256": sha(OLD_OPT)}
    opt["successor_reason"] = "require_fresh_v18_r0_then_formal_eight_lane_incumbent_baseline_before_outer5"
    opt["formal_baseline_transition"] = {
        "fresh_r0_required": True,
        "formal_eight_lane_incumbent_baseline_required": True,
        "workers_per_scene": 2,
        "incumbent_aggregation": "arithmetic_mean_of_two_formal_lanes",
        "accepted_rounds_mutated": False,
        "checkpoint_pilot_incumbent_replaced_before_candidate_ranking": True,
    }
    rebind(opt["bindings"])
    write(NEW_OPT, opt)

    coord = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coord["predecessor"] = {"path": str(OLD_COORD.relative_to(ROOT)), "sha256": sha(OLD_COORD)}
    coord["successor_reason"] = "bind_fresh_r0_and_formal_baseline_automatic_outer5_launch_sources"
    rebind(coord["source_bindings"])
    write(NEW_COORD, coord)
    print(json.dumps({"optimization_sha256": sha(NEW_OPT), "coordination_sha256": sha(NEW_COORD)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
