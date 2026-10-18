#!/usr/bin/env python3
"""Seal fresh live authority for matched XP baseline/champion replays."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import restore_mysql_contaminated_champion_slots_v1 as base
from scripts import mysql_cross_project_wave_coordination_v20 as coordination_v20


OUTPUT = ROOT / "history/mysql_four_scene_20260809/v1102_matched_xp_live_authority_20260829"
SELECTED = {
    "hybench_xp_cpu": ("xp_cpu_w2",),
    "hybench_xp_io": ("xp_io_w1", "xp_io_w2"),
}


def runtime_management_readback() -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for name in ("dockerd", "containerd"):
        pids: list[int] = []
        for process_dir in Path("/proc").iterdir():
            if not process_dir.name.isdigit():
                continue
            try:
                if (process_dir / "comm").read_text(encoding="utf-8").strip() == name:
                    pids.append(int(process_dir.name))
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
        rows[name] = [
            {"pid": pid, "effective_cpus": sorted(os.sched_getaffinity(pid))}
            for pid in sorted(pids)
        ]
    return rows


def verify_worker(
    freeze: Mapping[str, Any], worker: dict[str, Any], cached: Mapping[str, Any],
) -> dict[str, Any]:
    worker_id = str(worker["worker_id"])
    container = str(worker["container"])
    current = dict(cached[container])
    state = current.get("State") or {}
    labels = (current.get("Config") or {}).get("Labels") or {}
    if (
        state.get("Running") is not True
        or labels.get("ra.worker_id") != worker_id
    ):
        raise RuntimeError(f"{worker_id} running-container authority boundary differs")
    scoped = base.mini_freeze(freeze, worker_id)
    original_inspect = base.authority.inspect
    base.authority.inspect = lambda name: dict(cached[name])
    try:
        row = base.authority.verify_live(
            scoped, base.authority.canonical_row(scoped, worker)
        )
    finally:
        base.authority.inspect = original_inspect
    row.update({
        "matched_replay_worker_substitution": worker_id == "xp_cpu_w2",
        "substitution_reason": (
            "xp_cpu_w1 was removed by the failed pre-recovery reset; both fresh "
            "baseline and fresh champion are bound to xp_cpu_w2"
            if worker_id == "xp_cpu_w2" else None
        ),
        "verified_at": base.now(),
    })
    return row


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh output required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    cpu_freeze = base.load(base.FOUR_FREEZE_PATH)
    io_freeze = base.load(base.IO_FREEZE_PATH)
    cpu_workers = {
        str(row["worker_id"]): dict(row) for row in cpu_freeze["workers"]
    }
    io_workers = {
        str(row["worker_id"]): dict(row) for row in io_freeze["workers"]
    }
    selected = {
        "xp_cpu_w2": (cpu_freeze, cpu_workers["xp_cpu_w2"]),
        "xp_io_w1": (io_freeze, io_workers["xp_io_w1"]),
        "xp_io_w2": (io_freeze, io_workers["xp_io_w2"]),
    }
    cached = coordination_v20.batch_inspect(
        [str(worker["container"]) for _freeze, worker in selected.values()],
        maximum_wait_seconds=300.0,
    )
    rows = {
        worker_id: verify_worker(freeze, worker, cached)
        for worker_id, (freeze, worker) in selected.items()
    }
    target_cpus = set().union(*(
        base.authority.cpus(str(row["database_cpuset"]))
        | base.authority.cpus(str(row["client_cpuset"]))
        for row in rows.values()
    ))
    management = runtime_management_readback()
    gate = coordination_v20.rolling_quiet_gate(
        run_id=OUTPUT.name,
        required_seconds=60.0,
        maximum_wait_seconds=600.0,
        maximum_load1=512.0,
        target_cpus=target_cpus,
    )
    if gate.get("status") != "PASS":
        base.atomic(OUTPUT / "gate_failure.json", {
            "status": "FAILED_CLOSED",
            "created_at": base.now(),
            "rolling_dynamic_noninterference_gate": gate,
            "management_runtime_readback": management,
            "external_process_control_or_stop_performed": False,
            "postgresql_process_control_or_stop_performed": False,
        })
        raise RuntimeError("fresh XP live authority gate failed")
    common = {
        "created_at": base.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "status": "PASS",
        "formal_launch_authorized": True,
        "observed_seconds": gate.get("observed_seconds"),
        "rolling_dynamic_noninterference_gate": gate,
        "management_runtime_readback": management,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "accepted_state_credit_increment": 0,
        "authority_purpose": "same_worker_same_device_fresh_xp_baseline_then_champion_replay",
        "worker_assignments_by_scene": SELECTED,
        "resource_freeze_by_scene": {
            "hybench_xp_cpu": {
                "path": str(base.FOUR_FREEZE_PATH),
                "sha256": base.sha(base.FOUR_FREEZE_PATH),
            },
            "hybench_xp_io": {
                "path": str(base.IO_FREEZE_PATH),
                "sha256": base.sha(base.IO_FREEZE_PATH),
            },
        },
    }
    preflight = OUTPUT / "transition_preflight.json"
    base.atomic(preflight, {
        **common, "schema_version": "mysql_matched_xp_preflight_v1",
    })
    prepared = OUTPUT / "prepared_binding.json"
    base.atomic(prepared, {
        **common,
        "schema_version": "mysql_matched_xp_prepared_v1",
        "status": "PREPARED",
        "preflight_path": str(preflight),
        "preflight_sha256": base.sha(preflight),
        "workers": [rows[key] for key in sorted(rows)],
        "workload_started": False,
    })
    accounts = OUTPUT / "benchmark_accounts_binding.json"
    base.atomic(accounts, {
        **common,
        "schema_version": "mysql_matched_xp_accounts_v1",
        "prepared_binding_path": str(prepared),
        "prepared_binding_sha256": base.sha(prepared),
        "workers": [rows[key] for key in sorted(rows)],
        "passwords_emitted": False,
    })
    manifest = OUTPUT / "live_authority.json"
    base.atomic(manifest, {
        **common,
        "schema_version": "mysql_matched_xp_live_authority_v1",
        "artifacts": {
            "prepared": str(prepared),
            "preflight": str(preflight),
            "accounts": str(accounts),
        },
        "artifacts_sha256": {
            "prepared": base.sha(prepared),
            "preflight": base.sha(preflight),
            "accounts": base.sha(accounts),
        },
        "workers": sorted(rows),
    })
    print(json.dumps({"status": "PASS", "authority": str(manifest)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
