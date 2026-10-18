#!/usr/bin/env python3
"""Freeze Top4-B v40 for launch-bound C sequence gates."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v39"
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v40"
CONTRACT = OUTPUT / "algorithm_b_top4_contract.json"
COORDINATION = OUTPUT / "coordination_contract.json"
HISTORY = ROOT / "history/mysql_four_scene_20260809"


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
    OUTPUT.mkdir(parents=True, mode=0o700)
    atomic(COORDINATION, load(PREDECESSOR / "coordination_contract.json"))
    contract = load(PREDECESSOR / "algorithm_b_top4_contract.json")
    contract.update({
        "released_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "predecessor_release": str(PREDECESSOR.relative_to(ROOT)),
        "algorithm_c_sequence_gate_mode": "launch_bound_terminal_state_sha256",
        "previous_strategy_results_eligible_for_v40_credit": False,
    })
    bindings = copy.deepcopy(contract.get("source_bindings") or {})
    paths = {
        "controller": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v33.py",
        "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v30.py",
        "scene_launcher": ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v22.py",
        "top4_runtime_source": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v1.py",
        "outer_rejection_cycle_policy": ROOT / "scripts/mysql_outer_rejection_cycle_v1.py",
        "xp_four_worker_authority": ROOT / "scripts/run_mysql_xp_four_worker_authority_v1.py",
        "xp_four_worker_reset": ROOT / "scripts/reset_mysql_xp_data_planes_four_worker_v1.py",
        "coordination_contract": COORDINATION,
    }
    bindings.update({name: binding(path) for name, path in paths.items()})
    contract["source_bindings"] = bindings
    atomic(CONTRACT, contract)
    for suffix in ("cpu", "io"):
        root = HISTORY / f"v161_to_b_top4_fresh_bootstrap_20260826_v40_{suffix}"
        root.mkdir(parents=True, mode=0o700)
        atomic(root / "bootstrap_manifest.json", {
            "schema_version": "mysql_algorithm_b_xp_top4_fresh_bootstrap_v1",
            "status": "FROZEN",
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "scene": f"hybench_xp_{suffix}",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "fresh_baseline_required": True,
            "fresh_calibration_replays_required": 15,
            "historical_calibration_or_outer_credit": False,
            "algorithm_c_state_or_result_credit": False,
            "release_contract_path": str(CONTRACT),
            "release_contract_sha256": sha(CONTRACT),
        })
    print(CONTRACT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
