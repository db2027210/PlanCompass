#!/usr/bin/env python3
"""Independently launch and monitor one XP Top4-A after its bound B completes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_mixed_four_worker_v10.py"
SCENES = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}


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
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def terminal(state: Mapping[str, Any], algorithm: str, scene: str) -> bool:
    return bool(
        state.get("algorithm") == algorithm
        and state.get("async_scene") == scene
        and state.get("status") == "COMPLETE"
        and state.get("stage") == "FINISHED"
        and not state.get("error")
        and (state.get("converged") or {}).get(scene) is True
    )


def compact(state: Mapping[str, Any], scene: str) -> dict[str, Any]:
    return {
        "status": state.get("status"), "stage": state.get("stage"),
        "outer_attempt": state.get("outer_attempt"),
        "accepted_rounds": (state.get("accepted_rounds") or {}).get(scene),
        "rejected_outer_attempts": (
            state.get("rejected_outer_attempts") or {}
        ).get(scene),
        "accepted": state.get("accepted"),
        "converged": (state.get("converged") or {}).get(scene),
        "error": state.get("error"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    parser.add_argument("--b-state", type=Path, required=True)
    parser.add_argument("--a-run-id", required=True)
    parser.add_argument("--pipeline-root", type=Path, required=True)
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--a-unit", required=True)
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    args = parser.parse_args()
    scene = args.scene
    suffix = SCENES[scene]
    state_path = args.pipeline_root / f"{suffix}.pipeline_state.json"
    a_state_path = HISTORY / args.a_run_id / "algorithm_a_state.json"
    receipt = args.pipeline_root / "launches" / f"{suffix}.launch.json"
    launched = receipt.is_file()
    while True:
        b_state = load(args.b_state) if args.b_state.is_file() else {}
        a_state = load(a_state_path) if a_state_path.is_file() else {}
        if terminal(a_state, "A", scene):
            status = "COMPLETE"
        elif launched:
            status = "A_RUNNING"
        elif terminal(b_state, "B", scene):
            status = "LAUNCHING_A"
        elif b_state.get("status") in {"FAILED", "FAILED_CLOSED"}:
            status = "B_FAILED_REQUIRES_BOUNDARY_RECOVERY"
        else:
            status = "B_RUNNING"
        document = {
            "schema_version": "mysql_b_then_a_mixed_four_worker_scene_pipeline_v1",
            "status": status, "updated_at": now(), "scene": scene,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "four_scene_barrier": False, "sibling_scene_wait": False,
            "physical_workers": 4, "maximum_scene_mysql_workers": 4,
            "external_process_control_or_stop_performed": False,
            "postgresql_process_control_or_stop_performed": False,
            "b_state": str(args.b_state), "b_progress": compact(b_state, scene),
            "a_state": str(a_state_path), "a_progress": compact(a_state, scene),
            "launcher": str(LAUNCHER), "launcher_sha256": sha(LAUNCHER),
        }
        atomic(state_path, document)
        if status == "COMPLETE":
            return 0
        if status == "LAUNCHING_A":
            result = subprocess.run([
                str(PYTHON), str(LAUNCHER), "--scene", scene,
                "--b-state", str(args.b_state), "--run-id", args.a_run_id,
                "--pipeline-root", str(args.pipeline_root / "launches"),
                "--authority-root", str(args.authority_root),
                "--unit", args.a_unit,
            ], cwd=ROOT, capture_output=True, text=True, check=False, timeout=900)
            if result.returncode != 0:
                document.update({
                    "status": "A_LAUNCH_FAILED_REQUIRES_RECOVERY",
                    "launch_returncode": result.returncode,
                    "launch_stdout_tail": result.stdout[-4000:],
                    "launch_stderr_tail": result.stderr[-4000:],
                    "updated_at": now(),
                })
                atomic(state_path, document)
                return result.returncode or 1
            launched = True
            continue
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
