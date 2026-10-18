#!/usr/bin/env python3
"""Restore the two active XP w1 planes after a formal rank timeout."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_v5 as reset
from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm
from scripts.recover_mysql_algorithm_c_all_xp_after_timeout_v1 import (
    atomic, load, recorded_client_pids, sha,
)


RESET_IDS = {"xp_cpu_w1", "xp_io_w1"}
COMPANION_IDS = {"xp_cpu_w2", "xp_io_w2"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--failed-root", type=Path, required=True)
    parser.add_argument("--timeout-rank", type=int, required=True)
    parser.add_argument("--reset-binding", type=Path, required=True)
    parser.add_argument("--companion-binding", type=Path, required=True)
    args = parser.parse_args()
    if (
        re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None
        or args.timeout_rank not in range(1, 6)
    ):
        parser.error("safe run id and timeout rank 1..5 required")

    failed_root = args.failed_root.resolve()
    state_path = failed_root / "run_state.json"
    batch_root = (
        failed_root / f"outer_014/top5_batch_{args.timeout_rank}"
    )
    batch_path = batch_root / "batch_result.json"
    reset_root = (
        failed_root / "xp_resets"
        / f"outer_014_batch_{args.timeout_rank + 1}"
    )
    state, batch = load(state_path), load(batch_path)
    reset_binding = load(args.reset_binding.resolve())
    companion_binding = load(args.companion_binding.resolve())
    active_prior = {
        str(row["worker_id"]): dict(row)
        for row in (reset_binding.get("workers") or [])
        if row.get("worker_id") in RESET_IDS
    }
    companion_prior = {
        str(row["worker_id"]): dict(row)
        for row in (companion_binding.get("workers") or [])
        if row.get("worker_id") in COMPANION_IDS
    }
    prior = {**active_prior, **companion_prior}
    timeout_tasks = {
        str(row.get("task")) for row in (batch.get("failures") or [])
        if str(row.get("error") or "").startswith("TimeoutExpired:")
        and "timed out after 900 seconds" in str(row.get("error") or "")
    }
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or not str(state.get("error") or "").startswith(
            "RuntimeError: XP active client sessions were not proven drained "
        )
        or int(state.get("outer_attempt") or 0) != 13
        or batch.get("status") != "FAILED_CLOSED"
        or not {"pair_1", *RESET_IDS}.issubset(timeout_tasks)
        or not (900 <= float(batch.get("elapsed_seconds") or 0) <= 910)
        or set(active_prior) != RESET_IDS
        or set(companion_prior) != COMPANION_IDS
        or set(prior) != RESET_IDS | COMPANION_IDS
        or reset_binding.get("status") != "PASS"
        or companion_binding.get("status") != "PASS"
        or not reset_root.is_dir()
        or list(reset_root.rglob("*"))
        or (
            failed_root
            / f"outer_014/top5_batch_{args.timeout_rank + 1}/batch_result.json"
        ).exists()
        or list((failed_root / "outer_014").glob("*.decision.json"))
    ):
        raise RuntimeError("timed-out XP pair recovery authority differs")

    controller_pid = int(state.get("pid") or 0)
    if controller_pid > 0 and Path(f"/proc/{controller_pid}").exists():
        raise RuntimeError("failed formal controller remains alive")
    client_pids = recorded_client_pids(batch_root)
    live_client_pids = sorted(
        pid for pid in client_pids if Path(f"/proc/{pid}").exists()
    )
    if live_client_pids:
        raise RuntimeError(f"timed-out benchmark clients remain: {live_client_pids}")

    freeze_path = Path(str(state.get("freeze_path") or "")).resolve()
    freeze = load(freeze_path)
    if (
        state.get("freeze_sha256") != sha(freeze_path)
        or reset_binding.get("freeze_sha256") != sha(freeze_path)
        or companion_binding.get("freeze_sha256") != sha(freeze_path)
    ):
        raise RuntimeError("timed-out XP pair freeze binding differs")
    frozen = {
        str(row["worker_id"]): dict(row)
        for row in freeze.get("workers") or []
        if row.get("worker_id") in RESET_IDS | COMPANION_IDS
    }
    if set(frozen) != RESET_IDS | COMPANION_IDS:
        raise RuntimeError("frozen XP worker set differs")
    for worker_id in RESET_IDS | COMPANION_IDS:
        inspected = reset._docker_json("inspect", frozen[worker_id]["container"])
        if inspected.get("Id") != prior[worker_id].get("container_id"):
            raise RuntimeError(f"{worker_id} live predecessor identity differs")
    if any(
        algorithm.xp_active_client_session_count(prior[worker_id]) != 0
        for worker_id in COMPANION_IDS
    ):
        raise RuntimeError("XP companion session count differs")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh recovery output exists: {output}")
    stage = output / "xp_resets/timed_out_xp_pair_v2"
    stage.mkdir(parents=True, mode=0o700)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / output.name / stage.name
    )
    started = time.monotonic()
    reset_rows: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-pair-recovery") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze, frozen[worker_id], prior[worker_id], quarantine, stage,
                False, "formal_point", True, False, False, False,
            ): worker_id
            for worker_id in sorted(RESET_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            reset_rows[worker_id] = future.result()
            print(json.dumps({
                "worker_id": worker_id,
                "reset_complete": True,
                "elapsed_seconds": time.monotonic() - started,
            }), flush=True)

    account_root = stage / "accounts"
    account_root.mkdir()
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-pair-accounts") as pool:
        futures = {
            pool.submit(
                users._run_worker, reset_rows[worker_id], account_root, True, False,
            ): worker_id
            for worker_id in sorted(RESET_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            reset_rows[worker_id].update(future.result())

    workers = {
        **{worker_id: reset_rows[worker_id] for worker_id in RESET_IDS},
        **{worker_id: prior[worker_id] for worker_id in COMPANION_IDS},
    }
    if (
        set(workers) != RESET_IDS | COMPANION_IDS
        or any(row.get("ready") is not True for row in workers.values())
        or any(
            algorithm.xp_active_client_session_count(row) != 0
            for row in workers.values()
        )
    ):
        raise RuntimeError("timed-out XP pair recovery readback differs")

    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_timed_out_xp_pair_recovery_v2",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "failed_state": {"path": str(state_path), "sha256": sha(state_path)},
        "timeout_batch": {"path": str(batch_path), "sha256": sha(batch_path)},
        "predecessor_reset_binding": {
            "path": str(args.reset_binding.resolve()),
            "sha256": sha(args.reset_binding.resolve()),
        },
        "predecessor_companion_binding": {
            "path": str(args.companion_binding.resolve()),
            "sha256": sha(args.companion_binding.resolve()),
        },
        "freeze_path": str(freeze_path),
        "freeze_sha256": sha(freeze_path),
        "timeout_rank": args.timeout_rank,
        "resume_rank": args.timeout_rank + 1,
        "recorded_client_pids": sorted(client_pids),
        "live_recorded_client_pids": [],
        "workers": [workers[worker_id] for worker_id in sorted(workers)],
        "reset_worker_ids": sorted(RESET_IDS),
        "companion_worker_ids": sorted(COMPANION_IDS),
        "reset_concurrency": 2,
        "workload_started": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "elapsed_seconds": time.monotonic() - started,
        "recovery_source_path": str(Path(__file__).resolve()),
        "recovery_source_sha256": sha(Path(__file__).resolve()),
    })
    cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers=reset_rows,
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
