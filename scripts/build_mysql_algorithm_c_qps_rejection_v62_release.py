#!/usr/bin/env python3
"""Freeze Algorithm-C v62 for four scenes at two physical workers each."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PREDECESSOR = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260826_v7"
    / "algorithm_c_async_contract.json"
)
PREDECESSOR_OPTIMIZATION = PREDECESSOR.parent / "outer_runtime_optimization.json"
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_qps_20260826_v8"
CONTRACT = OUTPUT / "algorithm_c_async_contract.json"
OPTIMIZATION = OUTPUT / "outer_runtime_optimization.json"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19"
    / "four_scene_effective_resource_successor.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def binding(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh release required: {OUTPUT}")
    from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
    from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle

    OUTPUT.mkdir(parents=True, mode=0o700)
    optimization = load(PREDECESSOR_OPTIMIZATION)
    optimization.update({
        "schema_version": "mysql_algorithm_c_outer_runtime_optimization_v62",
        "released_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "four_scene_two_worker_freeze_path": str(FREEZE),
        "four_scene_two_worker_freeze_sha256": sha(FREEZE),
    })
    optimization["bindings"] = {
        **dict(optimization.get("bindings") or {}),
        "resource_freeze_path": str(FREEZE.relative_to(ROOT)),
        "resource_freeze_sha256": sha(FREEZE),
    }
    atomic(OPTIMIZATION, optimization)
    contract = load(PREDECESSOR)
    contract.update({
        "release_version": "v62",
        "released_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "outer_acceptance_policy": qps_policy.policy_contract(),
        "outer_rejection_cycle_policy": rejection_cycle.policy_contract(),
        "formal_measurement_started": False,
        "fresh_search_required": True,
        "previous_strategy_results_eligible_for_v62_credit": False,
        "physical_replay_parallelism_per_scene": 2,
        "maximum_concurrent_mysql_workers": 8,
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "four_scene_wave_barrier": False,
        "sibling_scene_wait": False,
        "lease_mode": "none",
        "per_scene_outer_state_machine": True,
        "scene_may_advance_immediately_after_own_round": True,
        "post_search_stability_confirmation": False,
        "slot_reassignment_policy": {
            "job_completion_expands_xp_cpu_from_two_to_four": True,
            "tpch_completion_expands_xp_io_from_two_to_four": True,
            "transition_only_at_completed_round_boundary": True,
            "accepted_round_history_preserved": True,
            "completed_candidates_not_replayed_for_parallelism_change": True,
        },
    })
    bindings = copy.deepcopy(contract.get("source_bindings") or {})
    paths = {
        "controller": ROOT / "scripts/run_mysql_algorithm_c_async_scene_v62.py",
        "launcher": ROOT / "scripts/launch_mysql_algorithm_c_async_v62.py",
        "predecessor_controller_v61": ROOT / "scripts/run_mysql_algorithm_c_async_scene_v61.py",
        "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v8.py",
        "four_scene_two_worker_resource_freeze": FREEZE,
        "outer_runtime_optimization": OPTIMIZATION,
        "qps_only_outer_policy": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
        "outer_rejection_cycle_policy": ROOT / "scripts/mysql_outer_rejection_cycle_v1.py",
    }
    bindings.update({name: binding(path) for name, path in paths.items()})
    contract["source_bindings"] = bindings
    atomic(CONTRACT, contract)
    print(CONTRACT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
