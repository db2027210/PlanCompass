#!/usr/bin/env python3
"""Restart only the two pre-measurement XP failures from campaign v1178."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_four_scene_client_primary_v2.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v68/"
    "algorithm_b_top4_contract.json"
)
SCENES = {
    "hybench_xp_cpu": (
        "xp_cpu", "v415_to_b_top4_two_worker_bootstrap_v47_20260826_xp_cpu"
    ),
    "hybench_xp_io": (
        "xp_io", "v415_to_b_top4_two_worker_bootstrap_v47_20260826_xp_io"
    ),
}


def now() -> str:
    return datetime.now().astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--original-campaign-root", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.campaign_id) is None:
        parser.error("unsafe campaign id")
    authority = args.authority_root.expanduser().resolve()
    original = args.original_campaign_root.expanduser().resolve()
    for name in (
        "transition_preflight.json", "prepared_binding.json",
        "benchmark_accounts_binding.json",
    ):
        if not (authority / name).is_file():
            raise RuntimeError(f"authority artifact absent: {name}")
    original_manifest = original / "launch_manifest.json"
    if not original_manifest.is_file():
        raise RuntimeError("original four-scene launch manifest absent")
    original_document = json.loads(original_manifest.read_text(encoding="utf-8"))
    if original_document.get("all_four_scenes_started_together") is not True:
        raise RuntimeError("original four-scene launch evidence differs")
    for scene in SCENES:
        row = (original_document.get("launches") or {}).get(scene) or {}
        state = HISTORY / str(row.get("run_id") or "") / "run_state.json"
        document = json.loads(state.read_text(encoding="utf-8"))
        if (
            document.get("status") != "FAILED_CLOSED"
            or document.get("accepted_rounds", {}).get(scene) != 0
            or "Docker/reset resource differs: {}" not in str(document.get("error"))
        ):
            raise RuntimeError(f"{scene} is not the authorized reset-only failure")

    campaign = HISTORY / args.campaign_id
    if campaign.exists() or campaign.is_symlink():
        raise RuntimeError(f"fresh recovery campaign required: {campaign}")
    campaign.mkdir(parents=True, mode=0o700)
    original_snapshot = campaign / "original_four_scene_launch_snapshot.json"
    atomic(original_snapshot, original_document)
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
    processes: dict[str, subprocess.Popen[str]] = {}
    logs: dict[str, Any] = {}
    launches: dict[str, Any] = {}
    launch_started = time.monotonic()
    try:
        for scene, (suffix, bootstrap_name) in SCENES.items():
            run_id = f"{args.campaign_id}_{suffix}"
            log_path = campaign / f"{suffix}.controller.log"
            log = log_path.open("w", encoding="utf-8")
            process = subprocess.Popen(
                [sys.executable, str(CONTROLLER), "--run-id", run_id,
                 "--resume-from", str(HISTORY / bootstrap_name), "--scene", scene],
                cwd=ROOT, env=environment, stdout=log,
                stderr=subprocess.STDOUT, text=True, start_new_session=True,
            )
            processes[scene] = process
            logs[scene] = log
            launches[scene] = {
                "scene": scene, "run_id": run_id, "pid": process.pid,
                "started_at": now(),
                "started_offset_seconds": time.monotonic() - launch_started,
                "workers": 2, "log": str(log_path),
            }
        manifest = {
            "schema_version": "mysql_algorithm_b_client_primary_xp_technical_recovery_v1",
            "status": "RUNNING", "created_at": now(),
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
            "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
            "authority_root": str(authority),
            "original_four_scene_launch_manifest": str(original_snapshot),
            "original_four_scene_launch_manifest_sha256": sha(original_snapshot),
            "technical_recovery_only": True,
            "accepted_or_rejected_outer_state_reused": False,
            "job_tpch_sibling_controllers_restarted": False,
            "maximum_start_skew_seconds": max(
                row["started_offset_seconds"] for row in launches.values()
            ),
            "external_process_control_or_stop_performed": False,
            "postgresql_process_control_or_stop_performed": False,
            "launches": launches,
        }
        manifest_path = campaign / "launch_manifest.json"
        atomic(manifest_path, manifest)
        remaining = set(processes)
        while remaining:
            for scene in tuple(remaining):
                code = processes[scene].poll()
                if code is not None:
                    launches[scene].update({
                        "exit_code": code, "completed_at": now(),
                    })
                    remaining.remove(scene)
            manifest.update({"launches": launches, "running_scenes": sorted(remaining)})
            atomic(manifest_path, manifest)
            if remaining:
                time.sleep(10)
        passed = all(row.get("exit_code") == 0 for row in launches.values())
        manifest.update({
            "status": "COMPLETE" if passed else "FAILED_CLOSED",
            "completed_at": now(), "launches": launches,
        })
        atomic(manifest_path, manifest)
        return 0 if passed else 4
    finally:
        for log in logs.values():
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
