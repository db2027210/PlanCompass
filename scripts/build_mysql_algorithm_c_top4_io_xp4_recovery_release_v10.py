#!/usr/bin/env python3
"""Freeze v17 IO C with actual-target-CPU gate scope."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_top4_io_xp4_recovery_release_v9 as predecessor


predecessor.predecessor.OUTPUT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v17"
)
predecessor.predecessor.RUNTIME = ROOT / "scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v7.py"
predecessor.predecessor.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v10.py"


def main() -> int:
    output = predecessor.predecessor.OUTPUT
    required = {
        output / "coordination_contract.json",
        output / "algorithm_c_xp4_async_contract.json",
        output / "transition_contract.json",
        output / "optimization_contract.json",
    }
    result = 0 if output.is_dir() and all(path.is_file() for path in required) else predecessor.main()
    release = predecessor.predecessor.release
    bind = predecessor.predecessor.bind
    gate = ROOT / "scripts/mysql_cross_project_wave_coordination_v5.py"
    refresh = ROOT / "scripts/refresh_mysql_xp4_live_authority_from_reset_v2.py"
    for name in (
        "coordination_contract.json", "algorithm_c_xp4_async_contract.json",
        "transition_contract.json",
    ):
        path = predecessor.predecessor.OUTPUT / name
        document = release.load(path)
        bindings = document.setdefault("source_bindings", {})
        if name == "coordination_contract.json":
            bindings["coordination_source_path"] = str(gate.relative_to(ROOT))
            bindings["coordination_source_sha256"] = release.sha(gate)
            bindings["authority_refresh_path"] = str(refresh.relative_to(ROOT))
            bindings["authority_refresh_sha256"] = release.sha(refresh)
            document["actual_target_cpu_scope_required"] = True
        else:
            bindings["dynamic_gate"] = bind(gate)
            bindings["current_authority_refresh"] = bind(refresh)
        release.atomic(path, document)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
