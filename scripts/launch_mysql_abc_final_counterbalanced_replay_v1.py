#!/usr/bin/env python3
"""Launch four independent final A/B/C counterbalanced replay scenes."""

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
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
RUNNER = ROOT / "scripts/run_mysql_abc_final_counterbalanced_scene_v1.py"
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


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
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
    parser.add_argument("--contract", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.campaign_id) is None:
        parser.error("unsafe campaign id")
    contract_path = args.contract.resolve()
    contract = load(contract_path)
    if (
        contract.get("schema_version")
        != "mysql_abc_final_counterbalanced_replay_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("all_four_scenes_parallel") is not True
        or contract.get("workers_per_scene") != 2
    ):
        raise RuntimeError("final counterbalanced launch contract differs")
    campaign = HISTORY / args.campaign_id
    if campaign.exists() or campaign.is_symlink():
        raise RuntimeError(f"fresh replay campaign required: {campaign}")
    outputs = {
        scene: HISTORY / f"{args.campaign_id}_{suffix}"
        for scene, suffix in SCENES.items()
    }
    if any(path.exists() or path.is_symlink() for path in outputs.values()):
        raise RuntimeError("fresh replay scene roots required")
    campaign.mkdir(parents=True, mode=0o700)
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
                    sys.executable, str(RUNNER),
                    "--run-id", run_id,
                    "--scene", scene,
                    "--contract", str(contract_path),
                    "--output-dir", str(outputs[scene]),
                ],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                text=True, start_new_session=True,
            )
            processes[scene] = process
            logs[scene] = log
            launches[scene] = {
                "scene": scene,
                "run_id": run_id,
                "pid": process.pid,
                "workers": 2,
                "started_at": now(),
                "started_offset_seconds": time.monotonic() - started,
                "log": str(log_path),
            }
        manifest: dict[str, Any] = {
            "schema_version": "mysql_abc_final_counterbalanced_launch_v1",
            "status": "RUNNING",
            "created_at": now(),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "contract": str(contract_path),
            "contract_sha256": sha(contract_path),
            "all_four_scenes_parallel": True,
            "workers_per_scene": 2,
            "maximum_concurrent_mysql_workers": 8,
            "four_scene_barrier": False,
            "sibling_scene_wait": False,
            "lease_mode": "none",
            "external_process_control_or_stop_performed": False,
            "postgresql_process_control_or_stop_performed": False,
            "maximum_start_skew_seconds": max(
                row["started_offset_seconds"] for row in launches.values()
            ),
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
                        "exit_code": code,
                        "completed_at": now(),
                    })
                    remaining.remove(scene)
            manifest.update({
                "launches": launches,
                "running_scenes": sorted(remaining),
            })
            atomic(manifest_path, manifest)
            if remaining:
                time.sleep(10)
        passed = all(row.get("exit_code") == 0 for row in launches.values())
        manifest.update({
            "status": "COMPLETE" if passed else "FAILED_CLOSED",
            "completed_at": now(),
            "launches": launches,
        })
        atomic(manifest_path, manifest)
        return 0 if passed else 4
    finally:
        for log in logs.values():
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
