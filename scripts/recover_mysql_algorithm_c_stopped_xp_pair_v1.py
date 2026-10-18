#!/usr/bin/env python3
"""Recover the exact stopped XP w1 pair after an InnoDB semaphore crash."""

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
from scripts import reset_mysql_xp_data_planes_v5 as reset
from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm


RESET_IDS = {"xp_cpu_w1", "xp_io_w1"}
COMPANION_IDS = {"xp_cpu_w2", "xp_io_w2"}


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--predecessor-binding", type=Path, required=True)
    parser.add_argument("--failed-run-state", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run-id")
    predecessor_path = args.predecessor_binding.expanduser().resolve()
    failed_path = args.failed_run_state.expanduser().resolve()
    predecessor = load(predecessor_path)
    failed = load(failed_path)
    bound_freeze = Path(str(predecessor.get("freeze_path") or "")).resolve()
    if (
        bound_freeze.parent
        != ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
        or not bound_freeze.is_file()
        or bound_freeze.is_symlink()
        or sha(bound_freeze) != predecessor.get("freeze_sha256")
    ):
        raise RuntimeError("bound v18 resource freeze differs")
    algorithm.FREEZE = bound_freeze
    algorithm.r0mod.FREEZE = bound_freeze
    freeze = algorithm.load(algorithm.FREEZE)
    prior = {
        str(row["worker_id"]): dict(row)
        for row in (predecessor.get("workers") or [])
    }
    frozen = {
        str(row["worker_id"]): dict(row)
        for row in freeze["workers"]
        if row.get("worker_id") in RESET_IDS | COMPANION_IDS
    }
    if (
        predecessor.get("status") not in {"PASS", "PREPARED"}
        or predecessor.get("freeze_sha256") != algorithm.sha(algorithm.FREEZE)
        or set(prior) != RESET_IDS | COMPANION_IDS
        or set(frozen) != RESET_IDS | COMPANION_IDS
        or failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or "xp_cpu_w1 live container identity differs" not in str(failed.get("error") or "")
    ):
        raise RuntimeError("stopped XP pair recovery authority differs")

    crash_evidence = {}
    for worker_id in sorted(RESET_IDS | COMPANION_IDS):
        name = frozen[worker_id]["container"]
        inspected = reset._docker_json("inspect", name)
        state = inspected.get("State") or {}
        if inspected.get("Id") != prior[worker_id].get("container_id"):
            raise RuntimeError(f"{worker_id} predecessor identity differs")
        if worker_id in RESET_IDS:
            completed = subprocess.run(
                ["docker", "logs", "--tail", "240", name],
                capture_output=True, text=True, check=False, timeout=30,
            )
            log_text = completed.stdout + completed.stderr
            if (
                state.get("Running") is not False
                or int(state.get("ExitCode") or 0) != 2
                or state.get("OOMKilled") is not False
                or "Semaphore wait has lasted > 600 seconds" not in log_text
            ):
                raise RuntimeError(f"{worker_id} stopped crash evidence differs")
            crash_evidence[worker_id] = {
                "container_id": inspected["Id"],
                "exit_code": state.get("ExitCode"),
                "oom_killed": state.get("OOMKilled"),
                "finished_at": state.get("FinishedAt"),
                "semaphore_wait_fatal_present": True,
                "docker_log_sha256": hashlib.sha256(log_text.encode()).hexdigest(),
            }
        elif (
            state.get("Running") is not True
            or algorithm.xp_active_client_session_count(prior[worker_id]) != 0
        ):
            raise RuntimeError(f"{worker_id} live companion differs")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh recovery output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    stage = output / "xp_resets/stopped_w1_pair_recovery_v1"
    stage.mkdir(parents=True, mode=0o700)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / output.name / stage.name
    )
    started = time.monotonic()
    reset_rows: dict[str, dict[str, Any]] = {}
    finished: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="stopped-xp-pair") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze, frozen[worker_id], prior[worker_id], quarantine, stage,
                True, "formal_point", True, False, True, False,
            ): worker_id
            for worker_id in sorted(RESET_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            reset_rows[worker_id] = future.result()
            finished[worker_id] = time.monotonic() - started

    account_root = stage / "accounts"
    account_root.mkdir()
    workers = {
        worker_id: {**frozen[worker_id], **prior[worker_id]}
        for worker_id in COMPANION_IDS
    }
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="stopped-xp-users") as pool:
        futures = {
            pool.submit(
                users._run_worker, reset_rows[worker_id], account_root, True, False,
            ): worker_id
            for worker_id in sorted(RESET_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            workers[worker_id] = {
                **frozen[worker_id], **reset_rows[worker_id], **future.result(),
            }
    if (
        set(workers) != RESET_IDS | COMPANION_IDS
        or any(row.get("ready") is not True for row in workers.values())
        or any(
            workers[worker_id].get("container_id")
            != prior[worker_id].get("container_id")
            for worker_id in RESET_IDS | COMPANION_IDS
        )
        or any(
            workers[worker_id].get("container_creation_avoided") is not True
            for worker_id in RESET_IDS
        )
        or any(
            algorithm.xp_active_client_session_count(row) != 0
            for row in workers.values()
        )
    ):
        raise RuntimeError("stopped XP pair recovery result differs")

    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_stopped_xp_pair_recovery_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(algorithm.FREEZE),
        "freeze_sha256": algorithm.sha(algorithm.FREEZE),
        "predecessor_binding_path": str(predecessor_path),
        "predecessor_binding_sha256": sha(predecessor_path),
        "failed_run_state_path": str(failed_path),
        "failed_run_state_sha256": sha(failed_path),
        "crash_evidence": crash_evidence,
        "workers": [workers[worker_id] for worker_id in sorted(workers)],
        "reset_worker_ids": sorted(RESET_IDS),
        "companion_worker_ids": sorted(COMPANION_IDS),
        "reset_concurrency": 2,
        "worker_reset_finished_seconds": finished,
        "elapsed_seconds": time.monotonic() - started,
        "workload_started": False,
        "formal_outer_started": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
    })
    cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers={worker_id: workers[worker_id] for worker_id in RESET_IDS},
        binding_path=binding,
        cleanup_path=output / "quarantine_cleanup.json",
    )
    print(json.dumps({
        "status": "PASS",
        "binding": str(binding),
        "elapsed_seconds": time.monotonic() - started,
        "quarantine_deleted_bytes": cleanup["deleted_bytes"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
