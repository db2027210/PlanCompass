#!/usr/bin/env python3
"""Freeze the adapter-compatible four-worker Top4 IO C successor."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import prepare_mysql_algorithm_c_xp4_handoff_v1 as release


OUTPUT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v9"
)
SUCCESSOR = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_xp4_handoff_20260826_v2"
    / "xp_four_worker_resource_successor.json"
)
RUNTIME = ROOT / "scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v2.py"
RUNTIME_PREDECESSOR = ROOT / "scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v1.py"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v2.py"
CONTROLLER_PREDECESSOR = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v1.py"
ADAPTER = ROOT / "mysql_workload_adapter.py"


def bind(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": release.sha(path)}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError("fresh Algorithm C IO XP4 v9 release required")
    OUTPUT.mkdir(parents=True, mode=0o700)
    release.CONTRACT_ROOT = OUTPUT
    release.SUCCESSOR_FREEZE = SUCCESSOR
    release.TRANSITION_CONTRACT = OUTPUT / "transition_contract.json"
    release.OPTIMIZATION_CONTRACT = OUTPUT / "optimization_contract.json"
    release.COORDINATION_CONTRACT = OUTPUT / "coordination_contract.json"
    release.ASYNC_CONTRACT = OUTPUT / "algorithm_c_xp4_async_contract.json"
    release.write_contracts(release.sha(SUCCESSOR))

    coordination = release.load(release.COORDINATION_CONTRACT)
    coordination["source_bindings"]["boundary_resume_controller_path"] = str(
        RUNTIME.relative_to(ROOT)
    )
    coordination["source_bindings"]["boundary_resume_controller_sha256"] = release.sha(
        RUNTIME
    )
    coordination["xp_graceful_quiescence_seconds"] = 120
    release.atomic(release.COORDINATION_CONTRACT, coordination)

    optimization = release.load(release.OPTIMIZATION_CONTRACT)
    optimization["xp_adaptive_measurement"] = {
        "minimum_measurement_seconds": 300,
        "maximum_measurement_seconds": 900,
        "graceful_quiescence_seconds": 120,
        "candidate_or_outer_flow_changed": False,
        "native_workload_unchanged": True,
    }
    optimization["bindings"]["workload_adapter_path"] = str(ADAPTER.relative_to(ROOT))
    optimization["bindings"]["workload_adapter_sha256"] = release.sha(ADAPTER)
    release.atomic(release.OPTIMIZATION_CONTRACT, optimization)

    source_updates = {
        "controller": bind(CONTROLLER),
        "controller_predecessor": bind(CONTROLLER_PREDECESSOR),
        "runtime": bind(RUNTIME),
        "runtime_predecessor": bind(RUNTIME_PREDECESSOR),
        "workload_adapter": bind(ADAPTER),
        "optimization_contract": bind(release.OPTIMIZATION_CONTRACT),
        "coordination_contract": bind(release.COORDINATION_CONTRACT),
    }
    for contract_path in (release.ASYNC_CONTRACT, release.TRANSITION_CONTRACT):
        document = release.load(contract_path)
        document["source_bindings"].update(source_updates)
        document["xp_graceful_quiescence_seconds"] = 120
        document["xp_measurement_seconds"] = {"minimum": 300, "maximum": 900}
        document["adapter_compatibility_successor"] = True
        release.atomic(contract_path, document)

    print(json.dumps({
        "status": "FROZEN", "release": str(OUTPUT),
        "successor_freeze": str(SUCCESSOR),
        "graceful_quiescence_seconds": 120,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
