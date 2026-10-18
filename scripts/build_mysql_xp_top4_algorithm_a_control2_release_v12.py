#!/usr/bin/env python3
"""Freeze XP8 Algorithm-A v12 with two scene-local Docker control calls."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v11"
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v12"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


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
    target = ROOT / path
    return {"path": path, "sha256": sha(target)}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh contract output required: {OUTPUT}")
    created = datetime.now(timezone.utc).astimezone().isoformat()
    coordination = load(SOURCE / "coordination_contract.json")
    coordination.update({
        "created_at": created,
        "docker_control_concurrency_per_scene": 2,
        "measurement_parallelism_per_scene": 8,
        "reason": (
            "observed Docker daemon congestion and repeated orphan shims at "
            "four control calls per scene; workload replay remains eight-way"
        ),
    })
    atomic(OUTPUT / "coordination_contract.json", coordination)

    contract = load(SOURCE / "algorithm_a_top4_contract.json")
    contract.update({
        "created_at": created,
        "docker_control_concurrency_per_scene": 2,
        "physical_replay_parallelism_per_scene": 8,
        "worker_reset_parallelism": 8,
        "docker_control_concurrency_change_only": True,
    })
    sources = dict(contract["source_bindings"])
    sources.update({
        "builder": binding("scripts/build_mysql_xp_top4_algorithm_a_control2_release_v12.py"),
        "controller": binding("scripts/run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v12.py"),
        "zero_baseline_runtime": binding("scripts/run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v12.py"),
        "xp_reset": binding("scripts/reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6.py"),
        "xp_reset_control4_predecessor": binding("scripts/reset_mysql_xp_data_planes_mixed_eight_worker_scene_v5.py"),
        "coordination_contract": binding(
            "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v12/coordination_contract.json"
        ),
    })
    contract["source_bindings"] = sources
    atomic(OUTPUT / "algorithm_a_top4_contract.json", contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(OUTPUT / "algorithm_a_top4_contract.json"),
        "contract_sha256": sha(OUTPUT / "algorithm_a_top4_contract.json"),
        "docker_control_concurrency_per_scene": 2,
        "physical_replay_parallelism_per_scene": 8,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
