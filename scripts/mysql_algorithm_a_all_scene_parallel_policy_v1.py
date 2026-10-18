#!/usr/bin/env python3
"""Frozen two-workers-per-scene Algorithm A concurrency policy."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_all_scene_parallel_20260823_v1"
    / "parallel_contract.json"
)
SCENES = (
    "job_memory",
    "tpch_sf4_memory",
    "hybench_xp_cpu",
    "hybench_xp_io",
)
PARALLELISM = {scene: 2 for scene in SCENES}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular frozen file required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def validate_contract() -> dict[str, Any]:
    contract = load(CONTRACT)
    coordination = contract.get("coordination") or {}
    transition = contract.get("transition") or {}
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_all_scene_parallel_contract_v1"
        or contract.get("release_version") != "v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("concurrent_scenes") != list(SCENES)
        or contract.get("physical_replay_parallelism_per_scene") != PARALLELISM
        or int(contract.get("total_concurrent_mysql_workers") or 0) != 8
        or contract.get("accepted_target") != 10
        or contract.get("post_convergence_stability_required") is not False
        or coordination.get("fixed_disjoint_cpu_affinity_required") is not True
        or coordination.get("numa_exclusive_scheduler_lock_required") is not False
        or coordination.get("effective_numa_and_memory_values_read_back") is not True
        or coordination.get("memory_scene_shared_nvme_mutual_exclusion") is not False
        or coordination.get("mysql_sibling_wave_wait_required") is not False
        or coordination.get("cross_project_shared_mysql_lease_retained") is not True
        or coordination.get("rolling_dynamic_noninterference_gate_required") is not True
        or transition.get("completed_accepted_rounds_reused") is not True
        or transition.get("completed_candidate_measurements_replayed") is not False
        or transition.get("incomplete_wave_credited") is not False
        or transition.get("resource_recalibration_required") is not False
        or transition.get("candidate_or_outer_loop_policy_changed") is not False
    ):
        raise RuntimeError("Algorithm A all-scene parallel contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"parallel policy source differs: {path}")
    return contract


def annotate_state(state: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(state))
    value["all_scene_parallel_execution"] = {
        "contract_path": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "concurrent_scenes": list(SCENES),
        "physical_replay_parallelism_per_scene": copy.deepcopy(PARALLELISM),
        "total_concurrent_mysql_workers": 8,
        "fixed_disjoint_cpu_affinity_required": True,
        "numa_exclusive_scheduler_lock_required": False,
        "memory_scene_shared_nvme_mutual_exclusion": False,
        "mysql_sibling_wave_wait_required": False,
        "rolling_dynamic_noninterference_gate_required": True,
    }
    value["other_scene_completion_required"] = False
    value["four_scene_wave_barrier"] = False
    value["scene_may_advance_immediately"] = True
    value.pop("shared_storage_lease", None)
    return value


__all__ = [
    "CONTRACT", "PARALLELISM", "SCENES", "annotate_state", "validate_contract",
]
