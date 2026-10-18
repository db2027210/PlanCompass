#!/usr/bin/env python3
"""Continue Top4 XP recovery after Docker publishes stale CPU task deletion.

Attempt v244 rebuilt IO W1 but failed closed while Docker still reported four
CPU containers as running after their exact host tasks had exited.  Docker has
since removed those four stale records.  This continuation reuses only the
completed IO-W1 reset, rebuilds the four absent CPU predecessors, and emits a
complete eight-worker binding.
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
from scripts import recover_mysql_xp_top4_unhealthy_slots_v1 as v1
from scripts import reset_mysql_xp_data_planes_four_worker_v1 as reset
from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm


CPU_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_cpu_w3", "xp_cpu_w4"}
IO_REUSED = "xp_io_w1"
COMPANIONS = {"xp_io_w2", "xp_io_w3", "xp_io_w4"}
ALL_WORKERS = CPU_IDS | {IO_REUSED} | COMPANIONS
V244 = v1.HISTORY / "v244_to_xp_top4_unhealthy_recovery_20260826_attempt001"
V244_IO_ROW = V244 / "xp_resets/top4_unhealthy_slots_v1/xp_io_w1/reset_worker.json"
V244_STDERR = (
    v1.HISTORY / "formal_launch_logs/v244_xp_top4_unhealthy_recovery.stderr.log"
)
V244_ERROR = (
    "RuntimeError: ra_mysql_disk2_algc_xp_cpu_w1_20260809 remained running "
    "after bound host-PID kill"
)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{v1.sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("safe run id required")
    output = v1.HISTORY / args.run_id
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh recovery output required: {output}")

    reset.install()
    algorithm.reset = reset
    algorithm.FREEZE = v1.FREEZE
    algorithm.WORKER_IDS = tuple(sorted(ALL_WORKERS))
    algorithm.XP_IDS = algorithm.WORKER_IDS
    algorithm.XP_RESET_MAX_WORKERS = 4

    if (
        not V244_IO_ROW.is_file()
        or V244_IO_ROW.is_symlink()
        or not V244_STDERR.is_file()
        or V244_ERROR not in V244_STDERR.read_text(encoding="utf-8")
        or not v1.unit_inactive("mysql-xp-top4-unhealthy-recovery-v244.service")
    ):
        raise RuntimeError("v244 failed recovery boundary differs")

    cpu_prior = v1.authority_workers(v1.CPU_AUTHORITY, "xp_cpu")
    io_prior = v1.authority_workers(v1.IO_AUTHORITY, "xp_io")
    prior = {**cpu_prior, **io_prior}
    freeze = v1.load(v1.FREEZE)
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    io_recovered = v1.load(V244_IO_ROW)
    if (
        io_recovered.get("worker_id") != IO_REUSED
        or io_recovered.get("ready") is not True
        or set(prior) != ALL_WORKERS
        or set(frozen) != ALL_WORKERS
        or v1.active_mysql_hybench_clients()
    ):
        raise RuntimeError("v244 reusable IO or Top4 worker authority differs")

    absent_cpu: dict[str, dict[str, Any]] = {}
    runtime_root = Path(str(freeze["storage"]["runtime_root"]))
    for worker_id in sorted(CPU_IDS):
        row = prior[worker_id]
        inventory = reset._docker_run(
            ["docker", "ps", "-aq", "--filter", f"name=^{row['container']}$"],
            timeout=30,
        ).stdout.strip()
        cgroup = Path("/sys/fs/cgroup/blkio/docker-limited") / str(row["container_id"])
        worker_root = runtime_root / worker_id
        if (
            inventory
            or cgroup.exists()
            or not (worker_root / "datadir").is_dir()
            or not (worker_root / "credentials").is_dir()
        ):
            raise RuntimeError(f"{worker_id} stale Docker removal proof differs")
        absent_cpu[worker_id] = {
            "container": row["container"], "container_id": row["container_id"],
            "container_absent": True, "containerd_task_absent": True,
            "blkio_cgroup_absent": True,
        }

    inspected_io = reset._docker_json("inspect", str(io_recovered["container"]))
    if (
        inspected_io.get("Id") != io_recovered.get("container_id")
        or (inspected_io.get("State") or {}).get("Running") is not True
    ):
        raise RuntimeError("v244 rebuilt IO W1 is not live")

    stage = output / "xp_resets/top4_cpu_absent_predecessor_v2"
    stage.mkdir(parents=True, mode=0o700)
    quarantine = runtime_root / "quarantine" / output.name / stage.name
    started = time.monotonic()
    recovered: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="xp-cpu-absent") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze, frozen[worker_id], prior[worker_id], quarantine,
                stage, True, "formal_point", True, False, False, True,
            ): worker_id
            for worker_id in sorted(CPU_IDS)
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
    account_targets = {**recovered, IO_REUSED: dict(io_recovered)}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="xp-top4-users-v2") as pool:
        futures = {
            pool.submit(users._run_worker, row, account_root, True, False): worker_id
            for worker_id, row in account_targets.items()
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            account_targets[worker_id].update(future.result())

    all_workers = {
        **{worker_id: dict(io_prior[worker_id]) for worker_id in COMPANIONS},
        **account_targets,
    }
    companion_readback: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(COMPANIONS):
        row = all_workers[worker_id]
        inspected = reset._docker_json("inspect", str(row["container"]))
        companion_readback[worker_id] = {
            "container_id": inspected.get("Id"),
            "container_pid": int((inspected.get("State") or {}).get("Pid") or 0),
            "sessions": algorithm.xp_active_client_session_count(row),
        }
        if (
            companion_readback[worker_id]["container_id"] != row.get("container_id")
            or companion_readback[worker_id]["container_pid"] <= 1
            or companion_readback[worker_id]["sessions"] != 0
        ):
            raise RuntimeError(f"{worker_id} preserved companion changed")
    if (
        set(all_workers) != ALL_WORKERS
        or any(row.get("ready") is not True for row in account_targets.values())
        or any(
            algorithm.xp_active_client_session_count(all_workers[worker_id]) != 0
            for worker_id in ALL_WORKERS
        )
    ):
        raise RuntimeError("Top4 XP v2 recovery readback differs")

    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_xp_top4_unhealthy_slots_recovery_v2",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "freeze_path": str(v1.FREEZE), "freeze_sha256": v1.sha(v1.FREEZE),
        "v244_failed_recovery_stderr": {
            "path": str(V244_STDERR), "sha256": v1.sha(V244_STDERR),
        },
        "v244_reused_io_worker_evidence": {
            "path": str(V244_IO_ROW), "sha256": v1.sha(V244_IO_ROW),
        },
        "absent_cpu_predecessors": absent_cpu,
        "reset_worker_ids": sorted(CPU_IDS | {IO_REUSED}),
        "physically_reset_in_this_continuation": sorted(CPU_IDS),
        "completed_reset_reused_from_v244": [IO_REUSED],
        "preserved_companion_worker_ids": sorted(COMPANIONS),
        "preserved_companion_readback": companion_readback,
        "workers": [all_workers[worker_id] for worker_id in sorted(ALL_WORKERS)],
        "reset_concurrency": 4,
        "workload_started": False, "formal_measurement_credit_increment": 0,
        "accepted_rounds_mutation": False, "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "external_container_mutation": False, "postgres_control_or_mutation": False,
        "elapsed_seconds": time.monotonic() - started,
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": v1.sha(Path(__file__).resolve()),
    })
    cleanups = []
    cleanups.append(reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine, reset_workers=recovered,
        binding_path=binding, cleanup_path=output / "cpu_quarantine_cleanup.json",
    ))
    io_quarantine = (
        runtime_root / "quarantine" / V244.name / "top4_unhealthy_slots_v1"
    )
    cleanups.append(reset.purge_completed_discard_quarantine(
        quarantine_root=io_quarantine, reset_workers={IO_REUSED: account_targets[IO_REUSED]},
        binding_path=binding, cleanup_path=output / "io_v244_quarantine_cleanup.json",
    ))
    print(json.dumps({
        "status": "PASS", "binding": str(binding),
        "elapsed_seconds": time.monotonic() - started,
        "quarantine_deleted_bytes": sum(int(row["deleted_bytes"]) for row in cleanups),
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
