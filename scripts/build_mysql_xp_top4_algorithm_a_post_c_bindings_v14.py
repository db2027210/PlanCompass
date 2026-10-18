#!/usr/bin/env python3
"""Freeze Algorithm-A v14 with the current C49 adapter."""

from __future__ import annotations

from datetime import datetime, timezone
import copy
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260828_v13"
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260828_v14"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return copy.deepcopy(json.loads(path.read_text(encoding="utf-8")))


def atomic(path: Path, value) -> None:
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


def binding(path: str):
    return {"path": path, "sha256": sha(ROOT / path)}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh contract output required: {OUTPUT}")
    created = datetime.now(timezone.utc).astimezone().isoformat()
    coordination = load(SOURCE / "coordination_contract.json")
    coordination.update({
        "created_at": created,
        "successor_reason": "current_c49_measurement_adapter_binding",
        "algorithm_flow_change": False,
    })
    atomic(OUTPUT / "coordination_contract.json", coordination)
    contract = load(SOURCE / "algorithm_a_top4_contract.json")
    contract.update({
        "created_at": created,
        "source_binding_successor": "current_c49_measurement_adapter_v14",
        "algorithm_flow_change": False,
        "candidate_set_change": False,
        "acceptance_policy_change": False,
        "resource_envelope_change": False,
    })
    sources = dict(contract["source_bindings"])
    sources.update({
        "builder": binding(
            "scripts/build_mysql_xp_top4_algorithm_a_post_c_bindings_v14.py"
        ),
        "controller": binding(
            "scripts/run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v14.py"
        ),
        "zero_baseline_runtime": binding(
            "scripts/run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v14.py"
        ),
        "c49_measurement_adapter_contract": binding(
            "config/formal/frozen_mysql_algorithm_a_c49_measurement_adapter_20260828_v2/algorithm_c_measurement_adapter_contract.json"
        ),
        "coordination_contract": binding(
            "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260828_v14/coordination_contract.json"
        ),
    })
    contract["source_bindings"] = sources
    atomic(OUTPUT / "algorithm_a_top4_contract.json", contract)
    print(OUTPUT / "algorithm_a_top4_contract.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
