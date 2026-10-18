#!/usr/bin/env python3
"""Start fresh B after the four valid independently completed C lanes."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
HANDOFF = HISTORY / "v1229_c_composite_to_b_pg_latest_handoff_20260905"
STATE = HANDOFF / "handoff_state.json"
SOURCE_AUTHORITY = HISTORY / "v1221_to_c_current_identity_authority_20260905/authority"
AUTHORITY_RUN_ID = "v1224_to_b_current_identity_authority_20260905"
R0_RUN_ID = "v1224_to_b_current_identity_fresh_r0_20260905"
B_LAUNCHER = ROOT / "scripts/launch_mysql_b_four_scene_pg_latest_v104.py"
SCENE_MANIFESTS = {
    "job_memory": HISTORY / "v1223_mysql_c_four_scene_pg_latest_v103_20260905/launch_manifest.json",
    "tpch_sf4_memory": HISTORY / "v1223_mysql_c_four_scene_pg_latest_v103_20260905/launch_manifest.json",
    "hybench_xp_io": HISTORY / "v1227_mysql_c_xp_blkio_recovery_20260905/launch_manifest.json",
    "hybench_xp_cpu": HISTORY / "v1228_mysql_c_xp_cpu_preflight_retry_20260905/launch_manifest.json",
}
XP_CONTAINERS = (
    "ra_mysql_disk2_algc_xp_cpu_w1_20260809",
    "ra_mysql_disk2_algc_xp_cpu_w2_20260809",
    "ra_mysql_disk2_algc_xp_io_w1_20260809",
    "ra_mysql_disk2_algc_xp_io_w2_20260809",
)


def now() -> str:
    return datetime.now().astimezone().isoformat()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(value: Mapping[str, Any]) -> None:
    HANDOFF.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = STATE.with_name(f".{STATE.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, STATE)


def run(argv: list[str], *, timeout: float) -> None:
    completed = subprocess.run(
        argv, cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=timeout,
    )
    with (HANDOFF / "handoff_commands.log").open("a", encoding="utf-8") as log:
        log.write(f"[{now()}] {' '.join(argv)}\n{completed.stdout}\n")
    if completed.returncode != 0:
        raise RuntimeError(
            f"handoff command failed ({completed.returncode}): {' '.join(argv)}"
        )


def main() -> int:
    if HANDOFF.exists() or HANDOFF.is_symlink():
        raise RuntimeError(f"fresh handoff root required: {HANDOFF}")
    atomic({
        "schema_version": "mysql_c_composite_to_b_handoff_v1",
        "status": "WAITING_FOR_C",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene_manifests": {key: str(value) for key, value in SCENE_MANIFESTS.items()},
        "failed_prebaseline_c_lanes_inherited": False,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    })
    completed: dict[str, dict[str, Any]] = {}
    while len(completed) != len(SCENE_MANIFESTS):
        for scene, manifest_path in SCENE_MANIFESTS.items():
            if scene in completed or not manifest_path.is_file():
                continue
            manifest = load(manifest_path)
            row = dict((manifest.get("scenes") or {}).get(scene) or {})
            if row.get("status") == "FAILED_CLOSED":
                raise RuntimeError(f"C {scene} replacement lane failed closed")
            if row.get("status") == "COMPLETE" and row.get("returncode") == 0:
                state = dict(row.get("state") or {})
                if state.get("status") != "COMPLETE":
                    raise RuntimeError(f"C {scene} terminal state differs")
                completed[scene] = {
                    "manifest_path": str(manifest_path),
                    "run_id": row.get("run_id"),
                    "state": state,
                }
        atomic({
            **load(STATE),
            "status": "WAITING_FOR_C",
            "completed_scenes": completed,
            "updated_at": now(),
        })
        if len(completed) != len(SCENE_MANIFESTS):
            time.sleep(30)

    atomic({
        **load(STATE), "status": "REFRESHING_B_AUTHORITY",
        "all_four_valid_c_lanes_complete": True, "updated_at": now(),
    })
    for container in XP_CONTAINERS:
        run(["docker", "start", container], timeout=180.0)
    run([
        "/home/anonymous/miniconda3/envs/py313/bin/python",
        str(ROOT / "scripts/refresh_mysql_client_primary_authority_after_xp_reset_v2.py"),
        "--run-id", AUTHORITY_RUN_ID,
        "--source-authority-root", str(SOURCE_AUTHORITY),
    ], timeout=900.0)
    authority = HISTORY / AUTHORITY_RUN_ID / "authority"
    run([
        "/home/anonymous/miniconda3/envs/py313/bin/python",
        str(ROOT / "scripts/build_mysql_algorithm_c_client_primary_live_authority_v1.py"),
        "--run-id", R0_RUN_ID, "--authority-root", str(authority),
    ], timeout=120.0)
    atomic({
        **load(STATE), "status": "STARTING_B", "authority_root": str(authority),
        "fresh_r0": str(HISTORY / R0_RUN_ID / "fresh_r0_binding.json"),
        "updated_at": now(),
    })
    run([
        "/home/anonymous/miniconda3/envs/py313/bin/python", str(B_LAUNCHER),
    ], timeout=7 * 24 * 3600.0)
    atomic({**load(STATE), "status": "COMPLETE", "completed_at": now()})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as error:
        if HANDOFF.exists() and not HANDOFF.is_symlink():
            previous = load(STATE) if STATE.is_file() else {}
            atomic({
                **previous, "status": "FAILED_CLOSED",
                "error": f"{type(error).__name__}: {error}", "failed_at": now(),
            })
        raise
