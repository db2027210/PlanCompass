#!/usr/bin/env python3
"""Freeze v77: isolate a capped lane without discarding valid siblings."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v28"
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v29"
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
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def binding(path: str) -> dict[str, str]:
    target = ROOT / path
    if not target.is_file() or target.is_symlink():
        raise RuntimeError(f"regular binding required: {target}")
    return {"path": path, "sha256": sha(target)}


def refresh(raw: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in raw.items():
        row = dict(value)
        target = ROOT / str(row.get("path") or "")
        if target.is_file() and not target.is_symlink():
            row["sha256"] = sha(target)
        result[str(name)] = row
    return result


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"fresh release directory required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)

    optimization = load(SOURCE / "outer_runtime_optimization.json")
    optimization.update({"created_at": now(), "status": "FROZEN_RUNTIME_VALIDATED"})
    optimization_path = OUTPUT / "outer_runtime_optimization.json"
    atomic(optimization_path, optimization)

    parent = load(SOURCE / "algorithm_c_async_contract.json")
    parent.update({"created_at": now(), "status": "FROZEN_RUNTIME_VALIDATED"})
    sources = refresh(parent.get("source_bindings") or {})
    sources.update({
        "controller": binding("scripts/run_mysql_algorithm_c_async_scene_v77.py"),
        "launcher": binding("scripts/launch_mysql_algorithm_c_async_v77.py"),
        "mixed_stability_cap_isolation": binding(
            "scripts/mysql_algorithm_c_mixed_stability_cap_isolation_v1.py"
        ),
        "release_builder": binding(
            "scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v2.py"
        ),
        "outer_runtime_optimization": binding(str(optimization_path.relative_to(ROOT))),
    })
    parent["source_bindings"] = sources
    parent_path = OUTPUT / "algorithm_c_async_contract.json"
    atomic(parent_path, parent)

    contract = load(SOURCE / "global_monotonic_incumbent_contract.json")
    contract.update({
        "release_version": "v77",
        "created_at": now(),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "parent_contract": binding(str(parent_path.relative_to(ROOT))),
        "stability_cap_isolation": {
            "classification": "CANDIDATE_STABILITY_CAP_EXCLUDED",
            "frozen_maximum_measurement_seconds": 900,
            "failed_lane_only_excluded": True,
            "valid_sibling_measurements_retained": True,
            "accepted_or_rejected_credit_for_excluded_lane": 0,
            "candidate_and_outer_policy_changed": False,
            "workload_and_resource_freeze_changed": False,
        },
    })
    global_sources = refresh(contract.get("source_bindings") or {})
    global_sources.update({
        "controller": binding("scripts/run_mysql_algorithm_c_async_scene_v77.py"),
        "launcher": binding("scripts/launch_mysql_algorithm_c_async_v77.py"),
        "mixed_stability_cap_isolation": binding(
            "scripts/mysql_algorithm_c_mixed_stability_cap_isolation_v1.py"
        ),
        "parent_contract": binding(str(parent_path.relative_to(ROOT))),
        "parent_optimization_contract": binding(
            str(optimization_path.relative_to(ROOT))
        ),
        "release_builder": binding(
            "scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v2.py"
        ),
    })
    contract["source_bindings"] = global_sources
    contract_path = OUTPUT / "global_monotonic_incumbent_contract.json"
    atomic(contract_path, contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "global_contract": str(contract_path),
        "global_sha256": sha(contract_path),
        "parent_sha256": sha(parent_path),
        "optimization_sha256": sha(optimization_path),
        "freeze_sha256": sha(FREEZE),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
