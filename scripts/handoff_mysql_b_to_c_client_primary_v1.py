#!/usr/bin/env python3
"""Wait for the four valid B scenes and launch the frozen v92 C campaign."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
B_STATES = {
    "job_memory": HISTORY / (
        "v1178_to_b_client_primary_four_scene_20260903_job/run_state.json"
    ),
    "tpch_sf4_memory": HISTORY / (
        "v1187_to_b_tpch_client_primary_predecision_continue_20260903_tpch/"
        "run_state.json"
    ),
    "hybench_xp_cpu": HISTORY / (
        "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_cpu/"
        "run_state.json"
    ),
    "hybench_xp_io": HISTORY / (
        "v1184_to_b_client_primary_xp_cleanup_recovery_20260903_xp_io/"
        "run_state.json"
    ),
}
SOURCE_AUTHORITY = HISTORY / (
    "v1183_to_b_xp_blkio_identity_recovery_20260903/authority"
)
AUTHORITY_RUN_ID = "v1188_to_c_client_primary_live_authority_20260903"
FRESH_R0_RUN_ID = "v1189_to_c_client_primary_fresh_r0_20260903"
C_CAMPAIGN_ID = "v1190_to_c_client_primary_four_scene_20260903"
SUPERVISOR_ROOT = HISTORY / "v1188_to_c_client_primary_handoff_20260903"
MANIFEST = SUPERVISOR_ROOT / "handoff_manifest.json"


def now() -> str:
    return datetime.now().astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic(path: Path, value: dict[str, Any]) -> None:
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


def verified_state(path: Path) -> dict[str, Any]:
    sidecar = path.with_name(path.name + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        raise RuntimeError(f"B state artifact absent: {path}")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    if expected != sha(path):
        raise RuntimeError(f"B state sidecar differs: {path}")
    return load(path)


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    if SUPERVISOR_ROOT.exists() or SUPERVISOR_ROOT.is_symlink():
        raise RuntimeError(f"fresh handoff root required: {SUPERVISOR_ROOT}")
    SUPERVISOR_ROOT.mkdir(parents=True, mode=0o700)
    manifest: dict[str, Any] = {
        "schema_version": "mysql_b_to_c_client_primary_handoff_v1",
        "status": "WAITING_FOR_B",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "b_states": {scene: str(path) for scene, path in B_STATES.items()},
        "c_release": "v92-client-primary",
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(MANIFEST, manifest)
    while True:
        states = {scene: verified_state(path) for scene, path in B_STATES.items()}
        failed = {
            scene: state for scene, state in states.items()
            if state.get("status") in {"FAILED", "FAILED_CLOSED"}
            or state.get("error") not in (None, "")
        }
        if failed:
            manifest.update({
                "status": "BLOCKED_BY_B_FAILURE",
                "updated_at": now(),
                "failed_scenes": {
                    scene: {
                        "status": state.get("status"),
                        "stage": state.get("stage"),
                        "error": state.get("error"),
                    }
                    for scene, state in failed.items()
                },
            })
            atomic(MANIFEST, manifest)
            return 4
        complete = all(
            state.get("status") == "COMPLETE"
            and (state.get("converged") or {}).get(scene) is True
            for scene, state in states.items()
        )
        manifest.update({
            "updated_at": now(),
            "b_progress": {
                scene: {
                    "status": state.get("status"),
                    "stage": state.get("stage"),
                    "outer_attempt": state.get("outer_attempt"),
                    "accepted_rounds": (
                        state.get("accepted_rounds") or {}
                    ).get(scene),
                    "rejected_outer_attempts": (
                        state.get("rejected_outer_attempts") or {}
                    ).get(scene),
                    "converged": (state.get("converged") or {}).get(scene),
                    "state_sha256": sha(B_STATES[scene]),
                }
                for scene, state in states.items()
            },
        })
        atomic(MANIFEST, manifest)
        if complete:
            break
        time.sleep(30)

    manifest.update({"status": "REFRESHING_C_AUTHORITY", "updated_at": now()})
    atomic(MANIFEST, manifest)
    python = sys.executable
    run([
        python,
        "scripts/refresh_mysql_client_primary_authority_after_xp_reset_v2.py",
        "--run-id", AUTHORITY_RUN_ID,
        "--source-authority-root", str(SOURCE_AUTHORITY),
    ])
    authority = HISTORY / AUTHORITY_RUN_ID / "authority"
    run([
        python,
        "scripts/build_mysql_algorithm_c_client_primary_live_authority_v1.py",
        "--run-id", FRESH_R0_RUN_ID,
        "--authority-root", str(authority),
    ])
    fresh_r0 = HISTORY / FRESH_R0_RUN_ID / "fresh_r0_binding.json"
    manifest.update({
        "status": "LAUNCHING_C",
        "updated_at": now(),
        "authority_root": str(authority),
        "fresh_r0": str(fresh_r0),
    })
    atomic(MANIFEST, manifest)
    run([
        python,
        "scripts/launch_mysql_algorithm_c_client_primary_four_scene_v2.py",
        "--campaign-id", C_CAMPAIGN_ID,
        "--authority-root", str(authority),
        "--fresh-r0", str(fresh_r0),
    ])
    manifest.update({
        "status": "COMPLETE",
        "updated_at": now(),
        "c_campaign": str(HISTORY / C_CAMPAIGN_ID),
    })
    atomic(MANIFEST, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
