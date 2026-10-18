#!/usr/bin/env python3
"""Freeze Algorithm-A v13 after verified shared-runtime successors."""

from __future__ import annotations

from datetime import datetime, timezone
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v12"
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260828_v13"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return copy.deepcopy(dict(value))


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def binding(path: str) -> dict[str, str]:
    return {"path": path, "sha256": sha(ROOT / path)}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh contract output required: {OUTPUT}")
    created = datetime.now(timezone.utc).astimezone().isoformat()
    coordination = load(SOURCE / "coordination_contract.json")
    coordination.update({
        "created_at": created,
        "successor_reason": "refresh_current_post_c_shared_runtime_bindings_only",
        "algorithm_flow_change": False,
    })
    atomic(OUTPUT / "coordination_contract.json", coordination)
    contract = load(SOURCE / "algorithm_a_top4_contract.json")
    contract.update({
        "created_at": created,
        "source_binding_successor": "post_c_current_runtime_v13",
        "algorithm_flow_change": False,
        "candidate_set_change": False,
        "acceptance_policy_change": False,
        "resource_envelope_change": False,
    })
    sources = dict(contract["source_bindings"])
    sources.update({
        "builder": binding(
            "scripts/build_mysql_xp_top4_algorithm_a_post_c_bindings_v13.py"
        ),
        "controller": binding(
            "scripts/run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v13.py"
        ),
        "zero_baseline_runtime": binding(
            "scripts/run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v13.py"
        ),
        "shared_measurement_core": binding(
            "scripts/run_mysql_four_scene_algorithm_c_v2.py"
        ),
        "xp_reset_run_scoped": binding(
            "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v3.py"
        ),
        "coordination_contract": binding(
            "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260828_v13/coordination_contract.json"
        ),
    })
    contract["source_bindings"] = sources
    atomic(OUTPUT / "algorithm_a_top4_contract.json", contract)
    print(OUTPUT / "algorithm_a_top4_contract.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
