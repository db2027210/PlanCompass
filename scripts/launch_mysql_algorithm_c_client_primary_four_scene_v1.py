#!/usr/bin/env python3
"""Launch all four v91 Algorithm-C scenes concurrently with two workers each."""

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
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v2.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v91/"
    "global_monotonic_incumbent_contract.json"
)
CHECKPOINT = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/"
    "checkpoint/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/"
    "replay_quarantine/replay_quarantine.json"
)
SCENES = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
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
    parser.add_argument("--fresh-r0", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.campaign_id) is None:
        parser.error("unsafe campaign id")
    authority = args.authority_root.expanduser().resolve()
    fresh_r0 = args.fresh_r0.expanduser().resolve()
    for path in (
        CONTRACT, CHECKPOINT, QUARANTINE, fresh_r0,
        authority / "transition_preflight.json",
        authority / "prepared_binding.json",
        authority / "benchmark_accounts_binding.json",
    ):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular launch artifact required: {path}")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if (
        contract.get("release_version") != "v91-client-primary"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("xp_outer_primary_metric")
        != "native_hybench_xp_client_xap_qps"
        or contract.get("full_parallel_launch", {}).get(
            "all_four_scenes_started_together"
        ) is not True
    ):
        raise RuntimeError("Algorithm C v91 launch contract differs")
    campaign = HISTORY / args.campaign_id
    if campaign.exists() or campaign.is_symlink():
        raise RuntimeError(f"fresh C campaign required: {campaign}")
    outputs = {
        scene: HISTORY / f"{args.campaign_id}_{suffix}"
        for scene, suffix in SCENES.items()
    }
    if any(path.exists() or path.is_symlink() for path in outputs.values()):
        raise RuntimeError("fresh per-scene C outputs required")
    barrier = Path("/tmp/mysql_async_outer_start") / args.campaign_id / "algorithm_c"
    if barrier.exists() or barrier.is_symlink():
        raise RuntimeError(f"fresh C start barrier required: {barrier}")
    barrier.mkdir(parents=True, mode=0o700)
    campaign.mkdir(parents=True, mode=0o700)
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
    processes: dict[str, subprocess.Popen[str]] = {}
    logs: dict[str, Any] = {}
    launches: dict[str, Any] = {}
    started = time.monotonic()
    try:
        for scene, suffix in SCENES.items():
            run_id = f"{args.campaign_id}_{suffix}"
            log_path = campaign / f"{suffix}.controller.log"
            log = log_path.open("w", encoding="utf-8")
            process = subprocess.Popen(
                [
                    sys.executable, str(CONTROLLER), "--run-id", run_id,
                    "--checkpoint", str(CHECKPOINT),
                    "--runtime-preflight", str(authority / "transition_preflight.json"),
                    "--prepared-binding", str(authority / "prepared_binding.json"),
                    "--benchmark-accounts", str(authority / "benchmark_accounts_binding.json"),
                    "--fresh-r0", str(fresh_r0),
                    "--replay-quarantine", str(QUARANTINE),
                    "--fresh-search", "--scene", scene,
                    "--scene-start-barrier", str(barrier),
                ],
                cwd=ROOT, env=environment, stdout=log,
                stderr=subprocess.STDOUT, text=True, start_new_session=True,
            )
            processes[scene] = process
            logs[scene] = log
            launches[scene] = {
                "scene": scene, "run_id": run_id, "pid": process.pid,
                "workers": 2, "started_at": now(),
                "started_offset_seconds": time.monotonic() - started,
                "log": str(log_path),
            }
        manifest = {
            "schema_version": "mysql_algorithm_c_client_primary_four_scene_launch_v1",
            "status": "RUNNING", "created_at": now(),
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
            "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
            "checkpoint": str(CHECKPOINT), "checkpoint_sha256": sha(CHECKPOINT),
            "replay_quarantine": str(QUARANTINE),
            "replay_quarantine_sha256": sha(QUARANTINE),
            "authority_root": str(authority),
            "fresh_r0": str(fresh_r0), "fresh_r0_sha256": sha(fresh_r0),
            "all_four_scenes_started_together": True,
            "maximum_start_skew_seconds": max(
                row["started_offset_seconds"] for row in launches.values()
            ),
            "workers_per_scene": 2, "maximum_concurrent_mysql_workers": 8,
            "four_scene_wave_barrier": False, "sibling_scene_wait": False,
            "lease_mode": "none",
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
