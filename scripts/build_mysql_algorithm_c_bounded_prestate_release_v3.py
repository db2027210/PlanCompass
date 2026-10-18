#!/usr/bin/env python3
"""Publish run-scoped reset runtime v12 and coordination v19."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v11/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v12/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v18/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v19/coordination_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        if key.endswith("_path") and key[:-5] + "_sha256" in bindings:
            bindings[key[:-5] + "_sha256"] = sha((ROOT / str(value)).resolve())


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v12"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = "run_private_formal_incumbent_reset_evidence_path"
    optimization["run_scoped_reset_evidence"] = {
        "global_history_reset_path_prohibited": True,
        "formal_baseline_run_private": True,
        "formal_incumbent_baseline_run_private": True,
        "outer_candidate_reset_run_private": True,
        "existing_or_foreign_reset_directory_deletion": False,
        "workload_or_measurement_semantics_changed": False,
    }
    bindings = optimization["bindings"]
    bindings.update({
        "run_scoped_reset_collision_evidence_path": (
            "history/mysql_four_scene_20260809/"
            "v18_formal_bounded_prestate_v11_20260814T1020p0800_algorithm_c_v2/"
            "run_state.json"
        ),
        "run_scoped_reset_collision_evidence_sha256": "",
        "bounded_gate_pass_evidence_path": (
            "history/mysql_four_scene_20260809/"
            "v18_formal_bounded_prestate_v11_20260814T1020p0800_algorithm_c_v2/"
            "formal_incumbent_baseline_cross_project_wave_quiet_gate_attempt_001.json"
        ),
        "bounded_gate_pass_evidence_sha256": "",
    })
    rebind(bindings)
    write(NEW_OPT, optimization)

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = "bind_run_scoped_reset_boundary_runtime_v12"
    rebind(coordination["source_bindings"])
    write(NEW_COORD, coordination)
    print(json.dumps({
        "optimization_sha256": sha(NEW_OPT),
        "coordination_sha256": sha(NEW_COORD),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
