#!/usr/bin/env python3
"""Freeze memory-scene Algorithm-B v39 outer semantics."""

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
    ROOT / "config/formal/frozen_mysql_async_outer_20260826_v38"
    / "algorithm_b_async_contract.json"
)
OUTPUT = ROOT / "config/formal/frozen_mysql_async_outer_20260826_v39"
CONTRACT = OUTPUT / "algorithm_b_async_contract.json"


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

    contract = load(PREDECESSOR)
    contract.update({
        "release_version": "v39",
        "released_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "outer_acceptance_policy": qps_policy.policy_contract(),
        "outer_rejection_cycle_policy": rejection_cycle.policy_contract(),
        "convergence_metric": "maximum_log_domain_width_relative_to_frozen_initial",
        "terminal_normalized_width": 0.01,
        "accepted_round_credit_from_forced_shrink": False,
        "fresh_search_required": True,
        "previous_strategy_results_eligible_for_v39_credit": False,
        "algorithm_c_v61_sequence_gate_required": True,
        "inner_candidate_generation_changed": False,
        "inner_candidate_ranking_changed": False,
        "algorithm_a_changed": False,
    })
    bindings = copy.deepcopy(contract.get("source_bindings") or {})
    paths = {
        "controller": ROOT / "scripts/run_mysql_algorithm_b_async_scene_v38.py",
        "launcher": ROOT / "scripts/launch_mysql_algorithm_b_qps_rejection_scene_v2.py",
        "predecessor_controller_v37": ROOT / "scripts/run_mysql_algorithm_b_async_scene_v37.py",
        "qps_only_outer_policy": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
        "outer_rejection_cycle_policy": ROOT / "scripts/mysql_outer_rejection_cycle_v1.py",
        "runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_b_dynamic_support_resume_core_v27.py",
        "coordinated_measurement_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v2.py",
    }
    bindings.update({name: binding(path) for name, path in paths.items()})
    contract["source_bindings"] = bindings
    atomic(CONTRACT, contract)
    print(CONTRACT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
