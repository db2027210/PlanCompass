#!/usr/bin/env python3
"""Start one Algorithm C scene immediately after the same B scene terminates."""

from __future__ import annotations

import argparse
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
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE_ROOT = ROOT / "config/formal/frozen_mysql_async_outer_20260819_v36"
HANDOFF_CONTRACT = FREEZE_ROOT / "serial_async_b_then_c_contract.json"
COORDINATION_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260819_v64"
    / "coordination_contract.json"
)
R0_COORDINATION_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v35"
    / "coordination_contract.json"
)
R0_SCRIPT = ROOT / "scripts/run_mysql_four_scene_r0_pg_outer_v35.py"
C_LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_async_v48.py"
CHECKPOINT = (
    HISTORY / "algorithm_c_v79_outer17_checkpoint_v20_20260817T1425p0800"
    / "prepared_binding.json"
)
REPLAY_QUARANTINE = (
    HISTORY / "algorithm_c_v79_replay_quarantine_v15_outer17_20260817T1427p0800"
    / "replay_quarantine.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def validate_contract() -> None:
    contract = load(HANDOFF_CONTRACT)
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("per_scene_b_to_c_handoff") is not True
        or contract.get("other_b_scene_completion_required") is not False
        or contract.get("recalibration_required") is not False
    ):
        raise RuntimeError("per-scene B-to-C handoff contract differs")
    for name, binding in (contract.get("source_bindings") or {}).items():
        path = ROOT / str(binding["path"])
        if not path.is_file() or path.is_symlink() or sha(path) != binding["sha256"]:
            raise RuntimeError(f"per-scene handoff source binding differs: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", required=True, choices=(
        "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
    ))
    parser.add_argument("--b-run-state", type=Path, required=True)
    parser.add_argument("--handoff-run-id", required=True)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--maximum-wait-seconds", type=float, default=14 * 86400)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,105}", args.handoff_run_id) is None:
        parser.error("unsafe handoff run id")
    if args.poll_seconds < 5 or args.maximum_wait_seconds <= 0:
        parser.error("invalid wait bounds")
    validate_contract()

    handoff_root = HISTORY / args.handoff_run_id
    handoff_root.mkdir(parents=True, mode=0o700, exist_ok=False)
    state_path = handoff_root / "handoff_state.json"
    atomic(state_path, {
        "status": "WAITING_FOR_OWN_B_SCENE",
        "scene": args.scene,
        "other_b_scene_completion_required": False,
        "b_run_state": str(args.b_run_state),
        "started_at": now(),
    })
    deadline = time.monotonic() + args.maximum_wait_seconds
    while time.monotonic() < deadline:
        b_state = load(args.b_run_state)
        if b_state.get("async_scene") != args.scene or b_state.get("algorithm") != "B":
            raise RuntimeError("Algorithm B scene identity differs")
        status = str(b_state.get("status"))
        if status in {"COMPLETE", "NON_CONVERGED"}:
            break
        if status not in {"RUNNING", "STARTING"}:
            raise RuntimeError(f"Algorithm B scene failed closed: {status}")
        time.sleep(args.poll_seconds)
    else:
        raise RuntimeError("Algorithm B scene did not terminate within bound")

    runtime_preflight = Path(str(b_state["runtime_preflight_path"])).resolve()
    prepared = Path(str(b_state["prepared_binding_path"])).resolve()
    accounts = Path(str(b_state["benchmark_accounts_path"])).resolve()
    preflight_doc = load(runtime_preflight)
    prepared_doc = load(prepared)
    accounts_doc = load(accounts)
    if (
        preflight_doc.get("status") != "PASS"
        or prepared_doc.get("status") != "PREPARED"
        or prepared_doc.get("preflight_sha256") != sha(runtime_preflight)
        or accounts_doc.get("status") != "PASS"
        or accounts_doc.get("prepared_binding_sha256") != sha(prepared)
    ):
        raise RuntimeError("completed B live authority lineage differs")

    atomic(state_path, {
        "status": "OWN_B_COMPLETE_STARTING_SCOPED_R0",
        "scene": args.scene,
        "other_b_scene_completion_required": False,
        "b_terminal_status": status,
        "b_run_state": str(args.b_run_state),
        "runtime_preflight": str(runtime_preflight),
        "prepared_binding": str(prepared),
        "benchmark_accounts": str(accounts),
        "updated_at": now(),
    })

    from scripts import mysql_cross_project_wave_coordination_v1 as coordination
    coordination.CONTRACT_PATH = R0_COORDINATION_CONTRACT
    lease = coordination.acquire_shared_blocking(timeout_seconds=1800.0)
    try:
        r0_run_id = f"{args.handoff_run_id}_r0"
        env = dict(os.environ)
        env.update({
            "MYSQL_V18_PASS_PREFLIGHT": str(runtime_preflight),
            "MYSQL_V18_FORMAL_PREPARED": str(prepared),
            "MYSQL_V18_FORMAL_ACCOUNTS": str(accounts),
            "MYSQL_V18_COORDINATION_LEASE_HOLDER_PID": str(os.getpid()),
            "MYSQL_V18_COORDINATION_CONTRACT": str(R0_COORDINATION_CONTRACT),
        })
        completed = subprocess.run(
            [str(PYTHON), str(R0_SCRIPT), "--run-id", r0_run_id,
             "--scene", args.scene],
            cwd=ROOT, env=env, text=True, check=False,
        )
    finally:
        coordination.release(lease)
    r0 = HISTORY / r0_run_id / "r0_binding.json"
    if completed.returncode != 0 or load(r0).get("status") != "PASS":
        raise RuntimeError(f"{args.scene} scoped R0 failed closed")

    atomic(state_path, {
        "status": "SCOPED_R0_COMPLETE_STARTING_C",
        "scene": args.scene,
        "other_b_scene_completion_required": False,
        "r0": str(r0),
        "updated_at": now(),
    })
    c_run_id = f"{args.handoff_run_id}_algorithm_c"
    launched = subprocess.run([
        str(PYTHON), str(C_LAUNCHER), "--run-id", c_run_id,
        "--scene", args.scene,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(runtime_preflight),
        "--prepared-binding", str(prepared),
        "--benchmark-accounts", str(accounts),
        "--fresh-r0", str(r0),
        "--replay-quarantine", str(REPLAY_QUARANTINE),
        "--fresh-search",
    ], cwd=ROOT, text=True, capture_output=True, check=False, timeout=1900)
    if launched.returncode != 0:
        raise RuntimeError(f"{args.scene} Algorithm C launch failed: {launched.stderr[-2000:]}")
    atomic(state_path, {
        "status": "ALGORITHM_C_STARTED",
        "scene": args.scene,
        "other_b_scene_completion_required": False,
        "r0": str(r0),
        "algorithm_c_run_id": c_run_id,
        "launcher_output": launched.stdout.strip(),
        "completed_at": now(),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
