#!/usr/bin/env python3
"""Freeze C v85 with genuinely run-scoped quarantine targets."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from scripts import build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v3 as base


ROOT = base.ROOT
SOURCE = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v40"
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v41"
RESET = "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v4.py"


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"fresh release directory required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    optimization = base.load(SOURCE / "outer_runtime_optimization.json")
    optimization.update({"created_at": base.now(), "status": "FROZEN_RUNTIME_VALIDATED"})
    optimization["bindings"]["reset_source_path"] = RESET
    optimization["bindings"]["reset_source_sha256"] = base.sha(ROOT / RESET)
    optimization_path = OUTPUT / "outer_runtime_optimization.json"
    base.atomic(optimization_path, optimization)
    updates = {
        "controller": base.binding("scripts/run_mysql_algorithm_c_async_scene_v85.py"),
        "launcher": base.binding("scripts/launch_mysql_algorithm_c_async_v85.py"),
        "release_builder": base.binding(
            "scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v10.py"
        ),
        "outer_runtime_optimization": base.binding(
            str(optimization_path.relative_to(ROOT))
        ),
        "mixed_worker_reset": base.binding(RESET),
        "mixed_worker_reset_run_scope_base": base.binding(
            "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v4_base.py"
        ),
    }
    parent = base.load(SOURCE / "algorithm_c_async_contract.json")
    parent.update({"created_at": base.now(), "status": "FROZEN_RUNTIME_VALIDATED"})
    parent_sources = base.refresh(parent.get("source_bindings") or {})
    parent_sources.update(updates)
    parent["source_bindings"] = parent_sources
    parent_path = OUTPUT / "algorithm_c_async_contract.json"
    base.atomic(parent_path, parent)
    contract = base.load(SOURCE / "global_monotonic_incumbent_contract.json")
    contract.update({
        "release_version": "v85",
        "created_at": base.now(),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "parent_contract": base.binding(str(parent_path.relative_to(ROOT))),
        "run_scoped_quarantine_successor": {
            "predecessor_release": "v84",
            "predecessor_collision_scope": "batch_kind/reset_token",
            "path_components": [
                "run_id", "outer", "batch_kind", "reset_token", "worker_id"
            ],
            "same_run_same_token_collision_remains_fail_closed": True,
            "cross_run_same_outer_collision_eliminated": True,
            "single_worker_management_mutex_retained": True,
            "candidate_or_outer_policy_changed": False,
            "completed_candidate_measurements_replay_policy_changed": False,
        },
        "failed_predecessor_release": {
            "release_version": "v84",
            "scene": "hybench_xp_cpu",
            "outer_attempt": 18,
            "accepted_or_rejected_state_mutation": False,
            "completed_top5_measurements_retained": True,
            "completed_fallback_measurements_retained": True,
            "failure": "cross-run current-interval quarantine token collision",
        },
    })
    sources = base.refresh(contract.get("source_bindings") or {})
    sources.update(updates)
    sources.update({
        "parent_contract": base.binding(str(parent_path.relative_to(ROOT))),
        "parent_optimization_contract": base.binding(
            str(optimization_path.relative_to(ROOT))
        ),
    })
    contract["source_bindings"] = sources
    contract_path = OUTPUT / "global_monotonic_incumbent_contract.json"
    base.atomic(contract_path, contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "release_version": "v85",
        "global_contract": str(contract_path),
        "global_sha256": base.sha(contract_path),
        "parent_sha256": base.sha(parent_path),
        "optimization_sha256": base.sha(optimization_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
