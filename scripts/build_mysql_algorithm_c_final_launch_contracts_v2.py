#!/usr/bin/env python3
"""Publish v8/v11 after binding coordinated preflight proof."""

from __future__ import annotations
import hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v7/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v8/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v10/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v11/coordination_contract.json"

def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def rebind(value: dict) -> None:
    for key, raw in list(value.items()):
        if key.endswith("_path") and key[:-5] + "_sha256" in value:
            value[key[:-5] + "_sha256"] = sha((ROOT / str(raw)).resolve())
def write(path: Path, value: dict) -> None:
    if path.exists(): raise SystemExit(f"refusing to replace: {path}")
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n")
def main() -> int:
    opt = json.loads(OLD_OPT.read_text())
    opt["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v8"
    opt["predecessor"] = {"path": str(OLD_OPT.relative_to(ROOT)), "sha256": sha(OLD_OPT)}
    opt["successor_reason"] = "bind_coordinated_preflight_lease_proof_and_idle_controller_inventory_semantics"
    rebind(opt["bindings"]); write(NEW_OPT, opt)
    coord = json.loads(OLD_COORD.read_text())
    coord["predecessor"] = {"path": str(OLD_COORD.relative_to(ROOT)), "sha256": sha(OLD_COORD)}
    coord["successor_reason"] = "preflight_receives_live_exclusive_lease_holder_pid_and_contract"
    rebind(coord["source_bindings"]); write(NEW_COORD, coord)
    print(json.dumps({"optimization_sha256": sha(NEW_OPT), "coordination_sha256": sha(NEW_COORD)}))
    return 0
if __name__ == "__main__": raise SystemExit(main())
