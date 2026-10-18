#!/usr/bin/env python3
"""Restore and re-provision all XP workers for the v11 R0 retry."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_v5 as reset


FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260809_v11"
    / "four_scene_effective_resource_successor.json"
)
PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v10_20260809T220000p0800/prepared_binding.json"
)
ACCOUNTS = (
    ROOT / "history/mysql_four_scene_20260809"
    / "benchmark_accounts_native_iq1_iq7_v10_post_restart_20260809T220800p0800"
    / "benchmark_accounts_binding.json"
)
FAILED_R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v10_20260809T221000p0800/r0_binding.json"
)
XP_IDS = ("xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-failed-reset", type=Path)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")
    freeze = load(FREEZE)
    prepared = load(PREPARED)
    accounts = load(ACCOUNTS)
    failed = load(FAILED_R0)
    if (
        freeze.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v11"
        or prepared.get("status") != "PREPARED"
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != sha(PREPARED)
        or failed.get("status") != "R0_FAILED_CLOSED"
        or any((failed.get("accepted_rounds") or {}).values())
    ):
        raise RuntimeError("v11 XP reset authority differs")
    failed_reset = None
    resume_stalled_copy = args.resume_failed_reset is not None
    if args.resume_failed_reset is not None:
        failed_reset_path = args.resume_failed_reset.resolve()
        failed_reset = load(failed_reset_path)
        if (
            failed_reset.get("status") != "FAILED_CLOSED"
            or failed_reset.get("freeze_sha256") != sha(FREEZE)
            or {row.get("worker_id") for row in failed_reset.get("failures") or []}
            != {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1"}
        ):
            raise RuntimeError("resumed v11 failed-reset authority differs")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    quarantine_run_id = (
        args.resume_failed_reset.resolve().parent.name
        if args.resume_failed_reset is not None else args.run_id
    )
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / quarantine_run_id
    )
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    adopted = {row["worker_id"]: row for row in prepared["workers"]}
    account_rows = {row["worker_id"]: row for row in accounts["workers"]}
    prior = {
        worker_id: {**frozen[worker_id], **adopted[worker_id], **account_rows[worker_id]}
        for worker_id in XP_IDS
    }
    reset_rows = []
    failures = []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-v11-reset") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp, freeze, frozen[worker_id], prior[worker_id],
                quarantine, output, resume_stalled_copy, "invalid_r0",
            ): worker_id
            for worker_id in XP_IDS
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                row = future.result()
                reset_rows.append(row)
                print(json.dumps({"worker_id": worker_id, "reset": True}), flush=True)
            except BaseException as error:
                failures.append({
                    "worker_id": worker_id,
                    "error": f"{type(error).__name__}: {error}",
                })
                print(json.dumps(failures[-1]), flush=True)
    provisioned = []
    if not failures:
        account_root = output / "accounts"
        account_root.mkdir()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-v11-users") as pool:
            futures = {
                pool.submit(users._run_worker, row, account_root, False, True): row
                for row in reset_rows
            }
            for future in as_completed(futures):
                row = futures[future]
                try:
                    provisioned.append({**row, **future.result()})
                except BaseException as error:
                    failures.append({
                        "worker_id": row["worker_id"],
                        "error": f"{type(error).__name__}: {error}",
                    })
    provisioned.sort(key=lambda row: row["worker_id"])
    document = {
        "schema_version": "mysql_four_scene_xp_reset_after_failed_r0_v11",
        "status": "PREPARED" if len(provisioned) == 4 and not failures else "FAILED_CLOSED",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "predecessor_prepared_path": str(PREPARED),
        "predecessor_prepared_sha256": sha(PREPARED),
        "predecessor_accounts_path": str(ACCOUNTS),
        "predecessor_accounts_sha256": sha(ACCOUNTS),
        "invalid_r0_path": str(FAILED_R0),
        "invalid_r0_sha256": sha(FAILED_R0),
        "resumed_failed_reset_path": (
            str(args.resume_failed_reset.resolve())
            if args.resume_failed_reset is not None else None
        ),
        "resumed_failed_reset_sha256": (
            sha(args.resume_failed_reset.resolve())
            if args.resume_failed_reset is not None else None
        ),
        "workers": provisioned,
        "failures": failures,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    binding = output / "prepared_binding.json"
    binding.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding.with_name(binding.name + ".sha256").write_text(
        f"{sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    print(json.dumps({"status": document["status"], "binding": str(binding)}))
    return 0 if document["status"] == "PREPARED" else 4


if __name__ == "__main__":
    raise SystemExit(main())
