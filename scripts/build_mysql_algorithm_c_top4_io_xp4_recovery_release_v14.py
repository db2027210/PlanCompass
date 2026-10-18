#!/usr/bin/env python3
"""Freeze v21 IO C on the unified v3 combined resource table."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_top4_io_xp4_recovery_release_v9 as predecessor


base = predecessor.predecessor
base.OUTPUT = ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v21"
base.SUCCESSOR = ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v3/resource_successor.json"
base.RUNTIME = ROOT / "scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v9.py"
base.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v14.py"


def main() -> int:
    result = predecessor.main()
    gate = ROOT / "scripts/mysql_cross_project_wave_coordination_v7.py"
    refresh = ROOT / "scripts/refresh_mysql_xp_io_combined_live_authority_v1.py"
    coordination_path = base.OUTPUT / "coordination_contract.json"
    coordination = base.release.load(coordination_path)
    bindings = coordination.setdefault("source_bindings", {})
    bindings["coordination_source_path"] = str(gate.relative_to(ROOT))
    bindings["coordination_source_sha256"] = base.release.sha(gate)
    bindings["authority_refresh_path"] = str(refresh.relative_to(ROOT))
    bindings["authority_refresh_sha256"] = base.release.sha(refresh)
    base.release.atomic(coordination_path, coordination)
    for name in ("algorithm_c_xp4_async_contract.json", "transition_contract.json"):
        path = base.OUTPUT / name
        document = base.release.load(path)
        outer = document.setdefault("source_bindings", {})
        outer["dynamic_gate"] = base.bind(gate)
        outer["current_authority_refresh"] = base.bind(refresh)
        outer["coordination_contract"] = base.bind(coordination_path)
        base.release.atomic(path, document)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

