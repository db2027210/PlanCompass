#!/usr/bin/env python3
"""Recover XP8 Algorithm A from a completed reset before baseline workload."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HISTORY = ROOT / "history/mysql_four_scene_20260809"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
REFRESH = ROOT / "scripts/refresh_mysql_xp8_live_authority_from_reset_v1.py"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v4.py"
CONTRACT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v4/algorithm_a_top4_contract.json"
PREFIX = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}
MANAGEMENT_CPUSET = "488-489,498-499"
LOG_ROOT = HISTORY / "formal_launch_logs"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
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
    parser.add_argument("--scene", choices=sorted(PREFIX), required=True)
    parser.add_argument("--b-state", type=Path, required=True)
    parser.add_argument("--failed-a-state", type=Path, required=True)
    parser.add_argument("--reset-binding", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--pipeline-root", type=Path, required=True)
    parser.add_argument("--unit", required=True)
    args = parser.parse_args()
    scene = args.scene
    prefix = PREFIX[scene]
    b_state = load(args.b_state.resolve())
    failed = load(args.failed_a_state.resolve())
    reset = load(args.reset_binding.resolve())
    expected = {f"{prefix}_w{i}" for i in range(1, 9)}
    reset_workers = {
        str(row.get("worker_id") or "") for row in reset.get("workers") or []
    }
    if not (
        b_state.get("algorithm") == "B"
        and b_state.get("async_scene") == scene
        and b_state.get("status") == "COMPLETE"
        and (b_state.get("converged") or {}).get(scene) is True
        and failed.get("algorithm") == "A"
        and failed.get("async_scene") == scene
        and failed.get("status") == "FAILED_CLOSED"
        and int(failed.get("accepted") or 0) == 0
        and "counter cardinality differs" in str(failed.get("error") or "")
        and reset.get("status") == "PASS"
        and reset_workers == expected
    ):
        raise RuntimeError("XP8 A reset-recovery boundary differs")
    if not args.unit.endswith(".service") or "/" in args.unit:
        parser.error("unit must be a .service basename")
    output = HISTORY / args.run_id
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh recovery output required: {output}")
    authority_output = args.authority_root / scene
    refreshed = subprocess.run(
        [str(PYTHON), str(REFRESH), "--scene", scene,
         "--reset-binding", str(args.reset_binding.resolve()),
         "--output", str(authority_output.resolve())],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=720,
    )
    if refreshed.returncode != 0:
        raise RuntimeError(f"A recovery authority failed: {refreshed.stderr[-3000:]}")
    authority = authority_output / "live_authority.json"
    stdout = LOG_ROOT / f"{args.run_id}.stdout.log"
    stderr = LOG_ROOT / f"{args.run_id}.stderr.log"
    command = [
        "systemd-run", "--user", f"--unit={args.unit.removesuffix('.service')}",
        f"--description=MySQL XP8 Algorithm A v4 recovery {scene}",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER), "--run-id", args.run_id,
        "--scene", scene, "--launch-authority", str(authority),
        "--output-dir", str(output),
    ]
    launched = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
    )
    if launched.returncode != 0:
        raise RuntimeError(f"A recovery launch failed: {launched.stderr[-2000:]}")
    state_path = output / "algorithm_a_state.json"
    deadline = time.monotonic() + 300.0
    while time.monotonic() < deadline:
        shown = subprocess.run(
            ["systemctl", "--user", "show", args.unit,
             "-p", "ActiveState", "-p", "MainPID"],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=15,
        ).stdout
        if "ActiveState=failed" in shown or "ActiveState=inactive" in shown:
            raise RuntimeError(f"A recovery exited during startup: {shown}; {stderr}")
        if state_path.is_file():
            state = load(state_path)
            if (
                state.get("algorithm") == "A"
                and state.get("async_scene") == scene
                and state.get("status") == "RUNNING"
                and state.get("physical_replay_parallelism") == 8
                and state.get("candidate_count_per_round") == 16
            ):
                receipt = {
                    "schema_version": "mysql_algorithm_a_xp8_reset_recovery_launch_v1",
                    "status": "LAUNCHED", "launched_at": now(), "scene": scene,
                    "formal_scope": "mysql_only",
                    "cross_engine_comparison_prohibited": True,
                    "algorithm_b_sequence_gate_path": str(args.b_state.resolve()),
                    "algorithm_b_sequence_gate_sha256": sha(args.b_state.resolve()),
                    "failed_a_state_path": str(args.failed_a_state.resolve()),
                    "failed_a_state_sha256": sha(args.failed_a_state.resolve()),
                    "reset_binding_path": str(args.reset_binding.resolve()),
                    "reset_binding_sha256": sha(args.reset_binding.resolve()),
                    "failed_reset_reused_as_accepted_or_outer_credit": False,
                    "fresh_algorithm_a_search": True,
                    "authority": str(authority), "authority_sha256": sha(authority),
                    "run_id": args.run_id, "unit": args.unit,
                    "output": str(output), "state": str(state_path),
                    "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
                    "controller": str(CONTROLLER),
                    "controller_sha256": sha(CONTROLLER),
                    "launcher": str(Path(__file__).resolve()),
                    "launcher_sha256": sha(Path(__file__).resolve()),
                    "external_process_control_or_stop_performed": False,
                }
                atomic(args.pipeline_root / f"{prefix}.recovery_launch.json", receipt)
                print(json.dumps(receipt, sort_keys=True))
                return 0
        time.sleep(1.0)
    raise TimeoutError("A recovery startup evidence timed out")


if __name__ == "__main__":
    raise SystemExit(main())
