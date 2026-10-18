#!/usr/bin/env python3
"""Seal current six-worker authority for matched baseline replay."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import restore_mysql_contaminated_champion_slots_v1 as base


OUTPUT = ROOT / "history/mysql_four_scene_20260809/v1075_matched_baseline_live_authority_20260829"


def inspect_optional(container: str) -> dict[str, Any] | None:
    completed = subprocess.run(
        ["docker", "inspect", container], cwd=ROOT, capture_output=True,
        text=True, check=False, timeout=120,
    )
    if completed.returncode != 0:
        return None
    value = json.loads(completed.stdout)
    return dict(value[0]) if isinstance(value, list) and len(value) == 1 else None


def remove_old(container: str) -> None:
    current = inspect_optional(container)
    if current is None:
        return
    state = current.get("State") or {}
    if state.get("Running") is True:
        subprocess.run(
            ["docker", "exec", container, "mysqladmin",
             "--defaults-file=/run/ra-credentials/selected.cnf", "shutdown"],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=180,
        )
        current = inspect_optional(container)
        if current is not None and (current.get("State") or {}).get("Running") is True:
            base.prepare._run(["docker", "stop", "--time", "60", container], timeout=120)
    base.prepare._run(["docker", "rm", container], timeout=180)


def restore_job_w2(
    full_freeze: Mapping[str, Any], worker: dict[str, Any],
) -> dict[str, Any]:
    worker_id = str(worker["worker_id"])
    spec = base.RESTORES[worker_id]
    runtime = Path(str(worker["runtime_root"]))
    backup = Path(str(spec["backup"]))
    current = inspect_optional(str(worker["container"]))
    if current is None:
        raise RuntimeError(f"{worker_id} timeout predecessor container vanished")
    labels = (current.get("Config") or {}).get("Labels") or {}
    mounts = {str(row.get("Destination")): str(row.get("Source")) for row in current.get("Mounts") or []}
    if (
        labels.get("ra.worker_id") != spec["current_label"]
        or mounts.get("/var/lib/mysql") != str(runtime / "datadir")
        or not backup.is_dir()
        or not (backup / "credentials/selected.cnf").is_file()
    ):
        raise RuntimeError(f"{worker_id} recovered continuation identity differs")
    remove_old(str(worker["container"]))
    discarded = runtime.parent / "quarantine" / OUTPUT.name / worker_id
    if discarded.exists() or discarded.is_symlink():
        raise RuntimeError(f"fresh resume quarantine required: {discarded}")
    discarded.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.rename(runtime, discarded)
    os.rename(backup, runtime)
    scoped = base.mini_freeze(full_freeze, worker_id)
    base.docker_run_worker(scoped, worker)
    row = base.authority.verify_live(scoped, base.authority.canonical_row(scoped, worker))
    row.update({
        "restored_from_recoverable_quarantine": str(backup),
        "discarded_interrupted_xp_runtime": str(discarded),
        "full_template_recopy_performed": False,
        "restored_at": base.now(), "docker_recovery_continuation": True,
    })
    return row


def resume_created(full_freeze: Mapping[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
    worker_id = str(worker["worker_id"])
    current = inspect_optional(str(worker["container"]))
    labels = ((current or {}).get("Config") or {}).get("Labels") or {}
    mounts = {
        str(row.get("Destination")): str(row.get("Source"))
        for row in (current or {}).get("Mounts") or []
    }
    status = str((current.get("State") or {}).get("Status")) if current else "missing"
    if (
        current is None or labels.get("ra.worker_id") != worker_id
        or status != "running"
        or mounts.get("/var/lib/mysql") != str(Path(str(worker["runtime_root"])) / "datadir")
    ):
        raise RuntimeError(f"{worker_id} running-container authority boundary differs")
    scoped = base.mini_freeze(full_freeze, worker_id)
    row = base.authority.verify_live(scoped, base.authority.canonical_row(scoped, worker))
    row.update({
        "post_champion_replay_reverified": True,
        "verified_at": base.now(),
        "initial_container_status": status,
    })
    return row


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh resume output required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    four = base.load(base.FOUR_FREEZE_PATH)
    io_freeze = base.load(base.IO_FREEZE_PATH)
    four_workers = {str(row["worker_id"]): dict(row) for row in four["workers"]}
    io_workers = {str(row["worker_id"]): dict(row) for row in io_freeze["workers"]}
    rows = {
        "job_w1": resume_created(four, four_workers["job_w1"]),
        "job_w2": resume_created(four, four_workers["job_w2"]),
        "tpch_w1": resume_created(four, four_workers["tpch_w1"]),
    }
    scoped_cpu = base.mini_freeze(four, "xp_cpu_w1")
    rows["xp_cpu_w1"] = base.authority.verify_live(
        scoped_cpu, base.authority.canonical_row(scoped_cpu, four_workers["xp_cpu_w1"])
    )
    for worker_id in ("xp_io_w1", "xp_io_w2"):
        scoped = base.mini_freeze(io_freeze, worker_id)
        rows[worker_id] = base.authority.verify_live(
            scoped, base.authority.canonical_row(scoped, io_workers[worker_id])
        )
    target_cpus = set().union(*(
        base.authority.cpus(str(row["database_cpuset"]))
        | base.authority.cpus(str(row["client_cpuset"])) for row in rows.values()
    ))
    gate = base.coordination.rolling_quiet_gate(
        run_id=OUTPUT.name, required_seconds=60.0, maximum_wait_seconds=600.0,
        maximum_load1=512.0, target_cpus=target_cpus,
    )
    if gate.get("status") != "PASS":
        base.atomic(OUTPUT / "gate_failure.json", {
            "status": "FAILED_CLOSED",
            "created_at": base.now(),
            "rolling_dynamic_noninterference_gate": gate,
            "external_process_control_or_stop_performed": False,
            "postgresql_process_control_or_stop_performed": False,
        })
        raise RuntimeError("resumed live authority gate failed")
    common = {
        "created_at": base.now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True, "status": "PASS",
        "formal_launch_authorized": True, "observed_seconds": gate.get("observed_seconds"),
        "rolling_dynamic_noninterference_gate": gate,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "accepted_state_credit_increment": 0,
        "authority_purpose": "same_worker_same_device_default_parameter_baseline_replay",
        "source_champion_replay_contract": str(
            ROOT / "config/formal/frozen_mysql_contaminated_champion_replay_20260829_v2/champion_replay_contract.json"
        ),
        "resource_freeze_by_scene": {
            "job_memory": {"path": str(base.FOUR_FREEZE_PATH), "sha256": base.sha(base.FOUR_FREEZE_PATH)},
            "tpch_sf4_memory": {"path": str(base.FOUR_FREEZE_PATH), "sha256": base.sha(base.FOUR_FREEZE_PATH)},
            "hybench_xp_cpu": {"path": str(base.FOUR_FREEZE_PATH), "sha256": base.sha(base.FOUR_FREEZE_PATH)},
            "hybench_xp_io": {"path": str(base.IO_FREEZE_PATH), "sha256": base.sha(base.IO_FREEZE_PATH)},
        },
    }
    preflight = OUTPUT / "transition_preflight.json"
    base.atomic(preflight, {**common, "schema_version": "mysql_contaminated_champion_preflight_v2"})
    prepared = OUTPUT / "prepared_binding.json"
    base.atomic(prepared, {
        **common, "schema_version": "mysql_contaminated_champion_prepared_v2",
        "status": "PREPARED", "preflight_path": str(preflight), "preflight_sha256": base.sha(preflight),
        "workers": [rows[key] for key in sorted(rows)], "workload_started": False,
    })
    accounts = OUTPUT / "benchmark_accounts_binding.json"
    base.atomic(accounts, {
        **common, "schema_version": "mysql_contaminated_champion_accounts_v2",
        "prepared_binding_path": str(prepared), "prepared_binding_sha256": base.sha(prepared),
        "workers": [rows[key] for key in sorted(rows)], "passwords_emitted": False,
    })
    manifest = OUTPUT / "live_authority.json"
    base.atomic(manifest, {
        **common, "schema_version": "mysql_contaminated_champion_live_authority_v2",
        "artifacts": {"prepared": str(prepared), "preflight": str(preflight), "accounts": str(accounts)},
        "artifacts_sha256": {"prepared": base.sha(prepared), "preflight": base.sha(preflight), "accounts": base.sha(accounts)},
        "workers": sorted(rows), "restored_relational_workers": sorted(base.RESTORES),
    })
    print(json.dumps({"status": "PASS", "authority": str(manifest)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
