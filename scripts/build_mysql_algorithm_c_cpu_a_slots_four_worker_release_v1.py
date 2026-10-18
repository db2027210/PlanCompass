#!/usr/bin/env python3
"""Freeze v76: four-worker XP C on current mixed idle CPU-A slots."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v27"
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v28"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3"
    / "four_scene_effective_resource_successor.json"
)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


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


def binding(path: str) -> dict[str, str]:
    target = ROOT / path
    if not target.is_file() or target.is_symlink():
        raise RuntimeError(f"regular binding required: {target}")
    return {"path": path, "sha256": sha(target)}


def refresh_bindings(raw: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in raw.items():
        row = dict(value)
        path = str(row.get("path") or "")
        target = ROOT / path
        if target.is_file() and not target.is_symlink():
            row["sha256"] = sha(target)
        result[str(name)] = row
    return result


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"fresh release directory required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    optimization = load(SOURCE / "outer_runtime_optimization.json")
    optimization.update({
        "schema_version": "mysql_algorithm_c_cpu_a_slots_four_worker_optimization_v1",
        "created_at": now(), "status": "FROZEN_RUNTIME_VALIDATED",
    })
    optimization["bindings"].update({
        "algorithm_source_path": "scripts/run_mysql_four_scene_algorithm_c_v2.py",
        "algorithm_source_sha256": sha(ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py"),
        "reset_source_path": "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v2.py",
        "reset_source_sha256": sha(ROOT / "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v2.py"),
        "resource_freeze_path": str(FREEZE.relative_to(ROOT)),
        "resource_freeze_sha256": sha(FREEZE),
    })
    optimization["candidate_batches"] = [[1, 2, 3, 4], [5]]
    optimization["physical_replay_parallelism_per_xp_scene"] = 4
    optimization_path = OUTPUT / "outer_runtime_optimization.json"
    atomic(optimization_path, optimization)

    parent = load(SOURCE / "algorithm_c_async_contract.json")
    parent.update({
        "created_at": now(), "status": "FROZEN_RUNTIME_VALIDATED",
        "candidate_batches": [[1, 2, 3, 4], [5]],
        "physical_replay_parallelism_per_scene": 4,
        "maximum_concurrent_mysql_workers": 12,
        "four_scene_wave_barrier": False, "sibling_scene_wait": False,
        "lease_mode": "none",
    })
    sources = refresh_bindings(parent.get("source_bindings") or {})
    sources.update({
        "controller": binding("scripts/run_mysql_algorithm_c_async_scene_v76.py"),
        "launcher": binding("scripts/launch_mysql_algorithm_c_async_v76.py"),
        "four_scene_four_worker_resource_freeze": binding(str(FREEZE.relative_to(ROOT))),
        "four_worker_freeze_builder": binding("scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_freeze_v1.py"),
        "release_builder": binding("scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v1.py"),
        "shared_algorithm_core": binding("scripts/run_mysql_four_scene_algorithm_c_v2.py"),
        "outer_runtime_optimization": binding(str(optimization_path.relative_to(ROOT))),
    })
    sources.pop("four_scene_two_worker_resource_freeze", None)
    parent["source_bindings"] = sources
    parent_path = OUTPUT / "algorithm_c_async_contract.json"
    atomic(parent_path, parent)

    global_contract = load(SOURCE / "global_monotonic_incumbent_contract.json")
    global_contract.update({
        "release_version": "v76", "created_at": now(),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "parent_contract": binding(str(parent_path.relative_to(ROOT))),
    })
    global_contract["parallel_execution"].update({
        "start_all_four_scenes_concurrently": True,
        "physical_workers_per_scene_initial": 4,
        "maximum_concurrent_mysql_workers_initial": 12,
        "four_scene_wave_barrier": False, "sibling_scene_wait": False,
        "shared_nvme_lease": False, "scene_may_advance_after_own_round": True,
    })
    global_sources = refresh_bindings(global_contract.get("source_bindings") or {})
    global_sources.update({
        "controller": binding("scripts/run_mysql_algorithm_c_async_scene_v76.py"),
        "launcher": binding("scripts/launch_mysql_algorithm_c_async_v76.py"),
        "parent_contract": binding(str(parent_path.relative_to(ROOT))),
        "parent_optimization_contract": binding(str(optimization_path.relative_to(ROOT))),
        "four_scene_four_worker_resource_freeze": binding(str(FREEZE.relative_to(ROOT))),
        "release_builder": binding("scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v1.py"),
    })
    global_sources.pop("four_scene_two_worker_resource_freeze", None)
    global_contract["source_bindings"] = global_sources
    global_path = OUTPUT / "global_monotonic_incumbent_contract.json"
    atomic(global_path, global_contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "global_contract": str(global_path), "global_sha256": sha(global_path),
        "parent_sha256": sha(parent_path), "optimization_sha256": sha(optimization_path),
        "freeze_sha256": sha(FREEZE),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
