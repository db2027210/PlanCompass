#!/usr/bin/env python3
"""Record the XP/CPU v237 continuation and disabled persistent monitoring."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v212_continued_four_scene_20260929"
)
MANIFEST = CAMPAIGN / "launch_manifest.json"
NEW_LAUNCH = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v237_xp_cpu_runtime_gate_recovery_20260929/launch_manifest.json"
)
AUTHORITY = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v235_xp_cpu_live_identity_rebind_20260929/authority"
)
FRESH_R0 = ROOT / (
    "history/mysql_four_scene_20260809/"
    "c_v235_xp_cpu_fresh_r0_identity_successor_20260929/fresh_r0_binding.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
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
    document = load(MANIFEST)
    launch = load(NEW_LAUNCH)
    if (
        document.get("formal_scope") != "mysql_only"
        or launch.get("release_version") != "v237-xp-cpu-runtime-gate-recovery"
        or launch.get("status") != "RUNNING"
        or int(launch.get("completed_outer_attempts_preserved") or 0) != 19
        or int(launch.get("actual_accepted_decisions_restored") or 0) != 11
        or int(launch.get("rejected_decisions_preserved") or 0) != 8
    ):
        raise RuntimeError("continued campaign or v237 launch differs")

    document["launches"]["hybench_xp_cpu"] = {
        "run_id": launch["run_id"],
        "pid": launch["pid"],
        "log": launch["log"],
        "controller": launch["controller"],
        "controller_sha256": launch["controller_sha256"],
        "controller_cpuset": launch["controller_cpuset"],
        "resumed_after_outer": 19,
        "current_pg_runtime_release": "v59",
        "container_identity_successor": True,
        "fresh_r0_identity_successor": True,
        "prestart_swappiness_reapplied_and_read_back": True,
        "post_restart_swappiness_reapplied_and_read_back": True,
        "post_restart_identity_uses_reset_result": True,
        "dynamic_gate_uses_latest_reset_result_identity": True,
        "runtime_gate_authority_is_append_only": True,
        "dynamic_gate_environment_switches_to_each_generation": True,
        "initial_startup_authority_preserved_until_install": True,
        "runtime_gate_authority_root": launch["runtime_gate_authority_root"],
    }
    document["additional_continuations"]["hybench_xp_cpu"] = {
        "continuation_run_id": launch["run_id"],
        "source_run_id": "c_v228_xp_cpu_reset_result_recovery_20260929_xp_cpu",
        "lineage_source_run_id": "c_v211f_pg_dynamic_gate_four_scene_20260929_xp_cpu",
        "checkpoint": launch["checkpoint"],
        "checkpoint_sha256": launch["checkpoint_sha256"],
        "quarantine": launch["quarantine"],
        "quarantine_sha256": launch["quarantine_sha256"],
        "completed_outer_attempts_preserved": 19,
        "actual_accepted_decisions_restored": 11,
        "rejected_decisions_preserved": 8,
        "discarded_incomplete_outer": 20,
        "discarded_incomplete_outer_credit": 0,
        "container_identity_rebind_authority": str(AUTHORITY),
        "fresh_r0_identity_successor": str(FRESH_R0),
        "fresh_search_reset_on_resume": False,
        "legacy_post_target_cap_retired": True,
        "chained_resume_entry_release": "v118",
        "current_pg_runtime_release": "v59",
        "dynamic_gate_release": "v210",
        "prestart_swappiness_reapplied_and_read_back": True,
        "post_restart_swappiness_reapplied_and_read_back": True,
        "post_restart_identity_uses_reset_result": True,
        "dynamic_gate_uses_latest_reset_result_identity": True,
        "runtime_gate_authority_is_append_only": True,
        "dynamic_gate_environment_switches_to_each_generation": True,
        "initial_startup_authority_preserved_until_install": True,
    }
    document["decision_lineage"]["hybench_xp_cpu"] = [
        {
            "from_outer": 1,
            "through_outer": 13,
            "run_id": "c_v211f_pg_dynamic_gate_four_scene_20260929_xp_cpu",
        },
        {
            "from_outer": 14,
            "through_outer": 19,
            "run_id": "c_v228_xp_cpu_reset_result_recovery_20260929_xp_cpu",
        },
        {
            "from_outer": 20,
            "through_outer": "terminal",
            "run_id": launch["run_id"],
        },
    ]
    failed = document.setdefault("failed_recovery_attempts", [])
    if not any(
        row.get("run_id") == "c_v228_xp_cpu_reset_result_recovery_20260929_xp_cpu"
        for row in failed
    ):
        failed.append({
            "run_id": "c_v228_xp_cpu_reset_result_recovery_20260929_xp_cpu",
            "reason": "dynamic_gate_used_stale_pre_reset_w1_container_identity",
            "completed_outers_preserved": [14, 19],
            "incomplete_outer_attempt": 20,
            "incomplete_outer_credit": 0,
            "rejected_outer_attempt_increment": 0,
            "workload_started_in_incomplete_outer": False,
        })
    extra_failures = [
        {
            "run_id": "c_v235_xp_cpu_runtime_gate_recovery_20260929_xp_cpu",
            "reason": "runtime_gate_environment_root_remained_on_static_authority_after_reset",
            "completed_outers_preserved": [14, 19],
            "incomplete_outer_attempt": 20,
            "incomplete_outer_credit": 0,
            "rejected_outer_attempt_increment": 0,
            "workload_started_in_incomplete_outer": True,
        },
        {
            "run_id": "c_v236_xp_cpu_runtime_gate_recovery_20260929_xp_cpu",
            "reason": "runtime_gate_environment_switched_before_legacy_authority_install",
            "completed_outer_credit": 0,
            "workload_started": False,
        },
    ]
    existing_failures = {str(row.get("run_id") or "") for row in failed}
    failed.extend(
        row for row in extra_failures if row["run_id"] not in existing_failures
    )
    lineage = document.setdefault("scene_lineage", {}).setdefault(
        "hybench_xp_cpu", []
    )
    if launch["run_id"] not in lineage:
        lineage.append(launch["run_id"])

    stopped_at = datetime.now().astimezone().isoformat()
    for name in ("monitor", "finalizer"):
        previous = dict(document.get(name) or {})
        document[name] = {
            **previous,
            "status": "STOPPED_BY_USER_REQUEST",
            "stopped_at": stopped_at,
            "continuous_polling_enabled": False,
            "replacement_process_started": False,
        }
    document["monitoring_policy"] = {
        "continuous_monitoring_enabled": False,
        "stable_complete_round_already_observed": True,
        "future_status_checks": "manual_one_off_only",
        "formal_experiment_controllers_continue_independently": True,
    }
    document["updated_at"] = stopped_at
    atomic(MANIFEST, document)
    print(json.dumps({
        "status": "UPDATED", "manifest": str(MANIFEST),
        "sha256": sha(MANIFEST), "xp_cpu_pid": launch["pid"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
