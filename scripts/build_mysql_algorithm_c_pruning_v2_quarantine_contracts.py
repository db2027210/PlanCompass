#!/usr/bin/env python3
"""Bind pruning-v2 clean-quarantine runtime sources into v6/v8 contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v5/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v6/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v7/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v8/coordination_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        if key.endswith("_path") and key[:-5] + "_sha256" in bindings:
            path = (ROOT / str(value)).resolve()
            if not path.is_file() or path.is_symlink():
                raise SystemExit(f"bound source absent: {path}")
            bindings[key[:-5] + "_sha256"] = sha(path)


def write(path: Path, value: dict) -> None:
    if path.exists():
        raise SystemExit(f"refusing to replace: {path}")
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n")


def main() -> int:
    opt = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    opt["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v6"
    opt["predecessor"] = {"path": str(OLD_OPT.relative_to(ROOT)), "sha256": sha(OLD_OPT)}
    opt["successor_reason"] = "activate_empty_v18_pruning_v2_replay_quarantine_without_importing_v15_invalid_evidence"
    opt["replay_quarantine_policy"] = {
        **dict(opt.get("replay_quarantine_policy") or {}),
        "schema_version": "mysql_algorithm_c_replay_quarantine_v6",
        "new_v18_campaign_initial_counts_by_scene": {scene: 0 for scene in ("job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io")},
        "pre_v18_evidence_imported": False,
    }
    rebind(opt["bindings"])
    write(NEW_OPT, opt)

    coord = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coord["predecessor"] = {"path": str(OLD_COORD.relative_to(ROOT)), "sha256": sha(OLD_COORD)}
    coord["successor_reason"] = "bind_v18_empty_quarantine_controller_sources_without_schedule_change"
    rebind(coord["source_bindings"])
    write(NEW_COORD, coord)
    print(json.dumps({"optimization_sha256": sha(NEW_OPT), "coordination_sha256": sha(NEW_COORD)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
