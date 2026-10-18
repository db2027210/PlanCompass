#!/usr/bin/env python3
"""Publish the bounded-prestate Algorithm C runtime and coordination releases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260813_v9"
    / "optimization_contract.json"
)
NEW_OPT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v10"
    / "optimization_contract.json"
)
OLD_COORD = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v16"
    / "coordination_contract.json"
)
NEW_COORD = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v17"
    / "coordination_contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        if key.endswith("_path") and key[:-5] + "_sha256" in bindings:
            bindings[key[:-5] + "_sha256"] = sha((ROOT / str(value)).resolve())


def write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v10"
    optimization["predecessor"] = {
        "path": relative(OLD_OPT), "sha256": sha(OLD_OPT),
    }
    optimization["successor_reason"] = (
        "bounded_parallel_xp_session_drain_with_transient_probe_retry"
    )
    optimization["bounded_pre_reset_session_drain"] = {
        "total_timeout_seconds": 60,
        "probe_concurrency": 4,
        "per_probe_timeout_cap_seconds": 15,
        "probe_targets": ["xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"],
        "retryable_probe_failures": ["TimeoutExpired", "CalledProcessError"],
        "transient_probe_failure_action": "retry_within_total_bound",
        "all_workers_explicit_zero_required": True,
        "unavailable_probe_is_not_zero": True,
        "timeout_action": "fail_closed_without_candidate_state_mutation",
        "workload_or_measurement_semantics_changed": False,
    }
    bindings = optimization["bindings"]
    bindings.update({
        "session_drain_test_path": "tests/test_mysql_algorithm_c_interval_convergence.py",
        "session_drain_test_sha256": "",
        "session_drain_timeout_evidence_path": (
            "history/mysql_four_scene_20260809/"
            "v18_pruning_v2_pilot_nonformal_v16_novel5_pg_ignored_retry1_"
            "20260814T0835p0800/outer_005/top5_batch_2/batch_result.json"
        ),
        "session_drain_timeout_evidence_sha256": "",
        "session_drain_recovery_binding_path": (
            "history/mysql_four_scene_20260809/"
            "xp_v18_after_novel5_rank2_timeout_20260814T0900p0800/"
            "reset_binding.json"
        ),
        "session_drain_recovery_binding_sha256": "",
        "session_drain_resume_completion_path": (
            "history/mysql_four_scene_20260809/"
            "v18_pruning_v2_pilot_nonformal_v16_novel5_rank3_resume_"
            "pg_ignored_20260814T0905p0800/run_state.json"
        ),
        "session_drain_resume_completion_sha256": "",
    })
    rebind(bindings)
    write_new(NEW_OPT, optimization)

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {
        "path": relative(OLD_COORD), "sha256": sha(OLD_COORD),
    }
    coordination["successor_reason"] = (
        "bind_bounded_prestate_runtime_and_parallel_xp_session_drain"
    )
    rebind(coordination["source_bindings"])
    write_new(NEW_COORD, coordination)
    print(json.dumps({
        "optimization_contract": relative(NEW_OPT),
        "optimization_sha256": sha(NEW_OPT),
        "coordination_contract": relative(NEW_COORD),
        "coordination_sha256": sha(NEW_COORD),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
