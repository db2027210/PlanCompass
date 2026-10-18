#!/usr/bin/env python3
"""Launch all four v157 scenes from their completed outer-1 boundaries."""

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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_four_scene_client_primary_v67 as controller


HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v67.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_full_resume_20260923_v157/"
    "algorithm_c_full_resume_contract.json"
)
FRESH_R0 = HISTORY / (
    "c_v157_full_resume_fresh_r0_v36_20260923/fresh_r0_binding.json"
)
SCENES = {
    "job_memory": ("job", "0-7,256-263"),
    "tpch_sf4_memory": ("tpch", "8-15,264-271"),
    "hybench_xp_cpu": ("xp_cpu", "16-23,272-279"),
    "hybench_xp_io": ("xp_io", "24-31,280-287"),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for item in value.split(","):
        left, separator, right = item.partition("-")
        result.update(range(int(left), int(right) + 1) if separator else [int(left)])
    return result


def scene_paths(scene: str) -> tuple[Path, Path]:
    suffix = SCENES[scene][0]
    checkpoint = HISTORY / (
        f"c_v157_full_resume_checkpoint_20260923_{suffix}/prepared_binding.json"
    )
    quarantine = HISTORY / (
        f"c_v157_full_resume_quarantine_20260923_{suffix}/replay_quarantine.json"
    )
    return checkpoint, quarantine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--authority-root", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.run_prefix) is None:
        parser.error("unsafe run prefix")
    controller.validate_contract()
    authority = args.authority_root.expanduser().resolve()
    preflight = authority / "transition_preflight.json"
    prepared = authority / "prepared_binding.json"
    accounts = authority / "benchmark_accounts_binding.json"
    preflight_value = load(preflight)
    prepared_value = load(prepared)
    accounts_value = load(accounts)
    fresh_r0 = load(FRESH_R0)
    freeze_sha = sha(controller.predecessor.FREEZE)
    if (
        preflight_value.get("status") != "PASS"
        or preflight_value.get("formal_launch_authorized") is not True
        or float(preflight_value.get("observed_seconds") or 0) < 60.0
        or (preflight_value.get("rolling_dynamic_noninterference_gate") or {}).get("status") != "PASS"
        or preflight_value.get("freeze_sha256") != freeze_sha
        or prepared_value.get("status") != "PREPARED"
        or prepared_value.get("freeze_sha256") != freeze_sha
        or prepared_value.get("preflight_sha256") != sha(preflight)
        or accounts_value.get("status") != "PASS"
        or accounts_value.get("freeze_sha256") != freeze_sha
        or accounts_value.get("prepared_binding_sha256") != sha(prepared)
        or fresh_r0.get("status") != "PASS"
        or fresh_r0.get("formal_outer_launch_authorized") is not True
        or fresh_r0.get("preflight_sha256") != sha(preflight)
        or fresh_r0.get("prepared_binding_sha256") != sha(prepared)
        or fresh_r0.get("benchmark_accounts_sha256") != sha(accounts)
    ):
        raise RuntimeError("v157 current authority or fresh-R0 lineage differs")

    checkpoint_rows = {}
    for scene in SCENES:
        checkpoint, quarantine = scene_paths(scene)
        checkpoint_value = controller.runtime.scene_checkpoint.verify_or_standard(checkpoint)
        quarantine_value = load(quarantine)
        if (
            checkpoint_value.get("freeze_sha256") != freeze_sha
            or int(checkpoint_value.get("completed_outer_attempts") or 0) != 1
            or (checkpoint_value.get("independent_scene_boundary") or {}).get("scene") != scene
            or quarantine_value.get("status") != "PASS"
            or quarantine_value.get("freeze_sha256") != freeze_sha
            or int(quarantine_value.get("resume_checkpoint_outer_attempt") or 0) != 1
            or (quarantine_value.get("evidence") or {}).get("checkpoint", {}).get("sha256") != sha(checkpoint)
        ):
            raise RuntimeError(f"v157 resume artifacts differ: {scene}")
        checkpoint_rows[scene] = {
            "checkpoint": checkpoint, "quarantine": quarantine,
        }

    campaign = HISTORY / args.run_prefix
    barrier = Path("/tmp/mysql_async_outer_start") / args.run_prefix / "algorithm_c"
    run_ids = {
        scene: f"{args.run_prefix}_{suffix}"
        for scene, (suffix, _affinity) in SCENES.items()
    }
    if campaign.exists() or barrier.exists() or any(
        (HISTORY / run_id).exists() for run_id in run_ids.values()
    ):
        raise RuntimeError("fresh v157 campaign, output, and start barrier required")
    campaign.mkdir(parents=True, mode=0o700)
    barrier.mkdir(parents=True, mode=0o700)
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(authority)
    launches = {}
    started = time.monotonic()
    for scene, (suffix, affinity) in SCENES.items():
        log_path = campaign / f"{suffix}.controller.log"
        log = log_path.open("w")
        row = checkpoint_rows[scene]
        command = [
            sys.executable, str(CONTROLLER),
            "--run-id", run_ids[scene],
            "--checkpoint", str(row["checkpoint"]),
            "--runtime-preflight", str(preflight),
            "--prepared-binding", str(prepared),
            "--benchmark-accounts", str(accounts),
            "--fresh-r0", str(FRESH_R0),
            "--replay-quarantine", str(row["quarantine"]),
            "--scene", scene,
            "--scene-start-barrier", str(barrier),
        ]
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True,
            preexec_fn=lambda value=affinity: os.sched_setaffinity(0, cpus(value)),
        )
        log.close()
        launches[scene] = {
            "run_id": run_ids[scene], "pid": process.pid,
            "controller_cpuset": affinity,
            "started_offset_seconds": time.monotonic() - started,
            "log": str(log_path),
            "checkpoint": str(row["checkpoint"]),
            "checkpoint_sha256": sha(row["checkpoint"]),
            "quarantine": str(row["quarantine"]),
            "quarantine_sha256": sha(row["quarantine"]),
        }
    manifest = {
        "schema_version": "mysql_algorithm_c_v157_full_resume_launch_v1",
        "status": "RUNNING",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "resume_after_completed_outer": 1,
        "incomplete_outer_credit": 0,
        "paper_baseline_tolerance_percentage_points": 1.0,
        "paper_baseline_gate_status": "PASS",
        "full_search_runs_until_runtime_terminal_condition": True,
        "all_four_scenes_started_together": True,
        "four_scene_round_barrier": False,
        "sibling_scene_wait": False,
        "workers_per_scene": 2,
        "maximum_start_skew_seconds": max(
            row["started_offset_seconds"] for row in launches.values()
        ),
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "authority_root": str(authority),
        "current_preflight_sha256": sha(preflight),
        "fresh_r0": str(FRESH_R0), "fresh_r0_sha256": sha(FRESH_R0),
        "launches": launches,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(campaign / "launch_manifest.json", manifest)
    print(json.dumps({
        "status": "RUNNING", "campaign": str(campaign),
        "launches": launches,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
