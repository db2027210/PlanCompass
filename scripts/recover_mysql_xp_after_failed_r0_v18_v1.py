#!/usr/bin/env python3
"""Restore all four v18 XP planes after a failed fresh formal R0."""

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
from scripts import reset_mysql_xp_data_planes_v6 as reset
from scripts import run_mysql_four_scene_algorithm_c_v2 as algorithm


FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)
HISTORY = ROOT / "history/mysql_four_scene_20260809"
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--prepared-binding", type=Path, required=True)
    parser.add_argument("--failed-r0-binding", type=Path, required=True)
    parser.add_argument("--copy-workers", type=int, default=2, choices=(1, 2, 4))
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")

    prepared_path = args.prepared_binding.expanduser().resolve()
    failed_path = args.failed_r0_binding.expanduser().resolve()
    if prepared_path.parent.parent != HISTORY or failed_path.parent.parent != HISTORY:
        raise RuntimeError("recovery evidence is outside the formal history root")
    prepared = load(prepared_path)
    failed = load(failed_path)
    freeze = load(FREEZE)
    prior = {
        str(row["worker_id"]): dict(row)
        for row in (prepared.get("workers") or [])
        if row.get("worker_id") in XP_IDS
    }
    frozen = {
        str(row["worker_id"]): dict(row)
        for row in (freeze.get("workers") or [])
        if row.get("worker_id") in XP_IDS
    }
    if (
        prepared.get("status") != "PREPARED"
        or prepared.get("formal_scope") != "mysql_only"
        or prepared.get("cross_engine_comparison_prohibited") is not True
        or prepared.get("formal_outer_started") is not False
        or prepared.get("freeze_sha256") != sha(FREEZE)
        or set(prior) != XP_IDS
        or set(frozen) != XP_IDS
        or failed.get("status") != "R0_FAILED_CLOSED"
        or failed.get("formal_scope") != "mysql_only"
        or failed.get("cross_engine_comparison_prohibited") is not True
        or failed.get("freeze_sha256") != sha(FREEZE)
        or failed.get("prepared_binding_path") != str(prepared_path)
        or failed.get("prepared_binding_sha256") != sha(prepared_path)
        or failed.get("fresh_r0_complete") is not False
        or failed.get("formal_outer_launch_authorized") is not False
        or any((failed.get("accepted_rounds") or {}).values())
    ):
        raise RuntimeError("v18 failed-R0 recovery authority differs")
    for worker_id in XP_IDS:
        if prior[worker_id].get("container") != frozen[worker_id].get("container"):
            raise RuntimeError(f"{worker_id} frozen container identity differs")
        if "0708" in str(prior[worker_id].get("container") or ""):
            raise RuntimeError(f"{worker_id} resolves to forbidden 0708 container")
        inspected = reset._docker_json("inspect", prior[worker_id]["container"])
        if inspected.get("Id") != prior[worker_id].get("container_id"):
            raise RuntimeError(f"{worker_id} live predecessor identity differs")

    output = HISTORY / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh recovery output exists: {output}")
    stage = output / "xp_reset"
    stage.mkdir(parents=True, mode=0o700)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / args.run_id / "xp_reset"
    )
    started = time.monotonic()
    rows: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(
        max_workers=args.copy_workers, thread_name_prefix="failed-r0-xp-reset"
    ) as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze, frozen[worker_id], prior[worker_id], quarantine, stage,
                False, "invalid_r0", False, False, False, False,
            ): worker_id
            for worker_id in sorted(XP_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            rows[worker_id] = future.result()
            print(json.dumps({
                "worker_id": worker_id,
                "reset_complete": True,
                "elapsed_seconds": time.monotonic() - started,
            }), flush=True)

    account_root = stage / "accounts"
    account_root.mkdir()
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="failed-r0-xp-accounts") as pool:
        futures = {
            pool.submit(users._run_worker, rows[worker_id], account_root, True, False): worker_id
            for worker_id in sorted(XP_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            rows[worker_id].update(future.result())

    if (
        set(rows) != XP_IDS
        or any(row.get("ready") is not True for row in rows.values())
        or any(algorithm.xp_active_client_session_count(row) != 0 for row in rows.values())
    ):
        raise RuntimeError("v18 failed-R0 XP recovery readback differs")

    binding = output / "recovered_xp_binding.json"
    atomic(binding, {
        "schema_version": "mysql_v18_xp_recovery_after_failed_r0_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "prepared_binding": {"path": str(prepared_path), "sha256": sha(prepared_path)},
        "failed_r0_binding": {"path": str(failed_path), "sha256": sha(failed_path)},
        "workers": [rows[worker_id] for worker_id in sorted(XP_IDS)],
        "reset_worker_ids": sorted(XP_IDS),
        "reset_concurrency": args.copy_workers,
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
        reset_workers=rows,
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
