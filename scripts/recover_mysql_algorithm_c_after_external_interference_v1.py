#!/usr/bin/env python3
"""Rebuild all XP planes after an acknowledged external-interference reset failure."""

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--predecessor-binding", type=Path, required=True)
    parser.add_argument("--failed-diagnostic-state", type=Path, required=True)
    parser.add_argument("--external-container", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    if re.fullmatch(r"[A-Za-z0-9_.-]{3,128}", args.external_container) is None:
        parser.error("unsafe external container name")

    predecessor_path = args.predecessor_binding.expanduser().resolve()
    failed_path = args.failed_diagnostic_state.expanduser().resolve()
    predecessor = load(predecessor_path)
    failed = load(failed_path)
    freeze = load(algorithm.FREEZE)
    prior = {
        str(row["worker_id"]): dict(row)
        for row in (predecessor.get("workers") or [])
    }
    frozen = {
        str(row["worker_id"]): dict(row)
        for row in freeze["workers"] if row.get("worker_id") in XP_IDS
    }
    external_running = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"name=^/{args.external_container}$"],
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()
    if (
        predecessor.get("status") not in {"PASS", "PREPARED"}
        or predecessor.get("freeze_sha256") != algorithm.sha(algorithm.FREEZE)
        or set(prior) != XP_IDS
        or set(frozen) != XP_IDS
        or any(row.get("ready") is not True for row in prior.values())
        or failed.get("status") != "FAILED"
        or failed.get("diagnostic_only") is not True
        or failed.get("formal_eligible") is not False
        or failed.get("accepted_rounds_unchanged") is not True
        or "docker" not in str(failed.get("error") or "")
        or "timed out" not in str(failed.get("error") or "")
        or external_running
    ):
        raise RuntimeError("external-interference XP recovery authority differs")

    # The failed attempts may have stopped/restarted containers, but they must
    # still be the exact predecessor identities.  No unknown container is
    # eligible for the resume-stalled discard path.
    for worker_id in sorted(XP_IDS):
        inspected = reset._docker_json("inspect", frozen[worker_id]["container"])
        if (
            inspected.get("Id") != prior[worker_id].get("container_id")
            or (inspected.get("State") or {}).get("Running") is True
            or (inspected.get("Config") or {}).get("Labels", {}).get("ra.owner")
            != "mysql_four_scene_v5"
        ):
            raise RuntimeError(f"{worker_id} stopped predecessor identity differs")

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh recovery output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    stage = output / "xp_resets/external_interference_discard_v1"
    stage.mkdir(parents=True, mode=0o700)
    quarantine = (
        Path(str(freeze["storage"]["runtime_root"]))
        / "quarantine" / output.name / stage.name
    )
    started = time.monotonic()
    reset_rows: dict[str, dict[str, Any]] = {}
    finished: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="external-recovery") as pool:
        futures = {
            pool.submit(
                reset._prepare_fresh_xp,
                freeze, frozen[worker_id], prior[worker_id], quarantine, stage,
                True, "formal_point", True, False, True, False,
            ): worker_id
            for worker_id in sorted(XP_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            reset_rows[worker_id] = future.result()
            finished[worker_id] = time.monotonic() - started

    account_root = stage / "accounts"
    account_root.mkdir()
    workers: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="external-users") as pool:
        futures = {
            pool.submit(users._run_worker, reset_rows[worker_id], account_root, True, False): worker_id
            for worker_id in sorted(XP_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            workers[worker_id] = {
                **frozen[worker_id], **reset_rows[worker_id], **future.result()
            }
    if (
        set(workers) != XP_IDS
        or any(row.get("ready") is not True for row in workers.values())
        or any(algorithm.xp_active_client_session_count(row) != 0 for row in workers.values())
    ):
        raise RuntimeError("recovered XP result differs")

    binding = output / "prepared_binding.json"
    atomic(binding, {
        "schema_version": "mysql_algorithm_c_external_interference_recovery_v1",
        "status": "PREPARED",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(algorithm.FREEZE),
        "freeze_sha256": algorithm.sha(algorithm.FREEZE),
        "predecessor_binding_path": str(predecessor_path),
        "predecessor_binding_sha256": sha(predecessor_path),
        "failed_diagnostic_state_path": str(failed_path),
        "failed_diagnostic_state_sha256": sha(failed_path),
        "external_container": args.external_container,
        "external_container_running_at_recovery": False,
        "workers": [workers[worker_id] for worker_id in sorted(XP_IDS)],
        "worker_reset_finished_seconds": finished,
        "elapsed_seconds": time.monotonic() - started,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
        "accepted_rounds": {scene: 0 for scene in freeze["active_scenes"]},
        "accepted_rounds_mutation": False,
    })
    cleanup = reset.purge_completed_discard_quarantine(
        quarantine_root=quarantine,
        reset_workers=workers,
        binding_path=binding,
        cleanup_path=output / "quarantine_cleanup.json",
    )
    print(json.dumps({
        "status": "PREPARED",
        "binding": str(binding),
        "elapsed_seconds": time.monotonic() - started,
        "quarantine_deleted_bytes": cleanup["deleted_bytes"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
