#!/usr/bin/env python3
"""Rebind every pruning-v2 runtime source into immutable v3/v6 contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v2/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v3/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v5/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260813_v6/coordination_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rebind_all(binding: dict) -> None:
    for key, value in list(binding.items()):
        if not key.endswith("_path"):
            continue
        sha_key = key[:-5] + "_sha256"
        if sha_key not in binding:
            continue
        path = (ROOT / str(value)).resolve()
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"bound regular file absent: {path}")
        binding[sha_key] = sha(path)


def write(path: Path, value: dict) -> None:
    if path.exists():
        raise SystemExit(f"refusing to replace: {path}")
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n")


def main() -> int:
    opt = load(OLD_OPT)
    opt["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v3"
    opt["predecessor"] = {"path": str(OLD_OPT.relative_to(ROOT)), "sha256": sha(OLD_OPT)}
    opt["successor_reason"] = "rebind_all_runtime_sources_after_pruning_v2_integration"
    rebind_all(opt["bindings"])
    write(NEW_OPT, opt)
    coord = load(OLD_COORD)
    coord["predecessor"] = {"path": str(OLD_COORD.relative_to(ROOT)), "sha256": sha(OLD_COORD)}
    coord["successor_reason"] = "rebind_pruning_v2_controller_and_coordination_source_without_schedule_change"
    rebind_all(coord["source_bindings"])
    write(NEW_COORD, coord)
    print(json.dumps({"optimization_sha256": sha(NEW_OPT), "coordination_sha256": sha(NEW_COORD)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
