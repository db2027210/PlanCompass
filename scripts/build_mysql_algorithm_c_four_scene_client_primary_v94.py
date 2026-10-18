#!/usr/bin/env python3
"""Freeze v94 with mixed JOB/TPC-H client-primary batch support."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PARENT_ROOT = ROOT / (
    "config/formal/"
    "frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v93"
)
OUTPUT_ROOT = ROOT / (
    "config/formal/"
    "frozen_mysql_four_scene_algorithm_c_client_primary_20260904_v94"
)
RELEASE = "v94-memory-pair-client-primary"
EXTRA = {
    "builder_v94": "scripts/build_mysql_algorithm_c_four_scene_client_primary_v94.py",
    "controller_v94": "scripts/run_mysql_algorithm_c_four_scene_client_primary_v5.py",
    "measurement_adapter_v94": "scripts/mysql_algorithm_c_measurement_client_primary_v3.py",
    "recovery_supervisor_v94": "scripts/recover_mysql_algorithm_c_staged_v94.py",
    "scene_authority_refresh_v94": "scripts/refresh_mysql_client_primary_scene_authority_v1.py",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic(path: Path, value: dict[str, Any]) -> None:
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


def binding(relative: str) -> dict[str, str]:
    path = ROOT / relative
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"v94 source binding absent: {path}")
    return {"path": relative, "sha256": sha(path)}


def main() -> int:
    if OUTPUT_ROOT.exists() or OUTPUT_ROOT.is_symlink():
        raise RuntimeError(f"fresh v94 contract root required: {OUTPUT_ROOT}")
    created = datetime.now().astimezone().isoformat()
    additions = {name: binding(path) for name, path in EXTRA.items()}
    release_policy = {
        "all_four_scenes_declared_in_same_formal_campaign": True,
        "all_four_scenes_started_together": False,
        "per_scene_b_completion_release": True,
        "completed_scene_early_release_allowed": True,
        "scene_release_policy": "after_same_scene_algorithm_b_complete",
        "physical_workers_per_scene": 2,
        "maximum_concurrent_mysql_workers": 8,
        "four_scene_wave_barrier": False,
        "sibling_scene_wait": False,
        "lease_mode": "none",
    }

    parent = load(PARENT_ROOT / "algorithm_c_async_contract.json")
    parent.update({
        "created_at": created,
        "release_version": RELEASE,
        **release_policy,
        "mixed_memory_scene_batch_supported": True,
        "mixed_memory_scene_batch_semantics": (
            "rebind_and_normalize_only_tpch_rows_preserve_job_rows"
        ),
        "successor_reason": (
            "v93_failed_before_measurement_because_tpch_adapter_rejected_"
            "a_valid_job_tpch_memory_pair_batch"
        ),
    })
    parent["source_bindings"].update(additions)
    parent_path = OUTPUT_ROOT / "algorithm_c_async_contract.json"
    atomic(parent_path, parent)

    optimization = load(PARENT_ROOT / "outer_runtime_optimization.json")
    optimization.update({
        "created_at": created,
        "release_version": RELEASE,
        "mixed_memory_scene_batch_supported": True,
    })
    algorithm_source = ROOT / str(optimization["bindings"]["algorithm_source_path"])
    optimization["bindings"]["algorithm_source_sha256"] = sha(algorithm_source)
    atomic(OUTPUT_ROOT / "outer_runtime_optimization.json", optimization)

    successor = load(PARENT_ROOT / "global_monotonic_incumbent_contract.json")
    successor.update({
        "created_at": created,
        "release_version": RELEASE,
        "parent_contract": {"path": str(parent_path), "sha256": sha(parent_path)},
        "outer_runtime_optimization": {
            "path": str(OUTPUT_ROOT / "outer_runtime_optimization.json"),
            "sha256": sha(OUTPUT_ROOT / "outer_runtime_optimization.json"),
        },
        "full_parallel_launch": release_policy,
        "mixed_memory_scene_batch_supported": True,
        "mixed_memory_scene_batch_semantics": (
            "rebind_and_normalize_only_tpch_rows_preserve_job_rows"
        ),
        "successor_reason": parent["successor_reason"],
    })
    successor["source_bindings"].update(additions)
    atomic(OUTPUT_ROOT / "global_monotonic_incumbent_contract.json", successor)
    print(json.dumps({
        "status": "PASS",
        "release_version": RELEASE,
        "contract_root": str(OUTPUT_ROOT),
        "parent_sha256": sha(parent_path),
        "optimization_sha256": sha(OUTPUT_ROOT / "outer_runtime_optimization.json"),
        "successor_sha256": sha(
            OUTPUT_ROOT / "global_monotonic_incumbent_contract.json"
        ),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
