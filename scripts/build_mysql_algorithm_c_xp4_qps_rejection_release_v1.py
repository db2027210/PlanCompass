#!/usr/bin/env python3
"""Freeze four-worker XP Algorithm C strict-QPS/two-reject semantics."""

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
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v8"
)
OUTPUT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_xp_top4_qps_rejection_20260826_v9"
)
CONTRACT = OUTPUT / "algorithm_c_xp4_async_contract.json"
OPTIMIZATION = OUTPUT / "optimization_contract.json"
COORDINATION = OUTPUT / "coordination_contract.json"


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
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        raise RuntimeError(f"refusing to overwrite frozen release: {OUTPUT}")
    from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
    from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle

    created = datetime.now(timezone.utc).astimezone().isoformat()
    optimization = load(PREDECESSOR / "optimization_contract.json")
    optimization.update({
        "schema_version": "mysql_algorithm_c_xp4_optimization_contract_v2",
        "created_at": created,
    })
    optimization["pg_search_outer"] = {
        **dict(optimization.get("pg_search_outer") or {}),
        "required_acceptance_tier": "qps_strict_progress",
        "accepted_target": None,
        "terminal_normalized_width": 0.01,
        "outer_rejection_cycle_policy_id": rejection_cycle.POLICY_ID,
        "post_search_stability_confirmation": False,
    }
    optimization["bindings"] = {
        **dict(optimization.get("bindings") or {}),
        "reset_source_sha256": sha(
            ROOT / "scripts/reset_mysql_xp_data_planes_four_worker_v1.py"
        ),
        "algorithm_source_sha256": sha(
            ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py"
        ),
        "resource_freeze_path": (
            "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4/"
            "resource_successor.json"
        ),
        "resource_freeze_sha256": sha(
            ROOT
            / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4"
            / "resource_successor.json"
        ),
    }
    atomic(OPTIMIZATION, optimization)
    coordination = load(PREDECESSOR / "coordination_contract.json")
    coordination["created_at"] = created
    coordination["source_bindings"] = {
        "coordination_source_path": "scripts/mysql_cross_project_wave_coordination_v8.py",
        "coordination_source_sha256": sha(
            ROOT / "scripts/mysql_cross_project_wave_coordination_v8.py"
        ),
        "boundary_resume_controller_path": "scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v1.py",
        "boundary_resume_controller_sha256": sha(
            ROOT / "scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v1.py"
        ),
    }
    atomic(COORDINATION, coordination)

    contract = copy.deepcopy(load(PREDECESSOR / "algorithm_c_xp4_async_contract.json"))
    contract.update({
        "schema_version": "mysql_algorithm_c_xp4_qps_rejection_contract_v1",
        "created_at": created,
        "outer_acceptance_policy": qps_policy.policy_contract(),
        "outer_rejection_cycle_policy": rejection_cycle.policy_contract(),
        "convergence_metric": "maximum_log_domain_width_relative_to_frozen_initial",
        "terminal_normalized_width": 0.01,
        "accepted_round_credit_from_forced_shrink": False,
        "fresh_search_required": True,
        "previous_strategy_results_eligible_for_credit": False,
        "post_search_stability_confirmation": False,
        "inner_candidate_generation_changed": False,
        "inner_candidate_ranking_changed": False,
        "algorithm_a_changed": False,
        "algorithm_c_candidate_or_outer_flow_changed": True,
        "formal_measurement_started": False,
    })
    paths = {
        "controller": ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v20.py",
        "launcher": ROOT / "scripts/launch_mysql_algorithm_c_xp4_qps_rejection_v1.py",
        "runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v1.py",
        "qps_only_outer_policy": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
        "outer_rejection_cycle_policy": ROOT / "scripts/mysql_outer_rejection_cycle_v1.py",
        "optimization_contract": OPTIMIZATION,
        "coordination_contract": COORDINATION,
        "authority": ROOT / "scripts/run_mysql_xp_four_worker_authority_v1.py",
        "reset": ROOT / "scripts/reset_mysql_xp_data_planes_four_worker_v1.py",
        "resource_successor": (
            ROOT
            / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4"
            / "resource_successor.json"
        ),
    }
    contract["source_bindings"] = {
        name: binding(path) for name, path in paths.items()
    }
    atomic(CONTRACT, contract)
    print(CONTRACT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
