#!/usr/bin/env python3
"""Launch all four client-primary B scenes concurrently, two workers each."""

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
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_four_scene_client_primary_v1.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v67/"
    "algorithm_b_top4_contract.json"
)
SCENES = {
    "job_memory": ("job", "v415_to_b_top4_two_worker_bootstrap_v47_20260826_job"),
    "tpch_sf4_memory": ("tpch", "v415_to_b_top4_two_worker_bootstrap_v47_20260826_tpch"),
    "hybench_xp_cpu": ("xp_cpu", "v415_to_b_top4_two_worker_bootstrap_v47_20260826_xp_cpu"),
    "hybench_xp_io": ("xp_io", "v415_to_b_top4_two_worker_bootstrap_v47_20260826_xp_io"),
}


def now() -> str:
    return datetime.now().astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n", encoding="ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--authority-root", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.campaign_id) is None:
        parser.error("unsafe campaign id")
    authority = args.authority_root.resolve()
    for name in ("transition_preflight.json", "prepared_binding.json", "benchmark_accounts_binding.json"):
        if not (authority / name).is_file():
            raise RuntimeError(f"authority artifact absent: {name}")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("xp_outer_primary_metric") != "native_hybench_xp_client_xap_qps"
        or (contract.get("full_parallel_launch") or {}).get("all_four_scenes_started_together") is not True
    ):
        raise RuntimeError("client-primary B contract differs")
    campaign = HISTORY / args.campaign_id
    if campaign.exists() or campaign.is_symlink():
        raise RuntimeError(f"fresh campaign root required: {campaign}")
    campaign.mkdir(parents=True, mode=0o700)
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
    processes: dict[str, subprocess.Popen[str]] = {}
    logs: dict[str, Any] = {}
    launches: dict[str, Any] = {}
    try:
        launch_started = time.monotonic()
        for scene, (suffix, bootstrap_name) in SCENES.items():
            run_id = f"{args.campaign_id}_{suffix}"
            output = HISTORY / run_id
            if output.exists():
                raise RuntimeError(f"fresh scene output required: {output}")
            bootstrap = HISTORY / bootstrap_name
            log_path = campaign / f"{suffix}.controller.log"
            log = log_path.open("w", encoding="utf-8")
            process = subprocess.Popen(
                [
                    sys.executable, str(CONTROLLER),
                    "--run-id", run_id,
                    "--resume-from", str(bootstrap),
                    "--scene", scene,
                ],
                cwd=ROOT, env=environment,
                stdout=log, stderr=subprocess.STDOUT,
                text=True, start_new_session=True,
            )
            processes[scene] = process
            logs[scene] = log
            launches[scene] = {
                "scene": scene, "run_id": run_id, "pid": process.pid,
                "started_at": now(), "started_offset_seconds": time.monotonic() - launch_started,
                "bootstrap": str(bootstrap), "bootstrap_sha256": sha(bootstrap / "bootstrap_manifest.json"),
                "log": str(log_path), "workers": 2,
            }
        manifest = {
            "schema_version": "mysql_algorithm_b_client_primary_four_scene_launch_v1",
            "status": "RUNNING", "created_at": now(),
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
            "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
            "authority_root": str(authority),
            "all_four_scenes_started_together": True,
            "maximum_start_skew_seconds": max(row["started_offset_seconds"] for row in launches.values()),
            "workers_per_scene": 2, "maximum_concurrent_mysql_workers": 8,
            "four_scene_barrier": False, "sibling_scene_wait": False,
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
                if code is None:
                    continue
                launches[scene]["exit_code"] = code
                launches[scene]["completed_at"] = now()
                remaining.remove(scene)
            manifest["launches"] = launches
            manifest["running_scenes"] = sorted(remaining)
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
