#!/usr/bin/env python3
"""Restore all four XP planes after a timed-out rank leaves SQL unresponsive."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
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


XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
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


def recorded_client_pids(batch_root: Path) -> set[int]:
    result: set[int] = set()
    for path in batch_root.glob("*/client_processes.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for field in ("launcher_pid", "workload_pid"):
                pid = int(row.get(field) or 0)
                if pid > 0:
                    result.add(pid)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--failed-root", type=Path, required=True)
    parser.add_argument("--timeout-root", type=Path, required=True)
    parser.add_argument("--predecessor-binding", type=Path, required=True)
    parser.add_argument("--timeout-rank", type=int, default=2)
    parser.add_argument("--resume-rank", type=int, default=3)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")

    failed_root = args.failed_root.resolve()
    timeout_root = args.timeout_root.resolve()
    predecessor_path = args.predecessor_binding.resolve()
    failed_state_path = failed_root / "run_state.json"
    timeout_state_path = timeout_root / "run_state.json"
    if (
        args.timeout_rank not in range(1, 6)
        or args.resume_rank != args.timeout_rank + 1
        or args.resume_rank not in range(2, 6)
    ):
        parser.error("timeout rank 1..4 and its immediate resume rank required")
    timeout_batch_path = timeout_root / (
        f"outer_014/top5_batch_{args.timeout_rank}/batch_result.json"
    )
    failed_state = load(failed_state_path)
    timeout_state = load(timeout_state_path)
    timeout_batch = load(timeout_batch_path)
    predecessor = load(predecessor_path)
    prior = {
        str(row["worker_id"]): dict(row)
        for row in (predecessor.get("workers") or [])
    }
    timeout_tasks = {
        str(row.get("task")) for row in (timeout_batch.get("failures") or [])
        if str(row.get("error") or "").startswith("TimeoutExpired:")
        and "timed out after 900 seconds" in str(row.get("error") or "")
    }
    if (
        failed_state.get("status") != "FAILED_CLOSED"
        or failed_state.get("stage") != "FAILED"
        or not str(failed_state.get("error") or "").startswith(
            "RuntimeError: XP active client sessions were not proven drained "
        )
        or int(failed_state.get("outer_attempt") or 0) != 13
        or timeout_state.get("status") != "FAILED_CLOSED"
        or timeout_batch.get("status") != "FAILED_CLOSED"
        or not {"pair_1", "xp_cpu_w1", "xp_io_w1"}.issubset(timeout_tasks)
        or not (900 <= float(timeout_batch.get("elapsed_seconds") or 0) <= 910)
        or predecessor.get("status") != "PASS"
        or set(prior) != XP_IDS
        or any("0708" in str(row.get("container") or "") for row in prior.values())
        or (
            failed_root
            / f"outer_014/top5_batch_{args.resume_rank}/batch_result.json"
        ).exists()
        or list(
            (
                failed_root / "xp_resets"
                / f"outer_014_batch_{args.resume_rank}"
            ).rglob("*")
        )
    ):
        raise RuntimeError("all-XP post-timeout recovery authority differs")

    for state in (failed_state, timeout_state):
        controller_pid = int(state.get("pid") or 0)
        if controller_pid > 0 and Path(f"/proc/{controller_pid}").exists():
            raise RuntimeError("failed formal controller remains alive")
    client_pids = recorded_client_pids(
        timeout_root / "outer_014/top5_batch_2"
    )
    live_client_pids = sorted(
        pid for pid in client_pids if Path(f"/proc/{pid}").exists()
    )
    if live_client_pids:
        raise RuntimeError(f"timed-out benchmark clients remain: {live_client_pids}")

    freeze_path = Path(str(failed_state.get("freeze_path") or "")).resolve()
    freeze = load(freeze_path)
    if (
        failed_state.get("freeze_sha256") != sha(freeze_path)
        or predecessor.get("freeze_sha256") != sha(freeze_path)
    ):
        raise RuntimeError("all-XP recovery freeze binding differs")
    frozen = {
        str(row["worker_id"]): dict(row)
        for row in freeze.get("workers") or []
        if row.get("worker_id") in XP_IDS
    }
    if set(frozen) != XP_IDS:
        raise RuntimeError("frozen XP worker set differs")
    for worker_id in XP_IDS:
        inspected = reset._docker_json("inspect", frozen[worker_id]["container"])
        if inspected.get("Id") != prior[worker_id].get("container_id"):
            raise RuntimeError(f"{worker_id} live predecessor identity differs")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh recovery output exists: {output}")
    stage = output / "xp_resets/all_xp_post_timeout_v1"
    stage.mkdir(parents=True, mode=0o700)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / output.name / stage.name
    )
    started = time.monotonic()
    reset_rows: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="all-xp-recovery") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze, frozen[worker_id], prior[worker_id], quarantine, stage,
                False, "formal_point", True, False, False, False,
            ): worker_id
            for worker_id in sorted(XP_IDS)
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
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="all-xp-accounts") as pool:
        futures = {
            pool.submit(
                users._run_worker, reset_rows[worker_id], account_root, True, False,
            ): worker_id
            for worker_id in sorted(XP_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            reset_rows[worker_id].update(future.result())

    if (
        set(reset_rows) != XP_IDS
        or any(row.get("ready") is not True for row in reset_rows.values())
        or any(
            algorithm.xp_active_client_session_count(row) != 0
            for row in reset_rows.values()
        )
    ):
        raise RuntimeError("all-XP post-timeout recovery readback differs")

    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_all_xp_post_timeout_recovery_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "failed_state": {"path": str(failed_state_path), "sha256": sha(failed_state_path)},
        "timeout_state": {"path": str(timeout_state_path), "sha256": sha(timeout_state_path)},
        "timeout_batch": {"path": str(timeout_batch_path), "sha256": sha(timeout_batch_path)},
        "predecessor_binding": {"path": str(predecessor_path), "sha256": sha(predecessor_path)},
        "freeze_path": str(freeze_path),
        "freeze_sha256": sha(freeze_path),
        "recorded_client_pids": sorted(client_pids),
        "live_recorded_client_pids": [],
        "workers": [reset_rows[worker_id] for worker_id in sorted(XP_IDS)],
        "reset_worker_ids": sorted(XP_IDS),
        "reset_concurrency": 4,
        "timeout_rank": args.timeout_rank,
        "resume_rank": args.resume_rank,
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
