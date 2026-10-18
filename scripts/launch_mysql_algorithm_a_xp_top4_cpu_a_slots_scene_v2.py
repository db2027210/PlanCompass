#!/usr/bin/env python3
"""Launch XP Top4-A on mixed CPU-A slots after its scene-local B."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python").resolve()
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_xp_top4_cpu_a_slots_v2.py"
REFRESH = ROOT / "scripts/refresh_mysql_cpu_a_slots_two_worker_scene_authority_v2.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_cpu_a_slots_20260828_v11"
    / "algorithm_a_top4_contract.json"
)
MANAGEMENT_CPUSET = "240-241,496-497"
SCENES = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}


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
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n", encoding="ascii")


def under_history(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if HISTORY.resolve() not in path.parents:
        raise argparse.ArgumentTypeError("path must be under formal history")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    parser.add_argument("--b-state", type=under_history, required=True)
    parser.add_argument("--source-authority", type=under_history, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--pipeline-root", type=under_history, required=True)
    parser.add_argument("--authority-root", type=under_history, required=True)
    parser.add_argument("--unit", required=True)
    args = parser.parse_args()
    scene = args.scene
    suffix = SCENES[scene]
    b_state = load(args.b_state)
    if not (
        b_state.get("algorithm") == "B" and b_state.get("async_scene") == scene
        and b_state.get("status") == "COMPLETE" and b_state.get("stage") == "FINISHED"
        and not b_state.get("error") and bool((b_state.get("converged") or {}).get(scene))
    ):
        raise RuntimeError(f"{scene} Algorithm B is not converged")
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None or not args.run_id.endswith(f"_{suffix}"):
        parser.error("unsafe scene-suffixed run-id")
    if not args.unit.endswith(".service") or "/" in args.unit:
        parser.error("unit must be a .service basename")
    output = HISTORY / args.run_id
    receipt = args.pipeline_root / f"{suffix}.launch.json"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh A output required: {output}")
    authority = args.authority_root / scene
    refreshed = subprocess.run([
        str(PYTHON), str(REFRESH), "--scene", scene,
        "--source-authority", str(args.source_authority),
        "--sequence-state", str(args.b_state), "--output", str(authority),
    ], cwd=ROOT, text=True, capture_output=True, check=False, timeout=300)
    if refreshed.returncode != 0:
        raise RuntimeError(f"{scene} two-worker A authority refresh failed: {refreshed.stderr[-4000:]}")
    live_path = authority / "live_authority.json"
    live = load(live_path)
    if live.get("status") != "PASS" or live.get("scene") != scene:
        raise RuntimeError(f"{scene} two-worker A authority differs")
    stdout = LOG_ROOT / f"{args.run_id}.stdout.log"
    stderr = LOG_ROOT / f"{args.run_id}.stderr.log"
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    command = [
        "systemd-run", "--user", f"--unit={args.unit.removesuffix('.service')}",
        f"--description=MySQL two-worker Top4 Algorithm A {scene}",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER), "--run-id", args.run_id,
        "--scene", scene, "--launch-authority", str(live_path),
        "--output-dir", str(output),
    ]
    launched = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=30)
    if launched.returncode != 0:
        raise RuntimeError(f"{scene} Algorithm A launch failed: {launched.stderr[-3000:]}")
    state_path = output / "algorithm_a_state.json"
    deadline = time.monotonic() + 900.0
    while time.monotonic() < deadline:
        shown = subprocess.run([
            "systemctl", "--user", "show", args.unit, "-p", "ActiveState", "-p", "MainPID",
        ], cwd=ROOT, text=True, capture_output=True, check=False, timeout=15).stdout
        if "ActiveState=failed" in shown or "ActiveState=inactive" in shown:
            raise RuntimeError(f"{scene} two-worker A exited during startup: {shown}; {stderr}")
        if state_path.is_file():
            state = load(state_path)
            if (
                state.get("algorithm") == "A" and state.get("async_scene") == scene
                and state.get("status") == "RUNNING"
                and state.get("four_scene_wave_barrier") is False
                and state.get("other_scene_completion_required") is False
                and state.get("candidate_count_per_round") == 16
                and state.get("physical_replay_parallelism") == 2
            ):
                document = {
                    "schema_version": "mysql_algorithm_a_xp_top4_cpu_a_slots_scene_launch_v2",
                    "status": "LAUNCHED", "launched_at": now(), "scene": scene,
                    "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
                    "algorithm_b_sequence_gate_path": str(args.b_state),
                    "algorithm_b_sequence_gate_sha256": sha(args.b_state),
                    "algorithm_b_state_or_result_credit": False,
                    "authority_refresh_path": str(live_path),
                    "authority_refresh_sha256": sha(live_path),
                    "run_id": args.run_id, "unit": args.unit,
                    "output": str(output), "state": str(state_path),
                    "stdout": str(stdout), "stderr": str(stderr),
                    "contract_path": str(CONTRACT), "contract_sha256": sha(CONTRACT),
                    "candidate_count_per_round": 16, "physical_replay_parallelism": 2,
                }
                atomic(receipt, document)
                print(json.dumps(document, ensure_ascii=False, sort_keys=True))
                return 0
        time.sleep(1.0)
    raise TimeoutError(f"{scene} two-worker Algorithm A startup timed out")


if __name__ == "__main__":
    raise SystemExit(main())
