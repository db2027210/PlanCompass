#!/usr/bin/env python3
"""Freeze general asynchronous Algorithm B with QPS-only outer progress."""

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
    ROOT / "config/formal/frozen_mysql_async_outer_20260819_v36"
    / "algorithm_b_async_contract.json"
)
OUTPUT = ROOT / "config/formal/frozen_mysql_async_outer_20260826_v38"
CONTRACT = OUTPUT / "algorithm_b_async_contract.json"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_async_scene_v37.py"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_b_qps_only_scene_v1.py"
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
        raise RuntimeError(f"refusing to overwrite frozen release: {OUTPUT}")
    from scripts import mysql_outer_qps_strict_progress_v1 as policy

    contract = load(PREDECESSOR)
    contract.update({
        "release_version": "v38",
        "released_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "outer_acceptance_policy": policy.policy_contract(),
        "outer_acceptance_policy_changed": True,
        "inner_candidate_generation_changed": False,
        "inner_candidate_ranking_changed": False,
        "algorithm_a_changed": False,
        "previous_strategy_results_eligible_for_v38_credit": False,
        "algorithm_c_v60_sequence_gate_required": True,
    })
    bindings = copy.deepcopy(contract.get("source_bindings") or {})
    bindings.update({
        "predecessor_controller_v36": binding(
            ROOT / "scripts/run_mysql_algorithm_b_async_scene_v36.py"
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
