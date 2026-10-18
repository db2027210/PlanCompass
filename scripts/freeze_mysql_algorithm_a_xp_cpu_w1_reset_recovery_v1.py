#!/usr/bin/env python3
"""Freeze Algorithm A XP/CPU w1 reset recovery contract."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FAILED = ROOT / "history/mysql_four_scene_20260809/v101_to_a_xp_cpu_external_diagnostic_resume_20260823_attempt001_xp_cpu/algorithm_a_state.json"
READBACK = ROOT / "history/mysql_four_scene_20260809/v131_to_a_xp_cpu_w1_reset_recovery_20260824/recovery_readback.json"
CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_xp_cpu_w1_reset_recovery_20260824_v1"
CONTRACT = CONFIG / "transition_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    failed = json.loads(FAILED.read_text(encoding="utf-8"))
    readback = json.loads(READBACK.read_text(encoding="utf-8"))
    checkpoint = failed.get("round_checkpoint") or {}
    if (
        failed.get("status") != "FAILED_CLOSED"
        or int(failed.get("accepted") or 0) != 2
        or int(failed.get("round_index") or 0) != 3
        or list(checkpoint.get("attempted_ranks") or []) != list(range(1, 79))
        or readback.get("status") != "PASS"
        or readback.get("failed_worker") != "xp_cpu_w1"
    ):
        raise RuntimeError("XP/CPU recovery evidence differs")
    bindings = {
        "controller": ROOT / "scripts/run_mysql_algorithm_a_xp_cpu_w1_reset_external_diagnostic_resume_v1.py",
        "recovery_capture": ROOT / "scripts/capture_mysql_algorithm_a_xp_cpu_w1_reset_recovery_v1.py",
        "partial_round_resume": ROOT / "scripts/run_mysql_algorithm_a_partial_round_codefix_resume_v2.py",
        "diagnostic_gate": ROOT / "scripts/mysql_external_workload_diagnostic_gate_v1.py",
        "parallel_policy": ROOT / "scripts/mysql_algorithm_a_all_scene_parallel_policy_v1.py",
        "external_diagnostic_preflight": ROOT / "scripts/preflight_mysql_four_scene_external_containers_diagnostic_only_v2.py",
    }
    atomic(CONTRACT, {
        "schema_version": "mysql_algorithm_a_xp_cpu_w1_reset_recovery_contract_v1",
        "release_version": "v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "scene": "hybench_xp_cpu",
        "physical_replay_parallelism": 2,
        "external_workloads_diagnostic_only": True,
        "external_workload_wait_required": False,
        "external_cpu_disjoint_containers_diagnostic_only": True,
        "cached_completed_candidate_prefix_reused": True,
        "completed_candidate_measurements_replayed": False,
        "failed_wave_credited": False,
        "new_physical_work_starts_after_rank": 78,
        "native_xp_workload_unchanged": True,
        "mysql_fixed_cpu_affinity_retained": True,
        "numa_exclusive_scheduler_lock_required": False,
        "predecessor": {
            "path": relative(FAILED), "sha256": sha(FAILED),
            "status": "FAILED_CLOSED", "stage": "FAILED",
            "accepted": 2, "round_index": 3,
            "cached_attempted_rank_first": 1,
            "cached_attempted_rank_last": 78,
        },
        "recovery_readback": {"path": relative(READBACK), "sha256": sha(READBACK)},
        "source_bindings": {
            name: {"path": relative(path), "sha256": sha(path)}
            for name, path in bindings.items()
        },
    })
    print(json.dumps({"contract": str(CONTRACT), "sha256": sha(CONTRACT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
