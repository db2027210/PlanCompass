#!/usr/bin/env python3
"""Pilot the single-start XP reset after an explicitly excluded outer run."""

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

from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm


FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
INTERRUPTED_RUN_ID = (
    "four_scene_algorithm_c_boundary_resume_outer2_optimized_v2_"
    "20260812T035930p0800"
)


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
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def active_formal_clients() -> list[str]:
    inventory = subprocess.run(
        ["ps", "-eo", "pid=,args="], capture_output=True, text=True,
        check=True, timeout=30,
    ).stdout.splitlines()
    # PostgreSQL's independently isolated BenchBase lanes are allowed by the
    # mysql_only policy and are evaluated by the rolling preflight.  This gate
    # rejects only clients/controllers belonging to the interrupted MySQL run.
    return [
        line.strip() for line in inventory
        if (
            "com.hybench.HyBench" in line
            or "mysql_job_tpch_parallel_adapter.py" in line
            or "/scripts/run_mysql_four_scene_algorithm_c_" in line
            or (
                "benchbase.jar" in line
                and "/history/mysql_four_scene_20260809/" in line
            )
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--prepared-binding", type=Path, required=True)
    parser.add_argument("--interrupted-state", type=Path, required=True)
    parser.add_argument("--point-reset-binding", type=Path, required=True)
    parser.add_argument("--completed-worker-evidence", type=Path)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run-id")
    prepared_path = args.prepared_binding.expanduser().resolve()
    interrupted_path = args.interrupted_state.expanduser().resolve()
    prepared = load(prepared_path)
    interrupted = load(interrupted_path)
    point_reset_path = args.point_reset_binding.expanduser().resolve()
    point_reset = load(point_reset_path)
    freeze = load(FREEZE)
    workers = {
        str(row["worker_id"]): dict(row) for row in (prepared.get("workers") or [])
    }
    point_workers = {
        str(row["worker_id"]): dict(row)
        for row in (point_reset.get("workers") or [])
    }
    workers.update(point_workers)
    controller_pid = int(interrupted.get("pid") or 0)
    clients = active_formal_clients()
    if (
        interrupted_path.parent.name != INTERRUPTED_RUN_ID
        or interrupted.get("status") != "RUNNING"
        or interrupted.get("stage") not in {
            "OUTER_003_BATCH_1_MEASURING",
            "OUTER_003_BATCH_2_RESETTING_XP",
        }
        or controller_pid <= 0
        or Path(f"/proc/{controller_pid}").exists()
        or clients
        or prepared.get("status") != "PREPARED"
        or prepared.get("formal_scope") != "mysql_only"
        or prepared.get("cross_engine_comparison_prohibited") is not True
        or prepared.get("freeze_sha256") != sha(FREEZE)
        or set(workers) != {str(row["worker_id"]) for row in freeze["workers"]}
        or not XP_IDS.issubset(workers)
        or point_reset.get("status") != "PASS"
        or point_reset.get("freeze_sha256") != sha(FREEZE)
        or set(point_workers) != XP_IDS
    ):
        raise RuntimeError(
            "single-start reset pilot authority differs: "
            f"clients={clients!r} controller_pid={controller_pid}"
        )
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh pilot output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    exclusion = {
        "schema_version": "mysql_algorithm_c_interrupted_outer_exclusion_v1",
        "status": "INTERRUPTED_EXCLUDED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "interrupted_run_id": INTERRUPTED_RUN_ID,
        "interrupted_state_path": str(interrupted_path),
        "interrupted_state_sha256": sha(interrupted_path),
        "last_complete_point_reset_path": str(point_reset_path),
        "last_complete_point_reset_sha256": sha(point_reset_path),
        "excluded_outer_attempt": 3,
        "excluded_batches": [1, 2, 3],
        "reason": (
            "controller stopped during incomplete batch 1 before activating a "
            "new source-bound reset implementation"
        ),
        "may_contribute_to_convergence": False,
        "resume_checkpoint_outer_attempt": 2,
        "formal_clients_after_stop": clients,
    }
    atomic(output / "interrupted_run_exclusion.json", exclusion)
    token = "single_start_reset_pilot_v1"
    reset_started = time.monotonic()
    stage = output / "xp_resets" / token
    stage.mkdir(parents=True, mode=0o700)
    drain_counts: dict[str, int] = {}
    drain_states: dict[str, str] = {}
    for worker_id in sorted(XP_IDS):
        container = str(workers[worker_id]["container"])
        existing = algorithm.reset._docker_run([
            "docker", "ps", "-aq", "--filter", f"name=^{container}$",
        ]).stdout.strip()
        if not existing:
            drain_states[worker_id] = "ABSENT_AFTER_INTERRUPTED_RESET"
            drain_counts[worker_id] = 0
            continue
        state = algorithm.reset._docker_json("inspect", container).get("State") or {}
        if state.get("Running") is not True:
            drain_states[worker_id] = "STOPPED_AFTER_INTERRUPTED_RESET"
            drain_counts[worker_id] = 0
            continue
        drain_states[worker_id] = "RUNNING_IDLE"
        drain_counts[worker_id] = algorithm.xp_active_client_session_count(
            workers[worker_id]
        )
    if any(drain_counts.values()):
        raise RuntimeError(f"XP sessions remain after interrupted run: {drain_counts}")
    drain = {
        "status": "PASS",
        "timeout_seconds": 0,
        "poll_seconds": 0,
        "elapsed_seconds": 0.0,
        "polls": [{
            "observed_at": algorithm.now(),
            "active_sessions": drain_counts,
            "container_states": drain_states,
        }],
        "interrupted_reset_aware": True,
    }
    algorithm.atomic(stage / "pre_reset_session_drain.json", {
        "schema_version": "mysql_four_scene_xp_pre_reset_session_drain_v1",
        "created_at": algorithm.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        **drain,
    })
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    fresh_quarantine = (
        Path(str(freeze["storage"]["runtime_root"])) / "quarantine"
        / output.name / token
    )
    interrupted_quarantine = (
        Path(str(freeze["storage"]["runtime_root"])) / "quarantine"
        / INTERRUPTED_RUN_ID / "outer_003_batch_2"
    )
    reset_rows: dict[str, dict[str, Any]] = {}
    if args.completed_worker_evidence is not None:
        completed_path = args.completed_worker_evidence.expanduser().resolve()
        completed = load(completed_path)
        completed_worker_id = str(completed.get("worker_id") or "")
        if (
            completed_worker_id not in XP_IDS
            or completed.get("credential_initialization") != "mysqld_init_file"
            or completed.get("reset_bootstrap_attempts") != []
            or completed.get("ready") is not True
        ):
            raise RuntimeError("completed single-start worker evidence differs")
        completed["worker_evidence_path"] = str(completed_path)
        completed["worker_evidence_sha256"] = sha(completed_path)
        reset_rows[completed_worker_id] = completed
    worker_finished_seconds: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="single-start-pilot") as pool:
        futures = {}
        for worker_id in sorted(XP_IDS - set(reset_rows)):
            futures[pool.submit(
                algorithm.reset._prepare_fresh_xp,
                freeze, frozen[worker_id], workers[worker_id],
                fresh_quarantine, stage, True, "formal_point", False,
                False, False,
            )] = worker_id
        for future in as_completed(futures):
            worker_id = futures[future]
            reset_rows[worker_id] = future.result()
            worker_finished_seconds[worker_id] = time.monotonic() - reset_started
    account_root = stage / "accounts"
    account_root.mkdir()
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="single-start-accounts") as pool:
        futures = {
            pool.submit(
                algorithm.users._run_worker,
                reset_rows[worker_id], account_root, True, False,
            ): worker_id
            for worker_id in XP_IDS
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            account = future.result()
            workers[worker_id] = {
                **frozen[worker_id], **reset_rows[worker_id], **account,
            }
    reset_binding = stage / "reset_binding.json"
    algorithm.atomic(reset_binding, {
        "schema_version": "mysql_four_scene_algorithm_c_xp_point_reset_v1",
        "status": "PASS",
        "created_at": algorithm.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_sha256": sha(FREEZE),
        "workers": [workers[worker_id] for worker_id in sorted(XP_IDS)],
        "reset_concurrency": 4,
        "worker_reset_finished_seconds": worker_finished_seconds,
        "elapsed_seconds": time.monotonic() - reset_started,
        "recovered_from_interrupted_batch_2_reset": True,
        "interrupted_quarantine_path": str(interrupted_quarantine),
        "completed_worker_reused_from_failed_pilot": sorted(
            XP_IDS & set(reset_rows) - set(worker_finished_seconds)
        ),
    })
    reset_result = load(reset_binding)
    xp_workers = [dict(row) for row in (reset_result.get("workers") or [])]
    if (
        reset_result.get("status") != "PASS"
        or {row.get("worker_id") for row in xp_workers} != XP_IDS
        or any(row.get("credential_initialization") != "mysqld_init_file" for row in xp_workers)
        or any(row.get("reset_bootstrap_attempts") != [] for row in xp_workers)
        or any(row.get("account_mutation_performed_in_this_run") is not False for row in xp_workers)
        or any(row.get("ready") is not True for row in xp_workers)
    ):
        raise RuntimeError("single-start reset pilot result differs")
    result = {
        "schema_version": "mysql_algorithm_c_single_start_reset_pilot_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "workload_started": False,
        "formal_outer_started": False,
        "reset_binding_path": str(reset_binding),
        "reset_binding_sha256": sha(reset_binding),
        "elapsed_seconds": reset_result["elapsed_seconds"],
        "reset_source_path": str(Path(algorithm.reset.__file__).resolve()),
        "reset_source_sha256": sha(Path(algorithm.reset.__file__).resolve()),
        "algorithm_source_path": str(Path(algorithm.__file__).resolve()),
        "algorithm_source_sha256": sha(Path(algorithm.__file__).resolve()),
        "interrupted_run_exclusion_path": str(output / "interrupted_run_exclusion.json"),
        "interrupted_run_exclusion_sha256": sha(output / "interrupted_run_exclusion.json"),
        "workers": xp_workers,
    }
    atomic(output / "pilot_result.json", result)
    print(json.dumps({
        "status": "PASS", "result": str(output / "pilot_result.json"),
        "reset_binding": str(reset_binding),
        "elapsed_seconds": result["elapsed_seconds"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
