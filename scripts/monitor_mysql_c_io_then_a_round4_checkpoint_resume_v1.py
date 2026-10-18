#!/usr/bin/env python3
"""Handoff terminal XP/IO C directly to the sole remaining A continuation."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
C_RUN = HISTORY / "v1038_to_c_io_outer13_v90_exact_fallback_resume_20260828_xp_io_xp_io"
C_STATE = C_RUN / "run_state.json"
RECONCILIATION = (
    HISTORY / "v1041_c_io_async_terminal_reconciliation_20260828"
    / "terminal_reconciliation.json"
)
PROJECTION = (
    HISTORY / "v1042_c_io_effective_terminal_projection_20260828"
    / "run_state.json"
)
RESET_ROOT = HISTORY / "v1043_a_io_round4_post_c_transition_reset_20260828"
AUTHORITY_ROOT = HISTORY / "v1044_a_io_round4_post_c_live_authority_20260828"
LIVE_AUTHORITY = AUTHORITY_ROOT / "live_authority.json"
A_RUN_ID = "v1045_to_a_io_round4_checkpoint_resume_20260828_xp_io"
A_OUTPUT = HISTORY / A_RUN_ID
A_STATE = A_OUTPUT / "algorithm_a_state.json"
A_UNIT = "mysql-alga-v1045_to_a_io_round4_checkpoint_resume_20260828_xp_io.service"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_xp_io_round4_checkpoint_resume_20260828_v1"
    / "transition_contract.json"
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def run(argv: list[str], *, timeout: float = 14400) -> None:
    completed = subprocess.run(
        argv, cwd=ROOT, text=True, capture_output=True, check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {argv!r}: "
            f"{completed.stdout[-2000:]} {completed.stderr[-3000:]}"
        )


def systemd_state(unit: str) -> str:
    return subprocess.run(
        ["systemctl", "--user", "is-active", unit], cwd=ROOT,
        text=True, capture_output=True, check=False, timeout=30,
    ).stdout.strip()


def wait_for_c_terminal() -> dict[str, Any]:
    while True:
        state = load(C_STATE)
        status = str(state.get("status") or "")
        if status == "RUNNING":
            time.sleep(15)
            continue
        if (
            status == "NON_CONVERGED"
            and state.get("stage") == "FINISHED"
            and state.get("error") in (None, "")
            and (state.get("converged") or {}).get("hybench_xp_io") is True
        ):
            return state
        raise RuntimeError(
            "XP/IO C did not reach the converged asynchronous terminal: "
            f"status={status!r} stage={state.get('stage')!r} "
            f"error={state.get('error')!r}"
        )


def ensure_terminal_projection() -> None:
    if not RECONCILIATION.is_file():
        run([
            str(PYTHON),
            str(ROOT / "scripts/reconcile_mysql_algorithm_c_async_terminal_v1.py"),
            "--source-run", str(C_RUN), "--output", str(RECONCILIATION),
        ])
    if not PROJECTION.is_file():
        run([
            str(PYTHON),
            str(ROOT / "scripts/project_mysql_algorithm_c_async_terminal_v1.py"),
            "--source-state", str(C_STATE),
            "--reconciliation", str(RECONCILIATION),
            "--output", str(PROJECTION),
        ])
    projected = load(PROJECTION)
    if (
        projected.get("status") != "COMPLETE"
        or projected.get("async_scene") != "hybench_xp_io"
        or (projected.get("converged") or {}).get("hybench_xp_io") is not True
    ):
        raise RuntimeError("XP/IO C effective terminal projection differs")


def ensure_live_authority() -> None:
    if not LIVE_AUTHORITY.is_file():
        run([
            str(PYTHON),
            str(ROOT / "scripts/prepare_mysql_algorithm_a_xp_io_round4_after_c_v1.py"),
            "--c-terminal-state", str(PROJECTION),
            "--reset-root", str(RESET_ROOT),
            "--authority-root", str(AUTHORITY_ROOT),
        ])
    authority = load(LIVE_AUTHORITY)
    if (
        authority.get("status") != "PASS"
        or authority.get("scene") != "hybench_xp_io"
        or int(authority.get("physical_replay_parallelism") or 0) != 8
    ):
        raise RuntimeError("XP/IO A post-C live authority differs")


def ensure_contract() -> None:
    if not CONTRACT.is_file():
        run([
            str(PYTHON),
            str(ROOT / "scripts/build_mysql_algorithm_a_xp_io_round4_checkpoint_resume_contract_v1.py"),
            "--live-authority", str(LIVE_AUTHORITY),
        ])
    # Validate all source hashes, including the just-frozen live authority.
    run([
        str(PYTHON), "-c",
        (
            "from pathlib import Path; "
            "from scripts import run_mysql_algorithm_a_xp_io_round4_checkpoint_resume_v1 as m; "
            f"m.validate_contract(Path({str(LIVE_AUTHORITY)!r}))"
        ),
    ], timeout=60)


def launch_a() -> None:
    if A_STATE.is_file():
        state = load(A_STATE)
        if state.get("status") in {"RUNNING", "COMPLETE"}:
            return
        raise RuntimeError(f"existing XP/IO A state is not resumable: {state.get('status')}")
    if systemd_state(A_UNIT) == "active":
        return
    run([
        "systemd-run", "--user", f"--unit={A_UNIT}",
        "--description=MySQL Algorithm A XP IO Round4 checkpoint resume",
        "--property=WorkingDirectory=" + str(ROOT),
        "--property=KillMode=control-group", "--property=TimeoutStopSec=180",
        "/usr/bin/taskset", "-c", "484-487", str(PYTHON),
        str(ROOT / "scripts/run_mysql_algorithm_a_xp_io_round4_checkpoint_resume_v1.py"),
        "--run-id", A_RUN_ID, "--scene", "hybench_xp_io",
        "--launch-authority", str(LIVE_AUTHORITY),
        "--output-dir", str(A_OUTPUT),
    ], timeout=60)
    time.sleep(3)
    if systemd_state(A_UNIT) != "active" or not A_STATE.is_file():
        raise RuntimeError("XP/IO A Round4 continuation did not remain active")


def main() -> int:
    wait_for_c_terminal()
    ensure_terminal_projection()
    ensure_live_authority()
    ensure_contract()
    launch_a()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
