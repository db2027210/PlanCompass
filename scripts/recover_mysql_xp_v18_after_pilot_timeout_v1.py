#!/usr/bin/env python3
"""Recover allowlisted v18 XP planes after a non-formal workload timeout."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as users
from scripts import reset_mysql_xp_data_planes_v5 as reset


FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"
PREDECESSOR = ROOT / "history/mysql_four_scene_20260809/xp_reset_v18_between_stability_waves_20260813T1550p0800/reset_binding.json"
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}
SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-interrupted", action="store_true")
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run-id")
    freeze, predecessor = load(FREEZE), load(PREDECESSOR)
    prior = {row["worker_id"]: row for row in predecessor.get("workers") or []}
    frozen = {row["worker_id"]: row for row in freeze.get("workers") or []}
    if (
        freeze.get("schema_version") != SCHEMA
        or predecessor.get("status") != "PASS"
        or predecessor.get("freeze_sha256") != sha(FREEZE)
        or set(prior) != XP_IDS
        or set(frozen).intersection(XP_IDS) != XP_IDS
        or any(row.get("ready") is not True for row in prior.values())
        or any("0708" in str(frozen[worker_id]["container"]) for worker_id in XP_IDS)
    ):
        raise RuntimeError("v18 pilot-timeout recovery authority differs")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists() and not args.resume_interrupted:
        raise RuntimeError(f"fresh recovery output exists: {output}")
    if args.resume_interrupted and not output.is_dir():
        raise RuntimeError(f"interrupted recovery output absent: {output}")
    previous = load(output / "reset_binding.json") if args.resume_interrupted else {}
    if args.resume_interrupted and previous.get("status") != "FAILED_CLOSED":
        raise RuntimeError("interrupted recovery binding differs")
    rows: dict[str, dict] = {
        row["worker_id"]: row for row in (previous.get("workers") or [])
    }
    requested = XP_IDS - set(rows)
    if args.resume_interrupted and not requested:
        raise RuntimeError("interrupted recovery has no failed workers")
    output.mkdir(parents=True, mode=0o700, exist_ok=args.resume_interrupted)
    stage = output / "xp_reset"
    stage.mkdir(mode=0o700, exist_ok=args.resume_interrupted)
    quarantine = Path(str(freeze["storage"]["runtime_root"])) / "quarantine" / args.run_id
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-v18-timeout-recovery") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze, frozen[worker_id], prior[worker_id], quarantine, stage,
                args.resume_interrupted, "formal_point", True, False,
                False, args.resume_interrupted,
            ): worker_id
            for worker_id in sorted(requested)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                rows[worker_id] = future.result()
                print(json.dumps({"worker_id": worker_id, "reset": "PASS"}), flush=True)
            except BaseException as error:
                failures.append({
                    "worker_id": worker_id,
                    "error": f"{type(error).__name__}: {error}",
                })
                print(json.dumps(failures[-1]), flush=True)
    if not failures:
        accounts = output / "accounts"
        accounts.mkdir(mode=0o700, exist_ok=args.resume_interrupted)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-v18-timeout-users") as pool:
            futures = {
                pool.submit(users._run_worker, rows[worker_id], accounts, True, False): worker_id
                for worker_id in sorted(XP_IDS)
            }
            for future in as_completed(futures):
                worker_id = futures[future]
                try:
                    rows[worker_id] = {**rows[worker_id], **future.result()}
                except BaseException as error:
                    failures.append({
                        "worker_id": worker_id,
                        "error": f"{type(error).__name__}: {error}",
                    })
    passed = not failures and set(rows) == XP_IDS and all(
        row.get("ready") is True for row in rows.values()
    )
    document = {
        "schema_version": "mysql_four_scene_xp_v18_pilot_timeout_recovery_v1",
        "status": "PASS" if passed else "FAILED_CLOSED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "predecessor_binding_path": str(PREDECESSOR),
        "predecessor_binding_sha256": sha(PREDECESSOR),
        "timeout_recovery": True,
        "allowlisted_containers_only": True,
        "external_container_mutation": False,
        "workers": [rows[key] for key in sorted(rows)],
        "failures": failures,
        "workload_started": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    binding = output / "reset_binding.json"
    atomic(binding, document)
    if passed:
        reset.purge_completed_discard_quarantine(
            quarantine_root=quarantine, reset_workers=rows,
            binding_path=binding, cleanup_path=output / "quarantine_cleanup.json",
        )
    print(json.dumps({"status": document["status"], "binding": str(binding)}))
    return 0 if passed else 4


if __name__ == "__main__":
    raise SystemExit(main())
