#!/usr/bin/env python3
"""Start four independent v137 scenes together, with optional R0-only calibration."""

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


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v47.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260920_v137/"
    "algorithm_c_paper_baseline_contract.json"
)
CHECKPOINT = HISTORY / (
    "c_v137_paper_resource_v29_bootstrap_20260920/checkpoint/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "c_v137_paper_resource_v29_bootstrap_20260920/"
    "replay_quarantine/replay_quarantine.json"
)
SCENES = {
    "job_memory": ("job", "0-7,256-263"),
    "tpch_sf4_memory": ("tpch", "8-15,264-271"),
    "hybench_xp_cpu": ("xp_cpu", "16-23,272-279"),
    "hybench_xp_io": ("xp_io", "24-31,280-287"),
}


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for item in value.split(","):
        left, separator, right = item.partition("-")
        result.update(range(int(left), int(right) + 1) if separator else [int(left)])
    return result


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--fresh-r0", type=Path, required=True)
    parser.add_argument("--baseline-only", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.run_prefix):
        parser.error("unsafe run prefix")
    authority = args.authority_root.resolve()
    fresh_r0 = args.fresh_r0.resolve()
    preflight = authority / "transition_preflight.json"
    prepared = authority / "prepared_binding.json"
    accounts = authority / "benchmark_accounts_binding.json"
    for path in (CONTRACT, CHECKPOINT, QUARANTINE, fresh_r0,
                 preflight, prepared, accounts):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular launch artifact required: {path}")
    preflight_doc = json.loads(preflight.read_text())
    prepared_doc = json.loads(prepared.read_text())
    if (
        preflight_doc.get("status") != "PASS"
        or preflight_doc.get("formal_launch_authorized") is not True
        or float(preflight_doc.get("observed_seconds") or 0) < 60
        or (preflight_doc.get("rolling_dynamic_noninterference_gate") or {}).get("status") != "PASS"
    ):
        raise RuntimeError("current live non-interference authority is not PASS")
    freeze_path = Path(str(preflight_doc["freeze_path"]))
    if not freeze_path.is_file() or sha(freeze_path) != preflight_doc.get("freeze_sha256"):
        raise RuntimeError("frozen resource envelope differs")
    frozen_workers = {
        str(row["worker_id"]): row
        for row in json.loads(freeze_path.read_text())["workers"]
    }
    resource_fields = (
        "worker_id", "scene", "container", "container_id",
        "database_cpuset", "database_cpuset_mems",
        "client_cpuset", "client_cpuset_mems", "cpu_quota_cores",
        "memory_bytes", "memory_swappiness", "effective_swap_policy",
        "io_read_bps", "io_write_bps", "pids_max",
    )
    effective_resources = {
        str(row["worker_id"]): {
            **{
                key: row[key] if key in row else frozen_workers[str(row["worker_id"])][key]
                for key in resource_fields
            },
            "kernel_cgroup_readback": row["kernel_cgroup_resources"],
            "blkio_readback": row["blkio_authority"],
        }
        for row in prepared_doc["workers"]
    }
    if len(effective_resources) != 8:
        raise RuntimeError("eight current worker resource readbacks required")
    campaign = HISTORY / args.run_prefix
    barrier = Path("/tmp/mysql_async_outer_start") / args.run_prefix / "algorithm_c"
    if campaign.exists() or barrier.exists():
        raise RuntimeError("fresh campaign and barrier required")
    run_ids = {scene: f"{args.run_prefix}_{suffix}" for scene, (suffix, _) in SCENES.items()}
    if any((HISTORY / run_id).exists() for run_id in run_ids.values()):
        raise RuntimeError("fresh per-scene output required")
    campaign.mkdir(parents=True, mode=0o700)
    barrier.mkdir(parents=True, mode=0o700)
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
    processes = {}
    logs = {}
    started = time.monotonic()
    launches = {}
    for scene, (suffix, affinity) in SCENES.items():
        run_id = run_ids[scene]
        log_path = campaign / f"{suffix}.controller.log"
        log = log_path.open("w")
        command = [
            sys.executable, str(CONTROLLER), "--run-id", run_id,
            "--checkpoint", str(CHECKPOINT),
            "--runtime-preflight", str(preflight),
            "--prepared-binding", str(prepared),
            "--benchmark-accounts", str(accounts),
            "--fresh-r0", str(fresh_r0),
            "--replay-quarantine", str(QUARANTINE),
            "--fresh-search", "--scene", scene,
            "--scene-start-barrier", str(barrier),
        ]
        if args.baseline_only:
            command.append("--baseline-only")
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True,
            preexec_fn=lambda value=affinity: os.sched_setaffinity(0, cpus(value)),
        )
        processes[scene] = process
        logs[scene] = log
        launches[scene] = {
            "run_id": run_id, "pid": process.pid,
            "controller_cpuset": affinity,
            "started_offset_seconds": time.monotonic() - started,
            "log": str(log_path),
        }
    manifest = {
        "schema_version": "mysql_algorithm_c_v137_four_scene_launch_v1",
        "status": "RUNNING",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "baseline_only": args.baseline_only,
        "all_four_scenes_started_together": True,
        "four_scene_round_barrier": False,
        "sibling_scene_wait": False,
        "maximum_start_skew_seconds": max(x["started_offset_seconds"] for x in launches.values()),
        "workers_per_scene": 2,
        "effective_resources": effective_resources,
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "authority_root": str(authority),
        "current_preflight_sha256": sha(preflight),
        "fresh_r0": str(fresh_r0), "fresh_r0_sha256": sha(fresh_r0),
        "launches": launches,
        "postgresql_process_control_or_stop_performed": False,
    }
    manifest_path = campaign / "launch_manifest.json"
    atomic(manifest_path, manifest)
    if not args.baseline_only:
        return 0
    try:
        pending = set(processes)
        while pending:
            for scene in tuple(pending):
                code = processes[scene].poll()
                if code is None:
                    continue
                pending.remove(scene)
                gate_path = HISTORY / run_ids[scene] / "paper_baseline_resource_gate.json"
                gate = json.loads(gate_path.read_text()) if gate_path.is_file() else {}
                launches[scene].update({
                    "exit_code": code,
                    "paper_baseline_gate_status": gate.get("status"),
                    "paper_baseline_gate_path": str(gate_path) if gate else None,
                })
            manifest["launches"] = launches
            manifest["running_scenes"] = sorted(pending)
            atomic(manifest_path, manifest)
            if pending:
                time.sleep(10)
        passed = all(
            row.get("exit_code") == 0 and row.get("paper_baseline_gate_status") == "PASS"
            for row in launches.values()
        )
        manifest["status"] = "PASS" if passed else "FAILED_CLOSED"
        atomic(manifest_path, manifest)
        return 0 if passed else 4
    finally:
        for log in logs.values():
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
