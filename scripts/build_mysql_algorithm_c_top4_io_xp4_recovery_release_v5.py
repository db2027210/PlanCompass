#!/usr/bin/env python3
"""Freeze the v12 Top4 IO C successor with executable-module bindings."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_top4_io_xp4_recovery_release_v2 as predecessor


predecessor.OUTPUT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v12"
)
predecessor.RUNTIME = ROOT / "scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v4.py"
predecessor.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v5.py"


def main() -> int:
    result = predecessor.main()
    coordination_path = predecessor.OUTPUT / "coordination_contract.json"
    coordination = predecessor.release.load(coordination_path)
    executable = predecessor.RUNTIME_PREDECESSOR
    coordination["source_bindings"]["boundary_resume_controller_path"] = str(
        executable.relative_to(ROOT)
    )
    coordination["source_bindings"]["boundary_resume_controller_sha256"] = (
        predecessor.release.sha(executable)
    )
    predecessor.release.atomic(coordination_path, coordination)
    for name in ("algorithm_c_xp4_async_contract.json", "transition_contract.json"):
        path = predecessor.OUTPUT / name
        document = predecessor.release.load(path)
        document["source_bindings"]["coordination_contract"] = predecessor.bind(
            coordination_path
        )
        predecessor.release.atomic(path, document)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
