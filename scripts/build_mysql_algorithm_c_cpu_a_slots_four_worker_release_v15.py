#!/usr/bin/env python3
"""Freeze C v90 IO exact-boundary fallback reuse hook."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v3 as base


SOURCE = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v45"
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v46"


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"fresh release directory required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    optimization = base.load(SOURCE / "outer_runtime_optimization.json")
    optimization.update({"created_at": base.now(), "status": "FROZEN_RUNTIME_VALIDATED"})
    optimization_path = OUTPUT / "outer_runtime_optimization.json"
    base.atomic(optimization_path, optimization)
    updates = {
        "controller": base.binding("scripts/run_mysql_algorithm_c_async_scene_v90.py"),
        "launcher": base.binding("scripts/launch_mysql_algorithm_c_async_v90.py"),
        "release_builder": base.binding("scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v15.py"),
        "outer_runtime_optimization": base.binding(str(optimization_path.relative_to(ROOT))),
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
        "release_version": "v90",
        "created_at": base.now(),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "parent_contract": base.binding(str(parent_path.relative_to(ROOT))),
        "exact_boundary_fallback_reuse_hook": {
            "scene": "hybench_xp_io",
            "outer_attempt": 13,
            "formal_partial_state_presence_not_used_as_hook_guard": True,
            "exact_source_and_regenerated_candidate_required": True,
            "candidate_vector_reverified": True,
            "physical_fallback_remeasurement": False,
            "new_physical_work_starts_at": "current_interval_plan_coverage",
            "accepted_or_rejected_credit": 0,
            "candidate_or_outer_policy_changed": False,
            "algorithm_a_input_used": False,
        },
    })
    sources = base.refresh(contract.get("source_bindings") or {})
    sources.update(updates)
    sources.update({
        "parent_contract": base.binding(str(parent_path.relative_to(ROOT))),
        "parent_optimization_contract": base.binding(str(optimization_path.relative_to(ROOT))),
    })
    contract["source_bindings"] = sources
    contract_path = OUTPUT / "global_monotonic_incumbent_contract.json"
    base.atomic(contract_path, contract)
    print(json.dumps({"status": "FROZEN_RUNTIME_VALIDATED", "release_version": "v90", "global_contract": str(contract_path), "global_sha256": base.sha(contract_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
