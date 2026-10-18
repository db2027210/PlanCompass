#!/usr/bin/env python3
"""Finalize an interrupted discard reset after all replacement XP planes are live."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_v5 as reset
from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm


XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
CPU_IDS = {"xp_cpu_w1", "xp_cpu_w2"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
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


def docker_inspect(container: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "inspect", container], capture_output=True, text=True,
        check=True, timeout=30,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError(f"Docker inspect cardinality differs: {container}")
    return dict(value[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--interrupted-recovery", type=Path, required=True)
    parser.add_argument("--failed-state", type=Path, required=True)
    parser.add_argument("--predecessor-reset", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,180}", args.run_id) is None:
        parser.error("unsafe run id")
    interrupted = args.interrupted_recovery.expanduser().resolve()
    failed_path = args.failed_state.expanduser().resolve()
    predecessor_path = args.predecessor_reset.expanduser().resolve()
    failed = load(failed_path)
    predecessor = load(predecessor_path)
    freeze = load(algorithm.FREEZE)
    stage = interrupted / "xp_resets/discard_fast_shutdown_pilot_v2"
    source_binding_path = stage / "reset_binding.json"
    drain_path = stage / "pre_reset_session_drain.json"
    source = load(source_binding_path)
    drain = load(drain_path)
    source_workers = {
        str(row["worker_id"]): dict(row) for row in source.get("workers") or []
    }
    predecessor_workers = {
        str(row["worker_id"]): dict(row)
        for row in predecessor.get("workers") or []
    }
    frozen = {
        str(row["worker_id"]): dict(row)
        for row in freeze.get("workers") or [] if row.get("worker_id") in XP_IDS
    }
    cpu_recovery_paths = {
        worker_id: stage / worker_id / "bound_init_credential_recovery.json"
        for worker_id in CPU_IDS
    }
    cpu_recoveries = {
        worker_id: load(path) for worker_id, path in cpu_recovery_paths.items()
    }
    controller_pid = int(failed.get("pid") or 0)
    shutdown_counts = {
        worker_id: len(source_workers[worker_id].get("mysql_shutdown_audit") or [])
        for worker_id in XP_IDS
    }
    if (
        interrupted.parent != ROOT / "history/mysql_four_scene_20260809"
        or (interrupted / "pilot_result.json").exists()
        or failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or "outer 5 batch 2 failed closed" not in str(failed.get("error") or "")
        or controller_pid <= 0
        or Path(f"/proc/{controller_pid}").exists()
        or predecessor.get("status") != "PASS"
        or predecessor.get("freeze_sha256") != sha(algorithm.FREEZE)
        or source.get("status") != "PASS"
        or source.get("freeze_sha256") != sha(algorithm.FREEZE)
        or set(source_workers) != XP_IDS
        or set(predecessor_workers) != XP_IDS
        or set(frozen) != XP_IDS
        or drain.get("status") != "PASS_DISCARD_ONLY"
        or set(drain.get("endpoint_observations") or {}) != XP_IDS
        or any(source_workers[row].get("ready") is not True for row in XP_IDS)
        or any(predecessor_workers[row].get("ready") is not True for row in XP_IDS)
        or any(shutdown_counts[row] != 0 for row in CPU_IDS)
        or any(shutdown_counts[row] != 1 for row in XP_IDS - CPU_IDS)
        or any(
            recovery.get("status") != "PASS_RECOVERY_ONLY"
            or recovery.get("worker_id") != worker_id
            or recovery.get("container_id") != source_workers[worker_id].get("container_id")
            or recovery.get("root_readback") != "1\t1"
            or recovery.get("benchmark_readback") != "1"
            for worker_id, recovery in cpu_recoveries.items()
        )
    ):
        raise RuntimeError("interrupted discard finalization authority differs")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    worker_root = output / "workers"
    worker_root.mkdir(mode=0o700)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"])) / "quarantine"
        / interrupted.name / stage.name
    )
    started = time.monotonic()
    recovered: dict[str, dict[str, Any]] = {}
    inspections: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(XP_IDS):
        row = dict(source_workers[worker_id])
        inspect = docker_inspect(str(row["container"]))
        inspections[worker_id] = inspect
        state = inspect.get("State") or {}
        mounts = {str(item.get("Destination")): str(item.get("Source")) for item in inspect.get("Mounts") or []}
        old_target = quarantine / worker_id
        if (
            inspect.get("Id") != row.get("container_id")
            or state.get("Running") is not True
            or mounts.get("/var/lib/mysql") != row.get("datadir")
            or not old_target.is_dir()
            or old_target.is_symlink()
            or row.get("quarantined_invalid_runtime_root") != str(old_target)
            or row.get("quarantined_predecessor_runtime_root") != str(old_target)
            or algorithm.xp_active_client_session_count(row) != 0
        ):
            raise RuntimeError(f"{worker_id} live discard recovery differs")
        row["container_pid"] = int(state.get("Pid") or 0)
        row["kernel_cgroup_resources"] = reset.prep._kernel_resource_readback(
            inspect, frozen[worker_id],
        )
        row["blkio_authority"] = reset.prep._enforce_blkio(
            inspect, freeze, frozen[worker_id],
        )
        if worker_id in CPU_IDS:
            prior = predecessor_workers[worker_id]
            row["mysql_shutdown_audit"] = [{
                "container": row["container"],
                "method": "discard_interrupted_completion_recovered",
                "clean_shutdown_error": (
                    "interrupted discard completed before per-worker audit commit; "
                    "exact predecessor root is quarantined and not live-mounted"
                ),
                "innodb_fast_shutdown_readback": None,
                "discard_blkio": {
                    "status": "RECONSTRUCTED_FROM_QUARANTINED_PREDECESSOR_AND_LIVE_REPLACEMENT",
                    "container_id": prior.get("container_id"),
                    "new_measurement_container_must_restore_frozen_limits": True,
                },
                "forced_kill": False,
                "forced_remove": False,
                "forced_host_pid_kill": False,
                "force_error": None,
                "initial_container_id": prior.get("container_id"),
                "initial_container_pid": prior.get("container_pid"),
                "container_exit_code": None,
                "container_absent_after_shutdown": False,
                "predecessor_discard_only": True,
                "discarded_datadir_never_reused": True,
                "elapsed_seconds": 0.0,
                "verified_stopped": True,
                "interrupted_resume_authority": True,
                "predecessor_reset_binding_path": str(predecessor_path),
                "predecessor_reset_binding_sha256": sha(predecessor_path),
                "credential_recovery_path": str(cpu_recovery_paths[worker_id]),
                "credential_recovery_sha256": sha(cpu_recovery_paths[worker_id]),
            }]
            row["interrupted_discard_audit_reconstructed"] = True
        account_dir = output / "accounts"
        account_dir.mkdir(exist_ok=True)
        account = users._run_worker(row, account_dir, verify_existing=False, rotate_all=True)
        row.update(account)
        evidence_dir = worker_root / worker_id
        evidence_dir.mkdir(mode=0o700)
        evidence = evidence_dir / "recovered_worker.json"
        row.pop("worker_evidence_path", None)
        row.pop("worker_evidence_sha256", None)
        atomic(evidence, row)
        row["worker_evidence_path"] = str(evidence)
        row["worker_evidence_sha256"] = sha(evidence)
        recovered[worker_id] = row

    if reset.prep._host_swap_devices():
        raise RuntimeError("host swap became active during discard finalization")
    binding = output / "reset_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_interrupted_discard_finalization_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(algorithm.FREEZE),
        "freeze_sha256": sha(algorithm.FREEZE),
        "interrupted_recovery_path": str(interrupted),
        "source_reset_binding_path": str(source_binding_path),
        "source_reset_binding_sha256": sha(source_binding_path),
        "failed_state_path": str(failed_path),
        "failed_state_sha256": sha(failed_path),
        "predecessor_reset_binding_path": str(predecessor_path),
        "predecessor_reset_binding_sha256": sha(predecessor_path),
        "workers": [recovered[row] for row in sorted(XP_IDS)],
        "reset_worker_ids": sorted(XP_IDS),
        "reset_concurrency": 1,
        "interrupted_discard_audit_reconstructed_worker_ids": sorted(CPU_IDS),
        "workload_started": False,
        "formal_outer_started": False,
        "accepted_rounds_mutation": False,
        "elapsed_seconds": time.monotonic() - started,
    })
    cleanup_path = output / "quarantine_cleanup.json"
    cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers=recovered,
        binding_path=binding,
        cleanup_path=cleanup_path,
    )
    extra_targets = [
        quarantine / f"{worker_id}_failed_staging_after_exec_timeout"
        for worker_id in sorted(CPU_IDS)
    ]
    mount_sources = {
        str(item.get("Source") or "")
        for inspect in inspections.values() for item in inspect.get("Mounts") or []
    }
    extra_rows = []
    for target in extra_targets:
        if (
            target.is_symlink()
            or not target.is_dir()
            or target.parent.resolve() != quarantine.resolve()
            or target.name not in {
                "xp_cpu_w1_failed_staging_after_exec_timeout",
                "xp_cpu_w2_failed_staging_after_exec_timeout",
            }
            or any(source == str(target) or source.startswith(str(target) + "/") for source in mount_sources)
        ):
            raise RuntimeError(f"failed staging cleanup authority differs: {target}")
        extra_rows.append({"path": str(target), "bytes_before_delete": reset._host_tree_bytes(target)})
    for row in extra_rows:
        target = Path(row["path"])
        subprocess.run(
            ["sudo", "-n", "rm", "-rf", "--", str(target)],
            check=True, timeout=1800,
        )
        if target.exists():
            raise RuntimeError(f"failed staging cleanup incomplete: {target}")
    extra_cleanup_path = output / "failed_staging_cleanup.json"
    atomic(extra_cleanup_path, {
        "schema_version": "mysql_algorithm_c_failed_staging_cleanup_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "reset_binding_path": str(binding),
        "reset_binding_sha256": sha(binding),
        "deleted": extra_rows,
        "deleted_bytes": sum(int(row["bytes_before_delete"]) for row in extra_rows),
        "live_mounts_deleted": False,
        "sealed_templates_deleted": False,
        "recoverable": False,
    })
    pilot = output / "pilot_result.json"
    atomic(pilot, {
        "schema_version": "mysql_algorithm_c_interrupted_discard_recovery_v1",
        "status": "PASS_RECOVERY_ONLY",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "reset_binding_path": str(binding),
        "reset_binding_sha256": sha(binding),
        "quarantine_cleanup_path": str(cleanup_path),
        "quarantine_cleanup_sha256": sha(cleanup_path),
        "failed_staging_cleanup_path": str(extra_cleanup_path),
        "failed_staging_cleanup_sha256": sha(extra_cleanup_path),
        "quarantine_deleted_bytes": int(cleanup["deleted_bytes"])
        + sum(int(row["bytes_before_delete"]) for row in extra_rows),
        "workload_started": False,
        "formal_outer_started": False,
        "accepted_rounds_mutation": False,
        "workers": [recovered[row] for row in sorted(XP_IDS)],
    })
    print(json.dumps({
        "status": "PASS", "pilot": str(pilot), "reset_binding": str(binding),
        "quarantine_deleted_bytes": load(pilot)["quarantine_deleted_bytes"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
