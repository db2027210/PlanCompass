#!/usr/bin/env python3
"""Recover only the five unhealthy Top4 XP MySQL data planes.

The failed CPU-B and IO-C measurements are immutable.  This recovery replaces
the four CPU planes and IO W1 from the sealed resource-v4 templates, preserves
the three healthy IO companions, and emits one eight-worker binding that can
refresh both scene-local live authorities.  No algorithm state credit is
created here.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from scripts import reset_mysql_xp_data_planes_four_worker_v1 as reset
from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4"
    / "resource_successor.json"
)
CPU_AUTHORITY = (
    HISTORY / "v233_to_b_top4_live_authority_20260826/hybench_xp_cpu/authority"
)
IO_AUTHORITY = (
    HISTORY
    / "v226_to_xp_combined_v4_schema_rebind_20260826/hybench_xp_io/authority"
)
CPU_FAILURE = HISTORY / "v241_to_b_top4_fresh_20260826_attempt025_xp_cpu"
IO_FAILURE = HISTORY / "v225_to_c_top4_io_xp4_20260826_attempt020_xp_io"
TARGETS = {
    "xp_cpu_w1", "xp_cpu_w2", "xp_cpu_w3", "xp_cpu_w4", "xp_io_w1",
}
COMPANIONS = {"xp_io_w2", "xp_io_w3", "xp_io_w4"}
ALL_WORKERS = TARGETS | COMPANIONS
CPU_ERROR_PREFIX = (
    "RuntimeError: XP active client sessions were not proven drained before "
    "the 60s bound:"
)
IO_ERROR_PREFIX = "CalledProcessError: Command '['mysql',"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    sidecar = path.with_name(path.name + ".sha256")
    if sidecar.is_file() and sidecar.read_text(encoding="ascii").split()[0] != sha(path):
        raise RuntimeError(f"checksum sidecar differs: {path}")
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
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def unit_inactive(unit: str) -> bool:
    completed = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "MainPID"],
        capture_output=True, text=True, check=True, timeout=20,
    )
    fields = dict(
        line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line
    )
    return (
        fields.get("ActiveState") in {"inactive", "failed"}
        and int(fields.get("MainPID") or 0) == 0
    )


def authority_workers(root: Path, prefix: str) -> dict[str, dict[str, Any]]:
    prepared_path = root / "prepared_binding.json"
    accounts_path = root / "benchmark_accounts_binding.json"
    prepared = load(prepared_path)
    accounts = load(accounts_path)
    freeze = load(FREEZE)
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    prepared_rows = {
        str(row["worker_id"]): dict(row) for row in prepared.get("workers") or []
    }
    account_rows = {
        str(row["worker_id"]): dict(row) for row in accounts.get("workers") or []
    }
    expected = {f"{prefix}_w{index}" for index in range(1, 5)}
    if (
        prepared.get("status") != "PREPARED"
        or accounts.get("status") != "PASS"
        or prepared.get("freeze_sha256") != sha(FREEZE)
        or accounts.get("freeze_sha256") != sha(FREEZE)
        or accounts.get("prepared_binding_sha256") != sha(prepared_path)
        or not expected.issubset(prepared_rows)
        or not expected.issubset(account_rows)
    ):
        raise RuntimeError(f"{prefix} authority chain differs")
    return {
        worker_id: {
            **frozen[worker_id], **prepared_rows[worker_id], **account_rows[worker_id],
        }
        for worker_id in expected
    }


def active_mysql_hybench_clients() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["ps", "-eo", "pid=,args="], capture_output=True, text=True,
        check=True, timeout=30,
    )
    rows: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        if (
            "com.hybench.HyBench" in command
            and ("stmt_mysql" in command or "/disk2/resource_adaptive_mysql" in command)
        ):
            rows.append({
                "pid": int(pid_text),
                "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            })
    return rows


def recorded_client_pids(*roots: Path) -> list[int]:
    pids: set[int] = set()
    for root in roots:
        for path in root.rglob("client_processes.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                for field in ("launcher_pid", "workload_pid"):
                    pid = int(row.get(field) or 0)
                    if pid > 1:
                        pids.add(pid)
    return sorted(pids)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("safe run id required")
    output = HISTORY / args.run_id
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh recovery output required: {output}")

    reset.install()
    algorithm.reset = reset
    algorithm.FREEZE = FREEZE
    algorithm.WORKER_IDS = tuple(sorted(ALL_WORKERS))
    algorithm.XP_IDS = algorithm.WORKER_IDS
    algorithm.XP_RESET_MAX_WORKERS = 4

    cpu_state_path = CPU_FAILURE / "run_state.json"
    io_state_path = IO_FAILURE / "run_state.json"
    cpu_state = load(cpu_state_path)
    io_state = load(io_state_path)
    if (
        cpu_state.get("status") != "FAILED_CLOSED"
        or cpu_state.get("stage") != "FAILED"
        or not str(cpu_state.get("error") or "").startswith(CPU_ERROR_PREFIX)
        or int(cpu_state.get("outer_attempt") or 0) != 0
        or io_state.get("status") != "FAILED_CLOSED"
        or io_state.get("stage") != "FAILED"
        or not str(io_state.get("error") or "").startswith(IO_ERROR_PREFIX)
        or int(io_state.get("outer_attempt") or 0) != 5
        or io_state.get("accepted_rounds", {}).get("hybench_xp_io") != 3
        or io_state.get("rejected_outer_attempts", {}).get("hybench_xp_io") != 2
        or not unit_inactive("mysql-algb-top4-v241-xp_cpu.service")
        or not unit_inactive("mysql-algc-top4-v225-xp_io.service")
    ):
        raise RuntimeError("Top4 XP failed-run recovery boundary differs")

    workers = {
        **authority_workers(CPU_AUTHORITY, "xp_cpu"),
        **authority_workers(IO_AUTHORITY, "xp_io"),
    }
    freeze = load(FREEZE)
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    if set(workers) != ALL_WORKERS or set(frozen) != ALL_WORKERS:
        raise RuntimeError("Top4 XP recovery worker set differs")

    live_clients = active_mysql_hybench_clients()
    recorded_pids = recorded_client_pids(CPU_FAILURE, IO_FAILURE)
    if live_clients:
        raise RuntimeError(f"MySQL HyBench clients remain live: {live_clients}")

    before: dict[str, dict[str, Any]] = {}
    companion_before: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(ALL_WORKERS):
        row = workers[worker_id]
        inspected = reset._docker_json("inspect", str(row["container"]))
        state = inspected.get("State") or {}
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        if (
            inspected.get("Id") != row.get("container_id")
            or state.get("Running") is not True
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or labels.get("ra.worker_id") != worker_id
        ):
            raise RuntimeError(f"{worker_id} pre-recovery identity differs")
        top = reset._docker_run(
            ["docker", "top", str(row["container"]), "-eo", "pid,stat,comm"],
            timeout=30,
        ).stdout
        before[worker_id] = {
            "container": row["container"], "container_id": inspected["Id"],
            "container_pid": int(state.get("Pid") or 0),
            "process_table_sha256": hashlib.sha256(top.encode()).hexdigest(),
            "identity_labels_verified": True,
        }
        if worker_id in COMPANIONS:
            if algorithm.xp_active_client_session_count(row) != 0:
                raise RuntimeError(f"{worker_id} healthy companion has active sessions")
            companion_before[worker_id] = {
                **before[worker_id],
                "mounts": sorted(
                    (str(item.get("Source")), str(item.get("Destination")))
                    for item in inspected.get("Mounts") or []
                ),
            }

    stage = output / "xp_resets/top4_unhealthy_slots_v1"
    stage.mkdir(parents=True, mode=0o700)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / output.name / stage.name
    )
    started = time.monotonic()
    recovered: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="xp-top4-recovery") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze, frozen[worker_id], workers[worker_id], quarantine,
                stage, False, "formal_point", True, False, False, False,
            ): worker_id
            for worker_id in sorted(TARGETS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            recovered[worker_id] = future.result()
            print(json.dumps({
                "worker_id": worker_id, "reset_complete": True,
                "elapsed_seconds": time.monotonic() - started,
            }), flush=True)

    account_root = stage / "accounts"
    account_root.mkdir()
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="xp-top4-users") as pool:
        futures = {
            pool.submit(users._run_worker, recovered[worker_id], account_root, True, False):
            worker_id for worker_id in sorted(TARGETS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            recovered[worker_id].update(future.result())

    all_workers = {
        **{worker_id: dict(workers[worker_id]) for worker_id in COMPANIONS},
        **recovered,
    }
    companion_after: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(COMPANIONS):
        row = all_workers[worker_id]
        inspected = reset._docker_json("inspect", str(row["container"]))
        companion_after[worker_id] = {
            "container": row["container"], "container_id": inspected.get("Id"),
            "container_pid": int((inspected.get("State") or {}).get("Pid") or 0),
            "process_table_sha256": companion_before[worker_id]["process_table_sha256"],
            "identity_labels_verified": True,
            "mounts": sorted(
                (str(item.get("Source")), str(item.get("Destination")))
                for item in inspected.get("Mounts") or []
            ),
        }
    if (
        companion_after != companion_before
        or set(all_workers) != ALL_WORKERS
        or any(row.get("ready") is not True for row in recovered.values())
        or any(
            algorithm.xp_active_client_session_count(all_workers[worker_id]) != 0
            for worker_id in ALL_WORKERS
        )
    ):
        raise RuntimeError("Top4 XP recovery readback differs")

    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_xp_top4_unhealthy_slots_recovery_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "cpu_failed_state": {"path": str(cpu_state_path), "sha256": sha(cpu_state_path)},
        "io_failed_state": {"path": str(io_state_path), "sha256": sha(io_state_path)},
        "cpu_authority_root": str(CPU_AUTHORITY),
        "io_authority_root": str(IO_AUTHORITY),
        "recorded_client_pids": recorded_pids,
        "active_mysql_hybench_clients": [],
        "pre_recovery": before,
        "reset_worker_ids": sorted(TARGETS),
        "preserved_companion_worker_ids": sorted(COMPANIONS),
        "companion_before": companion_before,
        "companion_after": companion_after,
        "workers": [all_workers[worker_id] for worker_id in sorted(ALL_WORKERS)],
        "reset_concurrency": 4,
        "workload_started": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "external_container_mutation": False,
        "postgres_control_or_mutation": False,
        "elapsed_seconds": time.monotonic() - started,
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": sha(Path(__file__).resolve()),
    })
    cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers=recovered,
        binding_path=binding,
        cleanup_path=output / "quarantine_cleanup.json",
    )
    print(json.dumps({
        "status": "PASS", "binding": str(binding),
        "elapsed_seconds": time.monotonic() - started,
        "quarantine_deleted_bytes": cleanup["deleted_bytes"],
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
