#!/usr/bin/env python3
"""Freeze v18 IO C with actual external CPU-affinity isolation."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_top4_io_xp4_recovery_release_v9 as predecessor


base = predecessor.predecessor
base.OUTPUT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v18"
)
base.RUNTIME = ROOT / "scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v8.py"
base.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v11.py"


def main() -> int:
    result = predecessor.main()
    gate = ROOT / "scripts/mysql_cross_project_wave_coordination_v6.py"
    refresh = ROOT / "scripts/refresh_mysql_xp4_live_authority_from_reset_v3.py"
    for name in (
        "coordination_contract.json", "algorithm_c_xp4_async_contract.json",
        "transition_contract.json",
    ):
        path = base.OUTPUT / name
        document = base.release.load(path)
        bindings = document.setdefault("source_bindings", {})
        if name == "coordination_contract.json":
            bindings["coordination_source_path"] = str(gate.relative_to(ROOT))
            bindings["coordination_source_sha256"] = base.release.sha(gate)
            bindings["authority_refresh_path"] = str(refresh.relative_to(ROOT))
            bindings["authority_refresh_sha256"] = base.release.sha(refresh)
            document["actual_target_cpu_scope_required"] = True
            document["obsolete_external_cpu_number_allowlist_required"] = False
        else:
            bindings["dynamic_gate"] = base.bind(gate)
            bindings["current_authority_refresh"] = base.bind(refresh)
        base.release.atomic(path, document)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

