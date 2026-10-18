#!/usr/bin/env python3
"""Pilot bounded discard-only fast shutdown on all four XP data planes."""

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


def active_failed_run_clients(run_id: str) -> list[str]:
    rows = subprocess.run(
        ["ps", "-eo", "pid=,args="], capture_output=True, text=True,
        check=True, timeout=30,
    ).stdout.splitlines()
    return [
        row.strip() for row in rows
        if run_id in row and (
            "com.hybench.HyBench" in row
            or "benchbase.jar" in row
            or "mysql_job_tpch_parallel_adapter.py" in row
            or "/scripts/run_mysql_four_scene_algorithm_c_" in row
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--failed-state", type=Path, required=True)
    parser.add_argument("--last-reset-binding", type=Path, required=True)
    parser.add_argument("--resume-interrupted", action="store_true")
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run-id")
    failed_path = args.failed_state.expanduser().resolve()
    reset_path = args.last_reset_binding.expanduser().resolve()
    failed = load(failed_path)
    prior_reset = load(reset_path)
    freeze = load(FREEZE)
    failed_run_id = failed_path.parent.name
    controller_pid = int(failed.get("pid") or 0)
    clients = active_failed_run_clients(failed_run_id)
    workers = {
        str(row["worker_id"]): dict(row)
        for row in (prior_reset.get("workers") or [])
    }
    failure_match = re.search(
        r"outer (\d+) batch ([123]) failed closed",
        str(failed.get("error") or ""),
    )
    expected_reset_suffix = None
    if failure_match is not None:
        expected_reset_suffix = (
            f"xp_resets/outer_{int(failure_match.group(1)):03d}_"
            f"batch_{int(failure_match.group(2))}/reset_binding.json"
        )
    if (
        failed.get("status") not in {"FAILED_CLOSED", "PILOT_FAILED_CLOSED"}
        or failed.get("stage") != "FAILED"
        or (
            failed.get("status") == "PILOT_FAILED_CLOSED"
            and (
                failed.get("formal_evidence_eligible") is not False
                or failed.get("accepted_state_credit_prohibited") is not True
            )
        )
        or failure_match is None
        or expected_reset_suffix is None
        or not str(reset_path).endswith(expected_reset_suffix)
        or failed_path.parent not in reset_path.parents
        or controller_pid <= 0
        or Path(f"/proc/{controller_pid}").exists()
        or clients
        or prior_reset.get("status") != "PASS"
        or prior_reset.get("freeze_sha256") != sha(FREEZE)
        or set(workers) != XP_IDS
        or any(row.get("ready") is not True for row in workers.values())
    ):
        raise RuntimeError(
            "discard reset pilot authority differs: "
            f"controller_pid={controller_pid} clients={clients!r}"
        )
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists() and not args.resume_interrupted:
        raise RuntimeError(f"fresh pilot output exists: {output}")
    if args.resume_interrupted and not output.is_dir():
        raise RuntimeError(f"interrupted pilot output is absent: {output}")
    output.mkdir(parents=True, mode=0o700, exist_ok=args.resume_interrupted)
    token = "discard_fast_shutdown_pilot_v2"
    reset_started = time.monotonic()
    stage = output / "xp_resets" / token
    stage.mkdir(parents=True, mode=0o700, exist_ok=args.resume_interrupted)
    endpoint_observations: dict[str, dict[str, Any]] = {}
    pre_reset_path = stage / "pre_reset_session_drain.json"
    if args.resume_interrupted:
        pre_reset = load(pre_reset_path)
        if (
            pre_reset.get("status") != "PASS_DISCARD_ONLY"
            or pre_reset.get("failed_run_client_process_inventory") != []
            or set(pre_reset.get("endpoint_observations") or {}) != XP_IDS
        ):
            raise RuntimeError("interrupted pilot drain authority differs")
        endpoint_observations = dict(pre_reset["endpoint_observations"])
    else:
        for worker_id in sorted(XP_IDS):
            try:
                count = algorithm.xp_active_client_session_count(workers[worker_id])
                endpoint_observations[worker_id] = {
                    "status": (
                        "RESPONSIVE"
                        if count == 0
                        else "RESIDUAL_ACTIVE_AFTER_FAILED_WORKLOAD"
                    ),
                    "active_sessions": count,
                    "failed_run_client_process_inventory_empty": True,
                    "old_datadir_discard_only": True,
                }
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                endpoint_observations[worker_id] = {
                    "status": "UNRESPONSIVE_AFTER_FAILED_WORKLOAD",
                    "active_sessions": None,
                    "error": f"{type(error).__name__}: {error}",
                    "failed_run_client_process_inventory_empty": True,
                }
        algorithm.atomic(pre_reset_path, {
            "schema_version": "mysql_four_scene_xp_unresponsive_discard_authority_v1",
            "created_at": algorithm.now(),
            "status": "PASS_DISCARD_ONLY",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "failed_run_client_process_inventory": clients,
            "endpoint_observations": endpoint_observations,
            "old_datadirs_never_reused": True,
        })
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"])) / "quarantine"
        / output.name / token
    )
    reset_rows: dict[str, dict[str, Any]] = {}
    finished_seconds: dict[str, float] = {}
    if args.resume_interrupted:
        for worker_id in sorted(XP_IDS):
            evidence = stage / worker_id / "reset_worker.json"
            if not evidence.is_file():
                continue
            row = load(evidence)
            inspect = algorithm.reset._docker_json(
                "inspect", str(row.get("container") or ""),
            )
            if (
                row.get("worker_id") != worker_id
                or row.get("ready") is not True
                or inspect.get("Id") != row.get("container_id")
                or (inspect.get("State") or {}).get("Running") is not True
            ):
                raise RuntimeError(
                    f"{worker_id} interrupted completed reset identity differs"
                )
            row["worker_evidence_path"] = str(evidence)
            row["worker_evidence_sha256"] = sha(evidence)
            reset_rows[worker_id] = row
            finished_seconds[worker_id] = 0.0
    pending_ids = sorted(XP_IDS - set(reset_rows))
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="discard-reset-v2") as pool:
        futures = {
            pool.submit(
                algorithm.reset._prepare_fresh_xp,
                freeze, frozen[worker_id], workers[worker_id],
                quarantine, stage, args.resume_interrupted, "formal_point", True,
                False, False, args.resume_interrupted,
            ): worker_id
            for worker_id in pending_ids
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            reset_rows[worker_id] = future.result()
            finished_seconds[worker_id] = time.monotonic() - reset_started
    account_root = stage / "accounts"
    account_root.mkdir(exist_ok=args.resume_interrupted)
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="discard-users-v2") as pool:
        futures = {
            pool.submit(
                algorithm.users._run_worker,
                reset_rows[worker_id], account_root, True, False,
            ): worker_id
            for worker_id in XP_IDS
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            workers[worker_id] = {
                **frozen[worker_id], **reset_rows[worker_id], **future.result(),
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
        "worker_reset_finished_seconds": finished_seconds,
        "elapsed_seconds": time.monotonic() - reset_started,
        "unresponsive_failed_workload_discard_recovery": True,
        "endpoint_observations": endpoint_observations,
    })
    result = load(reset_binding)
    result_workers = [dict(row) for row in (result.get("workers") or [])]
    shutdown_rows = [
        audit
        for worker in result_workers
        for audit in (worker.get("mysql_shutdown_audit") or [])
    ]
    if (
        result.get("status") != "PASS"
        or {row.get("worker_id") for row in result_workers} != XP_IDS
        or len(shutdown_rows) != 4
        or any(
            not (
                row.get("innodb_fast_shutdown_readback") == 2
                or (
                    row.get("innodb_fast_shutdown_readback") is None
                    and row.get("method") in {
                        "discard_fast_shutdown_sigkill_fallback",
                        "discard_zombie_wrapper_forced_remove",
                        "discard_zombie_wrapper_host_pid_kill",
                        "discard_pending_sigkill_completed_after_blkio_lift",
                        "discard_interrupted_completion_recovered",
                        "discard_failed_workload_nonzero_exit_recovered",
                        "discard_failed_workload_nonzero_exit",
                    }
                )
            )
            for row in shutdown_rows
        )
        or any(row.get("predecessor_discard_only") is not True for row in shutdown_rows)
        or any(row.get("discarded_datadir_never_reused") is not True for row in shutdown_rows)
        or any(row.get("innodb_fast_shutdown") != 1 for row in result_workers)
        or any(
            not (
                (
                    row.get("container_creation_avoided") is True
                    and row.get("container_start_mode")
                    == "restart_stopped_container_with_replaced_bind_sources"
                )
                or (
                    row.get("container_creation_avoided") is False
                    and row.get("container_start_mode") == "fresh_docker_run"
                    and len(row.get("mysql_shutdown_audit") or []) == 1
                    and (row["mysql_shutdown_audit"][0]).get(
                        "container_absent_after_shutdown"
                    ) is True
                    and (row["mysql_shutdown_audit"][0]).get("method")
                    in {
                        "discard_zombie_wrapper_forced_remove",
                        "discard_interrupted_completion_recovered",
                    }
                )
            )
            for row in result_workers
        )
        or any(row.get("credential_initialization") != "mysqld_init_file" for row in result_workers)
        or any(row.get("reset_bootstrap_attempts") != [] for row in result_workers)
        or any(row.get("ready") is not True for row in result_workers)
    ):
        raise RuntimeError("discard reset pilot result differs")
    cleanup_path = stage / "quarantine_cleanup.json"
    cleanup = algorithm.reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers={row["worker_id"]: row for row in result_workers},
        binding_path=reset_binding,
        cleanup_path=cleanup_path,
    )
    document = {
        "schema_version": "mysql_algorithm_c_discard_reset_pilot_v2",
        "status": "PASS_RECOVERY_ONLY" if args.resume_interrupted else "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "workload_started": False,
        "formal_outer_started": False,
        "failed_state_path": str(failed_path),
        "failed_state_sha256": sha(failed_path),
        "last_reset_binding_path": str(reset_path),
        "last_reset_binding_sha256": sha(reset_path),
        "reset_binding_path": str(reset_binding),
        "reset_binding_sha256": sha(reset_binding),
        "quarantine_cleanup_path": str(cleanup_path),
        "quarantine_cleanup_sha256": sha(cleanup_path),
        "quarantine_deleted_bytes": cleanup["deleted_bytes"],
        "quarantine_retention_policy": cleanup["policy"],
        "elapsed_seconds": result["elapsed_seconds"],
        "runtime_validation_eligible": not args.resume_interrupted,
        "interrupted_recovery": args.resume_interrupted,
        "shutdown_rows": shutdown_rows,
        "reset_source_path": str(Path(algorithm.reset.__file__).resolve()),
        "reset_source_sha256": sha(Path(algorithm.reset.__file__).resolve()),
        "algorithm_source_path": str(Path(algorithm.__file__).resolve()),
        "algorithm_source_sha256": sha(Path(algorithm.__file__).resolve()),
        "workers": result_workers,
    }
    pilot = output / "pilot_result.json"
    atomic(pilot, document)
    print(json.dumps({
        "status": "PASS", "pilot": str(pilot),
        "reset_binding": str(reset_binding),
        "elapsed_seconds": document["elapsed_seconds"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
