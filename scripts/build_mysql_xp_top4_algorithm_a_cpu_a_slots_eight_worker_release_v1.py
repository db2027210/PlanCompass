#!/usr/bin/env python3
"""Freeze current eight-worker XP Top4 Algorithm-A runtime."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_cpu_a_slots_four_worker_20260828_v13"
    / "algorithm_a_top4_contract.json"
)
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_cpu_a_slots_eight_worker_20260828_v15"
)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_eight_worker_cpu_a_slots_20260828_v6"
    / "xp_effective_resource_successor.json"
)
SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_cpu_a_slots_eight_worker_v1.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_cpu_a_slots_eight_worker_scene_v1.py",
    "authority_refresh": ROOT / "scripts/refresh_mysql_xp8_cpu_a_slots_live_authority_from_reset_v1.py",
    "resource_freeze": FREEZE,
    "reset": ROOT / "scripts/reset_mysql_xp_data_planes_mixed_eight_worker_scene_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v17.py",
    "full_factorial": ROOT / "scripts/mysql_algorithm_a_full_factorial_v1.py",
    "pg_aligned_core": ROOT / "scripts/run_mysql_algorithm_a_async_scene_v3.py",
}


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


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh release required: {OUTPUT}")
    if any(not path.is_file() or path.is_symlink() for path in SOURCES.values()):
        raise RuntimeError("current XP8 A source binding absent")
    contract = copy.deepcopy(load(PREDECESSOR))
    contract.update({
        "schema_version": "mysql_algorithm_a_xp_top4_async_contract_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": now(),
        "candidate_batches": [[1, 2, 3, 4, 5, 6, 7, 8]],
        "physical_replay_parallelism_per_scene": 8,
        "maximum_concurrent_xp_mysql_workers": 16,
        "parallel_expansion_policy": "eight_physical_workers_per_scene_at_each_wave_boundary",
        "resource_envelope_version": "cpu_a_slots_eight_worker_20260828_v6",
        "resource_freeze_path": str(FREEZE.relative_to(ROOT)),
        "resource_freeze_sha256": sha(FREEZE),
        "historical_algorithm_b_or_c_credit": False,
        "prediction_used_for_ordering": False,
        "prediction_used_for_selection": False,
        "four_scene_wave_barrier": False, "sibling_scene_wait": False,
        "lease_mode": "none",
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in sorted(SOURCES.items())
        },
    })
    for scene in ("hybench_xp_cpu", "hybench_xp_io"):
        contract["scenes"][scene].update({
            "physical_workers": 8,
            "physical_waves_per_round": 2,
            "candidates_per_round": 16,
        })
    atomic(OUTPUT / "algorithm_a_top4_contract.json", contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract_sha256": sha(OUTPUT / "algorithm_a_top4_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
