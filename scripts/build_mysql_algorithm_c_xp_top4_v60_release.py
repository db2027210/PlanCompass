#!/usr/bin/env python3
"""Freeze the four-scene Algorithm-C QPS-only successor."""

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
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v5"
    / "algorithm_c_async_contract.json"
)
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260826_v6"
CONTRACT = OUTPUT / "algorithm_c_async_contract.json"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_v60.py"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_async_v60.py"
POLICY = ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py"


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
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    if OUTPUT.exists() and any(OUTPUT.iterdir()):
        existing = load(CONTRACT) if CONTRACT.is_file() else {}
        if existing.get("release_version") != "v60":
            raise RuntimeError(f"refusing to overwrite frozen release: {OUTPUT}")
    from scripts import mysql_outer_qps_strict_progress_v1 as policy

    contract = load(PREDECESSOR)
    contract.update({
        "release_version": "v60",
        "released_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "outer_acceptance_policy": policy.policy_contract(),
        "outer_acceptance_policy_changed": True,
        "inner_candidate_generation_changed": False,
        "inner_candidate_ranking_changed": False,
        "mysql_interface_changed": False,
        "algorithm_a_changed": False,
        "previous_strategy_results_eligible_for_v60_credit": False,
        "fresh_search_required": True,
        "four_scene_wave_barrier": False,
        "per_scene_outer_state_machine": True,
        "scene_may_advance_immediately": True,
        "other_scene_completion_required": False,
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "post_search_stability_confirmation": False,
    })
    bindings = copy.deepcopy(contract.get("source_bindings") or {})
    bindings.update({
        "predecessor_controller_v59": binding(
            ROOT / "scripts/run_mysql_algorithm_c_async_scene_v59.py"
        ),
        "controller": binding(CONTROLLER),
        "launcher": binding(LAUNCHER),
        "qps_only_outer_policy": binding(POLICY),
    })
    contract["source_bindings"] = bindings
    atomic(CONTRACT, contract)
    print(CONTRACT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
