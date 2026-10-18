#!/usr/bin/env python3
"""Freeze Algorithm-C v61 strict-QPS/two-reject outer semantics."""

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
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260826_v6"
    / "algorithm_c_async_contract.json"
)
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260826_v7"
CONTRACT = OUTPUT / "algorithm_c_async_contract.json"
OPTIMIZATION_PREDECESSOR = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260819_v37"
    / "algorithm_c_async_optimization_contract.json"
)
OPTIMIZATION = OUTPUT / "outer_runtime_optimization.json"


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
        existing = load(CONTRACT) if CONTRACT.is_file() else {}
        if (
            existing.get("release_version") != "v61"
            or existing.get("formal_measurement_started") is True
        ):
            raise RuntimeError(f"refusing to overwrite frozen release: {OUTPUT}")
    from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
    from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle

    optimization = load(OPTIMIZATION_PREDECESSOR)
    optimization.update({
        "schema_version": "mysql_algorithm_c_outer_runtime_optimization_v61",
        "released_at": datetime.now(timezone.utc).astimezone().isoformat(),
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
            ROOT / "scripts/reset_mysql_xp_data_planes_v7.py"
        ),
        "algorithm_source_sha256": sha(
            ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py"
        ),
    }
    atomic(OPTIMIZATION, optimization)

    contract = load(PREDECESSOR)
    contract.update({
        "release_version": "v61",
        "released_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "outer_acceptance_policy": qps_policy.policy_contract(),
        "outer_rejection_cycle_policy": rejection_cycle.policy_contract(),
        "convergence_metric": "maximum_log_domain_width_relative_to_frozen_initial",
        "terminal_normalized_width": 0.01,
        "accepted_round_credit_from_forced_shrink": False,
        "fresh_search_required": True,
        "previous_strategy_results_eligible_for_v61_credit": False,
        "inner_candidate_generation_changed": False,
        "inner_candidate_ranking_changed": False,
        "algorithm_a_changed": False,
        "prelaunch_validation_correction": (
            "successor_contract_replaces_stale_predecessor_core_hash_validator"
        ),
        "formal_measurement_started": False,
    })
    bindings = copy.deepcopy(contract.get("source_bindings") or {})
    paths = {
        "controller": ROOT / "scripts/run_mysql_algorithm_c_async_scene_v61.py",
        "launcher": ROOT / "scripts/launch_mysql_algorithm_c_async_v61.py",
        "predecessor_controller_v60": ROOT / "scripts/run_mysql_algorithm_c_async_scene_v60.py",
        "qps_only_outer_policy": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
        "outer_rejection_cycle_policy": ROOT / "scripts/mysql_outer_rejection_cycle_v1.py",
        "coordinated_measurement_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v2.py",
        "outer_runtime_optimization": OPTIMIZATION,
    }
    bindings.update({name: binding(path) for name, path in paths.items()})
    contract["source_bindings"] = bindings
    atomic(CONTRACT, contract)
    print(CONTRACT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
