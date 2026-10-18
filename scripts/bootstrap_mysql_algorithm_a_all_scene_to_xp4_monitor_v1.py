#!/usr/bin/env python3
"""Wait for the JOB4 authority, freeze XP4 handoff, then become its monitor."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_all_scene_to_xp4_20260823_v1"
BOOTSTRAP_CONTRACT = CONFIG / "bootstrap_contract.json"
TRANSITION_CONTRACT = CONFIG / "transition_contract.json"
SOURCE_FREEZE = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v9"
    / "job_four_worker_resource_successor.json"
)
UPSTREAM_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v9"
    / "transition_contract.json"
)
MONITOR = ROOT / "scripts/monitor_mysql_algorithm_a_all_scene_to_xp4_v1.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular frozen file required: {path}")
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
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def validate_bootstrap_contract() -> dict[str, Any]:
    contract = load(BOOTSTRAP_CONTRACT)
    if (
        contract.get("schema_version")
        != "mysql_algorithm_a_all_scene_to_xp4_bootstrap_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("memory_scenes_complete_before_handoff") is not True
        or contract.get("xp_parallelism") != {"before": 2, "after": 4}
        or contract.get("cpu_pinning_retained") is not True
        or contract.get("numa_exclusive_scheduler_lock_required") is not False
        or contract.get("historical_accepted_rounds_preserved") is not True
        or contract.get("partial_round_credited") is not False
    ):
        raise RuntimeError("all-scene XP4 bootstrap contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"all-scene XP4 bootstrap source differs: {path}")
    return contract


def validate_source_freeze(source: Mapping[str, Any]) -> None:
    workers = {
        str(row.get("worker_id") or ""): row for row in source.get("workers") or []
    }
    required = {
        "job_w1", "job_w2", "job_w3", "job_w4",
        "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
    }
    if (
        source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
        or not required.issubset(workers)
        or any(workers[name].get("scene") != "job_memory" for name in required if name.startswith("job_"))
        or any(workers[name].get("scene") != "hybench_xp_cpu" for name in required if name.startswith("xp_cpu_"))
        or any(workers[name].get("scene") != "hybench_xp_io" for name in required if name.startswith("xp_io_"))
    ):
        raise RuntimeError("generated JOB4 source freeze differs")


def build_transition_contract(bootstrap: Mapping[str, Any]) -> dict[str, Any]:
    source = load(SOURCE_FREEZE)
    validate_source_freeze(source)
    bindings = dict(bootstrap.get("transition_source_bindings") or {})
    for binding in bindings.values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"XP4 transition source differs: {path}")
    return {
        "schema_version": "mysql_algorithm_a_job_to_xp_slot_contract_v1",
        "status": "FROZEN",
        "release_version": "all_scene_parallel_xp4_v1",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "contract": "mysql_algorithm_a_job_four_slots_to_xp_2_to_4_v1",
        "trigger_requires_job_complete_10": True,
        "tpch_complete_10_required": True,
        "both_memory_scenes_complete_before_handoff": True,
        "job_nonconvergence_does_not_release_slots": True,
        "xp_parallelism": {"before": 2, "after": 4},
        "released_slot_mapping": {
            "job_w1": "xp_cpu_w3", "job_w2": "xp_cpu_w4",
            "job_w3": "xp_io_w3", "job_w4": "xp_io_w4",
        },
        "per_scene_independent_transition": True,
        "xp_cpu_does_not_wait_for_xp_io": True,
        "xp_io_does_not_wait_for_xp_cpu": True,
        "round_boundary_required": True,
        "partial_next_round_archived_and_not_credited": True,
        "mixed_worker_cohort_round_prohibited": True,
        "accepted_ranges_incumbent_history_preserved": True,
        "historical_accepted_rounds_preserved": True,
        "canonical_baseline_preserved": True,
        "fresh_baseline_repetitions_per_worker": 2,
        "incumbent_replay_required": True,
        "resource_recalibration_required": False,
        "post_convergence_stability_required": False,
        "native_xp_workload_unchanged": True,
        "cpu_pinning_retained": True,
        "numa_exclusive_scheduler_lock_required": False,
        "effective_numa_and_memory_values_read_back": True,
        "rolling_dynamic_noninterference_gate_retained": True,
        "postgresql_process_control_or_stop_prohibited": True,
        "source_resource_freeze": {
            "path": str(SOURCE_FREEZE.relative_to(ROOT)),
            "sha256": sha(SOURCE_FREEZE),
        },
        "upstream_job4_transition_contract": {
            "path": str(UPSTREAM_CONTRACT.relative_to(ROOT)),
            "sha256": sha(UPSTREAM_CONTRACT),
        },
        "source_bindings": bindings,
    }


def run(args: argparse.Namespace) -> int:
    bootstrap = validate_bootstrap_contract()
    output = args.output.resolve()
    while not SOURCE_FREEZE.is_file():
        atomic(output, {
            "schema_version": "mysql_algorithm_a_all_scene_to_xp4_bootstrap_v1",
            "status": "WAITING_FOR_JOB4_RESOURCE_AUTHORITY",
            "updated_at": now(), "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "source_freeze": str(SOURCE_FREEZE),
            "monitor": str(MONITOR),
            "cpu_pinning_retained": True,
            "numa_exclusive_scheduler_lock_required": False,
        })
        time.sleep(args.poll_seconds)
    transition = build_transition_contract(bootstrap)
    if TRANSITION_CONTRACT.is_file():
        existing = load(TRANSITION_CONTRACT)
        source_binding = existing.get("source_resource_freeze") or {}
        if (
            existing.get("schema_version")
            != "mysql_algorithm_a_job_to_xp_slot_contract_v1"
            or existing.get("status") != "FROZEN"
            or existing.get("formal_scope") != "mysql_only"
            or existing.get("cross_engine_comparison_prohibited") is not True
            or existing.get("contract")
            != "mysql_algorithm_a_job_four_slots_to_xp_2_to_4_v1"
            or source_binding.get("path")
            != str(SOURCE_FREEZE.relative_to(ROOT))
            or source_binding.get("sha256") != sha(SOURCE_FREEZE)
            or existing.get("source_bindings")
            != transition.get("source_bindings")
        ):
            raise RuntimeError("existing XP4 transition contract differs")
    else:
        atomic(TRANSITION_CONTRACT, transition)
    atomic(output, {
        "schema_version": "mysql_algorithm_a_all_scene_to_xp4_bootstrap_v1",
        "status": "EXECUTING_COMPLETION_MONITOR",
        "updated_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "transition_contract": str(TRANSITION_CONTRACT),
        "transition_contract_sha256": sha(TRANSITION_CONTRACT),
        "monitor": str(MONITOR),
    })
    os.execv(str(PYTHON), [
        str(PYTHON), str(MONITOR), "--output", str(output),
        "--poll-seconds", str(args.poll_seconds),
    ])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()
    if not 5 <= args.poll_seconds <= 300:
        parser.error("poll seconds must be in [5, 300]")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
