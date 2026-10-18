#!/usr/bin/env python3
"""Restore all XP data planes after an interrupted, result-less fresh R0."""

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
    / "xp_restore_after_failed_r0_v16_20260810T022000p0800/prepared_binding.json"
)
INTERRUPTED_R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v15_20260810T023710p0800"
)
XP_IDS = ("xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")

    freeze = load(FREEZE)
    prepared = load(PREPARED)
    interrupted = INTERRUPTED_R0.resolve()
    if (
        freeze.get("schema_version")
        != "mysql_disk2_four_scene_two_worker_effective_resource_successor_v11"
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != sha(FREEZE)
        or {row.get("worker_id") for row in prepared.get("workers") or []}
        != set(XP_IDS)
        or prepared.get("formal_outer_started") is not False
        or not interrupted.is_dir()
        or (interrupted / "r0_binding.json").exists()
        or not (interrupted / "resource_samples.jsonl").is_file()
    ):
        raise RuntimeError("interrupted-R0 XP reset authority differs")

    interruption_evidence = {}
    for worker_id in XP_IDS:
        command_path = interrupted / worker_id / "command.json"
        clients_path = interrupted / worker_id / "client_processes.jsonl"
        command = load(command_path)
        client_lines = clients_path.read_text(encoding="utf-8").splitlines()
        if (
            command.get("adapter") != "hybench"
            or command.get("mode") != "xp"
            or command.get("dry_run") is not False
            or "runxp" not in (command.get("argv") or [])
            or len(client_lines) < 1
        ):
            raise RuntimeError(f"{worker_id} lacks interrupted native-XP evidence")
        interruption_evidence[worker_id] = {
            "command_path": str(command_path),
            "command_sha256": sha(command_path),
            "client_processes_path": str(clients_path),
            "client_processes_sha256": sha(clients_path),
            "completed_adapter_result_absent": not (
                interrupted / worker_id / "adapter_result.json"
            ).exists(),
        }

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    quarantine = Path(str(freeze["storage"]["runtime_root"])) / "quarantine" / args.run_id
    frozen = {row["worker_id"]: row for row in freeze["workers"]}
    prior = {row["worker_id"]: row for row in prepared["workers"]}

    reset_rows: list[dict] = []
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-v14-restore") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze,
                frozen[worker_id],
                prior[worker_id],
                quarantine,
                output,
                False,
                "invalid_r0",
            ): worker_id
            for worker_id in XP_IDS
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                reset_rows.append(future.result())
                print(json.dumps({"worker_id": worker_id, "restored": True}), flush=True)
            except BaseException as error:
                failures.append({
                    "worker_id": worker_id,
                    "stage": "reset",
                    "error": f"{type(error).__name__}: {error}",
                })
                print(json.dumps(failures[-1]), flush=True)

    provisioned: list[dict] = []
    if not failures:
        account_root = output / "accounts"
        account_root.mkdir()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="xp-v14-users") as pool:
            futures = {
                pool.submit(users._run_worker, row, account_root, False, True): row
                for row in reset_rows
            }
            for future in as_completed(futures):
                row = futures[future]
                try:
                    provisioned.append({**row, **future.result()})
                    print(json.dumps({
                        "worker_id": row["worker_id"], "account_ready": True,
                    }), flush=True)
                except BaseException as error:
                    failures.append({
                        "worker_id": row["worker_id"],
                        "stage": "account",
                        "error": f"{type(error).__name__}: {error}",
                    })
                    print(json.dumps(failures[-1]), flush=True)

    provisioned.sort(key=lambda row: row["worker_id"])
    passed = len(provisioned) == len(XP_IDS) and not failures
    document = {
        "schema_version": "mysql_four_scene_xp_restore_after_interrupted_r0_v14",
        "status": "PREPARED" if passed else "FAILED_CLOSED",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE),
        "predecessor_prepared_path": str(PREPARED),
        "predecessor_prepared_sha256": sha(PREPARED),
        "interrupted_r0_path": str(interrupted),
        "interrupted_r0_binding_absent": True,
        "interrupted_resource_samples_sha256": sha(interrupted / "resource_samples.jsonl"),
        "interrupted_xp_evidence": interruption_evidence,
        "reset_source_path": str(Path(__file__).resolve()),
        "reset_source_sha256": sha(Path(__file__).resolve()),
        "quarantine_root": str(quarantine),
        "workers": provisioned,
        "failures": failures,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
    }
    binding = output / "prepared_binding.json"
    binding.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    binding.with_name(binding.name + ".sha256").write_text(
        f"{sha(binding)}  {binding.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": document["status"],
        "binding": str(binding),
        "ready": len(provisioned),
        "failed": len(failures),
    }), flush=True)
    return 0 if passed else 4


if __name__ == "__main__":
    raise SystemExit(main())
