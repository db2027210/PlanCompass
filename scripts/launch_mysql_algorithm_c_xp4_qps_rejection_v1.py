#!/usr/bin/env python3
"""Fresh-launch one strict-QPS XP Algorithm C scene on four workers."""

from __future__ import annotations

import argparse
import copy
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

from scripts import build_mysql_algorithm_c_xp4_transition_checkpoint_v1 as rebind
from scripts import build_mysql_algorithm_c_empty_quarantine_for_checkpoint_v1 as quarantine


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4"
    / "resource_successor.json"
)
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v20.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
MANAGEMENT_CPUSET = "240-241,496-497"
CONFIG = {
    "hybench_xp_cpu": {
        "short": "cpu",
        "boundary": HISTORY / "v39_to_c_per_scene_20260820_attempt076_xp_cpu_cached_preemption_checkpoint/prepared_binding.json",
    },
    "hybench_xp_io": {
        "short": "io",
        "boundary": HISTORY / "v165_to_c_top4_io_outer4_boundary_20260826/prepared_binding.json",
    },
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


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
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(CONFIG), required=True)
    parser.add_argument("--campaign-tag", required=True)
    parser.add_argument("--attempt-number", type=int, required=True)
    parser.add_argument("--live-authority-root", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"v[0-9]{3}", args.campaign_tag) is None:
        parser.error("campaign tag must match vNNN")
    if args.attempt_number < 1 or args.attempt_number > 999:
        parser.error("attempt number must be 1..999")
    scene = args.scene
    item = CONFIG[scene]
    short = str(item["short"])
    boundary_path = Path(item["boundary"])
    output = HISTORY / (
        f"{args.campaign_tag}_to_c_top4_qps_rejection_20260826_{short}"
    )
    run_id = (
        f"{args.campaign_tag}_to_c_top4_qps_rejection_20260826_"
        f"attempt{args.attempt_number:03d}_xp_{short}"
    )
    unit = f"mysql-algc-xp4-qps-{args.campaign_tag}-{short}.service"
    live_root = args.live_authority_root.resolve() / scene
    if not live_root.is_relative_to(HISTORY.resolve()):
        parser.error("live authority root must be under formal history")
    if output.exists() or output.is_symlink():
        result = output / "launch.json"
        if result.is_file():
            print(json.dumps(load(result), sort_keys=True))
            return 0
        raise RuntimeError(f"fresh {scene} launch output required")
    live = load(live_root / "live_authority.json")
    boundary = rebind.verifier.verify_checkpoint(boundary_path)
    if (
        live.get("status") != "PASS"
        or live.get("scene") != scene
        or live.get("physical_replay_parallelism") != 4
        or (boundary.get("independent_scene_boundary") or {}).get("scene") != scene
    ):
        raise RuntimeError(f"{scene} boundary or XP4 live authority differs")
    output.mkdir(parents=True, mode=0o700)
    source_freeze = Path(str(boundary["freeze_path"])).resolve()
    authority_path = output / "transition_authority.json"
    authority = {
        "schema_version": "mysql_algorithm_c_xp4_fresh_qps_transition_authority_v1",
        "status": "PASS",
        "created_at": now(),
        "scene": scene,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "round_boundary_preserved": True,
        "accepted_state_credit_increment": 0,
        "completed_candidate_replay_increment": 0,
        "recalibration_performed": False,
        "fresh_search_resets_optimizer_state": True,
        "source_freeze_path": str(source_freeze),
        "source_freeze_sha256": sha(source_freeze),
        "successor_freeze_path": str(FREEZE),
        "successor_freeze_sha256": sha(FREEZE),
        "source_boundary_checkpoint_path": str(boundary_path),
        "source_boundary_checkpoint_sha256": sha(boundary_path),
        "worker_ids": list(live["worker_ids"]),
        "candidate_batches_before": [[1, 2], [3, 4], [5]],
        "candidate_batches_after": [[1, 2, 3, 4], [5]],
        "semantic_top_k": 5,
        "native_xp_workload_unchanged": True,
        "lease_mode": "none",
    }
    atomic(authority_path, authority)
    rebound_root = output / "successor_checkpoint"
    rebound_root.mkdir(parents=True, mode=0o700)
    checkpoint_path = rebound_root / "prepared_binding.json"
    checkpoint = rebind.build(boundary_path, FREEZE, authority_path)
    rebind.base.alg.atomic(checkpoint_path, checkpoint)
    rebind.verifier.verify_checkpoint(checkpoint_path)
    quarantine_root = output / "replay_quarantine"
    quarantine_root.mkdir(parents=True, mode=0o700)
    empty = {name: [] for name in quarantine.SCENES}
    zeros = {name: 0 for name in quarantine.SCENES}
    quarantine_document = {
        "schema_version": "mysql_algorithm_c_replay_quarantine_v6",
        "status": "PASS",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_sha256": checkpoint["freeze_sha256"],
        "resume_checkpoint_outer_attempt": checkpoint["completed_outer_attempts"],
        "formal_state_mutation": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "quarantined_counts_by_scene": zeros,
        "soft_replayed_counts_by_scene": dict(zeros),
        "permanent_invalid_counts_by_scene": dict(zeros),
        "quarantined_param_keys_by_scene": empty,
        "soft_replayed_param_keys_by_scene": copy.deepcopy(empty),
        "permanent_invalid_param_keys_by_scene": copy.deepcopy(empty),
        "rows_by_scene": {name: [] for name in quarantine.SCENES},
        "cycle_reset_may_clear_soft_replayed": True,
        "cycle_reset_may_clear_permanent_invalid": False,
        "initialization": "xp_top4_strict_qps_fresh_search",
        "evidence": {
            "checkpoint": quarantine.bound(checkpoint_path),
            "resource_freeze": quarantine.bound(FREEZE),
            "pruning_contract": quarantine.bound(quarantine.PRUNING),
        },
    }
    quarantine_path = quarantine_root / "replay_quarantine.json"
    atomic(quarantine_path, quarantine_document)
    artifacts = live["artifacts"]
    run_root = HISTORY / run_id
    log_root = HISTORY / "formal_launch_logs"
    stdout = log_root / f"{run_id}.stdout.log"
    stderr = log_root / f"{run_id}.stderr.log"
    barrier = Path(f"/tmp/mysql_c_xp4_qps_start/{run_id}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Top4 {short.upper()} Algorithm C strict-QPS",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER),
        "--run-id", run_id,
        "--checkpoint", str(checkpoint_path),
        "--runtime-preflight", str(artifacts["preflight"]),
        "--prepared-binding", str(artifacts["prepared"]),
        "--benchmark-accounts", str(artifacts["accounts"]),
        "--fresh-r0", str(artifacts["r0"]),
        "--replay-quarantine", str(quarantine_path),
        "--fresh-search",
        "--scene", scene,
        "--scene-start-barrier", str(barrier),
    ]
    launched = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        check=False, timeout=30,
    )
    if launched.returncode != 0:
        raise RuntimeError(f"{scene} C launch failed: {launched.stderr}")
    state_path = run_root / "run_state.json"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        shown = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "MainPID"],
            capture_output=True, text=True, check=False, timeout=15,
        ).stdout
        if "ActiveState=failed" in shown or "ActiveState=inactive" in shown:
            raise RuntimeError(f"{scene} C exited during startup: {shown}; {stderr}")
        if state_path.is_file() and load(state_path).get("status") == "RUNNING":
            result = {
                "status": "LAUNCHED",
                "launched_at": now(),
                "scene": scene,
                "run_id": run_id,
                "unit": unit,
                "state": str(state_path),
                "checkpoint": str(checkpoint_path),
                "output": str(run_root),
                "stdout": str(stdout),
                "stderr": str(stderr),
            }
            atomic(output / "launch.json", result)
            print(json.dumps(result, sort_keys=True))
            return 0
        time.sleep(2)
    raise TimeoutError(f"{scene} C startup evidence timed out")


if __name__ == "__main__":
    raise SystemExit(main())
