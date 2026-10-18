#!/usr/bin/env python3
"""Independently hand each completed v61 C scene to its v39 B successor."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
HISTORY = ROOT / "history/mysql_four_scene_20260809"
OUTPUT = HISTORY / "formal_launch_logs/v277_bc_qps_rejection_monitor.json"
MEMORY_B_LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_b_qps_rejection_scene_v2.py"
XP_B_LAUNCHER = ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v21.py"
MEMORY_B_CAMPAIGN = "v276_to_b_qps_rejection_20260826_attempt001"
C_STATES = {
    "job_memory": HISTORY / "v272_to_c_qps_rejection_20260826_attempt001_job_scene_job/run_state.json",
    "tpch_sf4_memory": HISTORY / "v272_to_c_qps_rejection_20260826_attempt001_tpch_scene_tpch/run_state.json",
    "hybench_xp_cpu": HISTORY / "v272_to_c_qps_rejection_20260826_attempt001_cpu_scene_xp_cpu/run_state.json",
    "hybench_xp_io": HISTORY / "v272_to_c_qps_rejection_20260826_attempt001_io_scene_xp_io/run_state.json",
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


def atomic(value: Mapping[str, Any]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = OUTPUT.with_name(f".{OUTPUT.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, OUTPUT)


def c_complete(scene: str, state: Mapping[str, Any]) -> bool:
    return bool(
        state.get("algorithm") == "C"
        and state.get("async_scene") == scene
        and state.get("stage") == "FINISHED"
        and state.get("status") == "COMPLETE"
        and (state.get("converged") or {}).get(scene) is True
        and not state.get("error")
    )


def launch_b(scene: str, c_path: Path) -> subprocess.CompletedProcess[str]:
    if scene in {"job_memory", "tpch_sf4_memory"}:
        command = [
            str(PYTHON), str(MEMORY_B_LAUNCHER), "--scene", scene,
            "--c-state", str(c_path), "--campaign-id", MEMORY_B_CAMPAIGN,
        ]
    else:
        command = [str(PYTHON), str(XP_B_LAUNCHER), "--scene", scene]
    return subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True,
        check=False, timeout=1200,
    )


def main() -> int:
    document: dict[str, Any] = {
        "schema_version": "mysql_bc_qps_rejection_independent_handoff_v1",
        "status": "MONITORING",
        "started_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "four_scene_barrier": False,
        "scene_handoffs": {
            scene: {"status": "WAITING_C", "c_state": str(path)}
            for scene, path in C_STATES.items()
        },
    }
    atomic(document)
    while True:
        terminal = True
        for scene, path in C_STATES.items():
            row = document["scene_handoffs"][scene]
            if row["status"] in {"B_LAUNCHED", "C_FAILED"}:
                continue
            terminal = False
            if not path.is_file():
                continue
            state = load(path)
            row.update({
                "c_status": state.get("status"),
                "c_stage": state.get("stage"),
                "c_outer_attempt": state.get("outer_attempt"),
                "c_accepted_rounds": (state.get("accepted_rounds") or {}).get(scene),
                "c_rejected_outer_attempts": (
                    state.get("rejected_outer_attempts") or {}
                ).get(scene),
                "c_normalized_width": (
                    state.get("last_interval_convergence") or {}
                ).get(scene),
            })
            if state.get("status") in {"FAILED_CLOSED", "NON_CONVERGED"}:
                row.update({"status": "C_FAILED", "observed_at": now()})
                continue
            if not c_complete(scene, state):
                continue
            row.update({
                "status": "LAUNCHING_B",
                "c_completed_at": state.get("completed_at"),
                "c_state_sha256": sha(path),
            })
            atomic(document)
            launched = launch_b(scene, path)
            row.update({
                "b_launcher_exit_code": launched.returncode,
                "b_launcher_stdout": launched.stdout[-4000:],
                "b_launcher_stderr": launched.stderr[-4000:],
                "observed_at": now(),
            })
            if launched.returncode == 0:
                row["status"] = "B_LAUNCHED"
            else:
                row["status"] = "B_LAUNCH_RETRY"
        atomic(document)
        if terminal:
            document["status"] = (
                "COMPLETE"
                if all(
                    row["status"] == "B_LAUNCHED"
                    for row in document["scene_handoffs"].values()
                )
                else "FAILED_PARTIAL"
            )
            document["completed_at"] = now()
            atomic(document)
            return 0 if document["status"] == "COMPLETE" else 2
        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
