#!/usr/bin/env python3
"""Freeze v78: same-lane incumbent revalidation for Algorithm C."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v29"
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v30"


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

    source_updates = {
        "controller": binding("scripts/run_mysql_algorithm_c_async_scene_v78.py"),
        "launcher": binding("scripts/launch_mysql_algorithm_c_async_v78.py"),
        "lane_paired_boundary_runtime": binding(
            "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v3.py"
        ),
        "release_builder": binding(
            "scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v3.py"
        ),
        "outer_runtime_optimization": binding(str(optimization_path.relative_to(ROOT))),
    }

    parent = load(SOURCE / "algorithm_c_async_contract.json")
    parent.update({"created_at": now(), "status": "FROZEN_RUNTIME_VALIDATED"})
    parent_sources = refresh(parent.get("source_bindings") or {})
    parent_sources.update(source_updates)
    parent["source_bindings"] = parent_sources
    parent_path = OUTPUT / "algorithm_c_async_contract.json"
    atomic(parent_path, parent)

    contract = load(SOURCE / "global_monotonic_incumbent_contract.json")
    contract.update({
        "release_version": "v78",
        "created_at": now(),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "parent_contract": binding(str(parent_path.relative_to(ROOT))),
        "lane_paired_incumbent_revalidation": {
            "schema_version": "mysql_algorithm_c_lane_paired_incumbent_revalidation_v1",
            "applies_to": [
                "job_memory", "tpch_memory", "hybench_xp_cpu", "hybench_xp_io"
            ],
            "trigger_on_apparent_acceptance": True,
            "trigger_before_second_rejection_shrink": True,
            "same_physical_worker_ratio": True,
            "decision_formula": (
                "global_incumbent_db_qps * candidate_raw_db_qps / "
                "same_lane_incumbent_db_qps"
            ),
            "raw_qps_preserved": True,
            "control_measurements_grant_no_state_credit": True,
            "accepted_qps_must_strictly_exceed_previous_incumbent": True,
            "workload_or_resource_freeze_changed": False,
            "completed_scene_replay_required": False,
        },
    })
    global_sources = refresh(contract.get("source_bindings") or {})
    global_sources.update(source_updates)
    global_sources.update({
        "parent_contract": binding(str(parent_path.relative_to(ROOT))),
        "parent_optimization_contract": binding(
            str(optimization_path.relative_to(ROOT))
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
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
