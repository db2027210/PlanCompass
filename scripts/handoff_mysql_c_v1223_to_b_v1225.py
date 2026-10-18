#!/usr/bin/env python3
"""Start fresh B v1225 after all four fresh C v1223 lanes complete."""

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
C_MANIFEST = HISTORY / (
    "v1223_mysql_c_four_scene_pg_latest_v103_20260905/launch_manifest.json"
)
HANDOFF = HISTORY / "v1224_c_to_b_pg_latest_handoff_20260905"
STATE = HANDOFF / "handoff_state.json"
SOURCE_AUTHORITY = HISTORY / (
    "v1221_to_c_current_identity_authority_20260905/authority"
)
AUTHORITY_RUN_ID = "v1224_to_b_current_identity_authority_20260905"
R0_RUN_ID = "v1224_to_b_current_identity_fresh_r0_20260905"
B_LAUNCHER = ROOT / "scripts/launch_mysql_b_four_scene_pg_latest_v104.py"
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
    (HANDOFF / "handoff_commands.log").open("a", encoding="utf-8").write(
        f"[{now()}] {' '.join(argv)}\n{completed.stdout}\n"
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"handoff command failed ({completed.returncode}): {' '.join(argv)}"
        )


def main() -> int:
    if HANDOFF.exists() or HANDOFF.is_symlink():
        raise RuntimeError(f"fresh handoff root required: {HANDOFF}")
    atomic({
        "schema_version": "mysql_c_v1223_to_b_v1225_handoff_v1",
        "status": "WAITING_FOR_C",
        "created_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "c_manifest": str(C_MANIFEST),
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    })
    while True:
        if not C_MANIFEST.is_file():
            time.sleep(30)
            continue
        c = load(C_MANIFEST)
        status = str(c.get("status") or "")
        if status == "FAILED_CLOSED":
            raise RuntimeError("C v1223 failed closed; B handoff prohibited")
        if status == "COMPLETE":
            scenes = dict(c.get("scenes") or {})
            if (
                len(scenes) != 4
                or any(row.get("status") != "COMPLETE" for row in scenes.values())
            ):
                raise RuntimeError("C v1223 completion scene set differs")
            break
        time.sleep(30)

    atomic({
        **load(STATE),
        "status": "REFRESHING_B_AUTHORITY",
        "c_completed_at": c.get("completed_at"),
        "updated_at": now(),
    })
    # XP reset implementations may leave a clean data plane stopped at the
    # terminal boundary. Starting the same frozen containers is technical
    # handoff only; their identities are read back and sealed immediately.
    for container in XP_CONTAINERS:
        run(["docker", "start", container], timeout=180.0)
    run([
        str(Path("/home/anonymous/miniconda3/envs/py313/bin/python")),
        str(ROOT / "scripts/refresh_mysql_client_primary_authority_after_xp_reset_v2.py"),
        "--run-id", AUTHORITY_RUN_ID,
        "--source-authority-root", str(SOURCE_AUTHORITY),
    ], timeout=900.0)
    authority = HISTORY / AUTHORITY_RUN_ID / "authority"
    run([
        str(Path("/home/anonymous/miniconda3/envs/py313/bin/python")),
        str(ROOT / "scripts/build_mysql_algorithm_c_client_primary_live_authority_v1.py"),
        "--run-id", R0_RUN_ID,
        "--authority-root", str(authority),
    ], timeout=120.0)
    atomic({
        **load(STATE),
        "status": "STARTING_B",
        "authority_root": str(authority),
        "fresh_r0": str(HISTORY / R0_RUN_ID / "fresh_r0_binding.json"),
        "updated_at": now(),
    })
    run([
        str(Path("/home/anonymous/miniconda3/envs/py313/bin/python")),
        str(B_LAUNCHER),
    ], timeout=7 * 24 * 3600.0)
    atomic({
        **load(STATE),
        "status": "COMPLETE",
        "completed_at": now(),
    })
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as error:
        if HANDOFF.exists() and not HANDOFF.is_symlink():
            previous = load(STATE) if STATE.is_file() else {}
            atomic({
                **previous,
                "status": "FAILED_CLOSED",
                "error": f"{type(error).__name__}: {error}",
                "failed_at": now(),
            })
        raise
