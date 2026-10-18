#!/usr/bin/env python3
"""Build the audited C-to-B release and launch B after direct C completion."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
SCENES = (
    "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
)
PREFLIGHT = ROOT / "preflight/mysql_four_scene_20260809/v54_recovered_xp_rebind_r0_20260817_attempt012_preflight.json"
PREPARED = ROOT / "history/mysql_four_scene_20260809/v68_outer16_post_baseline_rebind_20260817_attempt006_rebind/prepared_binding.json"
ACCOUNTS = ROOT / "history/mysql_four_scene_20260809/v68_outer16_post_baseline_rebind_20260817_attempt006_accounts/benchmark_accounts_binding.json"
R0 = ROOT / "history/mysql_four_scene_20260809/v68_outer16_post_baseline_rebind_20260817_attempt006/continuation_r0/r0_binding.json"
PRIOR_QUARANTINE = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v64_replay_quarantine_v13_outer15_20260817T0652p0800/replay_quarantine.json"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    temporary.replace(path)


def run(command: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=timeout, check=True,
    )


def unit_inactive(unit: str) -> bool:
    completed = subprocess.run(
        ["systemctl", "--user", "is-active", unit], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
        check=False,
    )
    return completed.stdout.strip() not in {"active", "activating", "reloading"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm-c-run-id", required=True)
    parser.add_argument("--algorithm-b-run-id", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--state-output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=8 * 3600)
    args = parser.parse_args()
    for run_id in (args.algorithm_c_run_id, args.algorithm_b_run_id):
        if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", run_id) is None:
            parser.error(f"unsafe run id: {run_id}")
    output_root = args.output_root.resolve()
    if output_root.exists():
        raise RuntimeError("fresh C-to-B output root required")
    output_root.mkdir(parents=True, mode=0o700)
    c_root = ROOT / "history/mysql_four_scene_20260809" / args.algorithm_c_run_id
    c_state_path = c_root / "run_state.json"
    c_unit = f"mysql-four-scene-algc-{args.algorithm_c_run_id}.service"
    c_watchdog_unit = f"mysql-four-scene-algc-watchdog-{args.algorithm_c_run_id}.service"
    c_affinity_unit = f"mysql-four-scene-algc-affinity-{args.algorithm_c_run_id}.service"
    atomic(args.state_output, {
        "status": "WAITING_FOR_ALGORITHM_C",
        "algorithm_c_run_id": args.algorithm_c_run_id,
        "algorithm_b_run_id": args.algorithm_b_run_id,
        "started_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
    })
    started = time.monotonic()
    terminal: dict[str, Any] | None = None
    while time.monotonic() - started < args.timeout_seconds:
        if c_state_path.is_file():
            state = load(c_state_path)
            if state.get("status") in {"COMPLETE", "NON_CONVERGED", "FAILED_CLOSED"}:
                terminal = state
                break
        time.sleep(5)
    if terminal is None:
        atomic(args.state_output, {"status": "TIMEOUT", "algorithm_b_launch_performed": False})
        return 2
    if (
        terminal.get("status") != "COMPLETE"
        or terminal.get("stage") != "FINISHED"
        or terminal.get("error") not in (None, "")
        or terminal.get("formal_scope") != "mysql_only"
        or terminal.get("cross_engine_comparison_prohibited") is not True
        or terminal.get("formal_evidence_eligible") is not True
        or terminal.get("accepted_state_credit_prohibited") is not False
        or set(terminal.get("converged") or {}) != set(SCENES)
        or not all((terminal.get("converged") or {}).values())
        or any(int((terminal.get("accepted_rounds") or {}).get(scene, -1)) != 10 for scene in SCENES)
        or any(int((terminal.get("interval_stable_rounds") or {}).get(scene, -1)) < 2 for scene in SCENES)
    ):
        atomic(args.state_output, {
            "status": "ALGORITHM_C_NOT_COMPLETE_FAIL_CLOSED",
            "algorithm_c_status": terminal.get("status"),
            "algorithm_c_stage": terminal.get("stage"),
            "algorithm_c_error": terminal.get("error"),
            "algorithm_b_launch_performed": False,
        })
        return 3

    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if all(unit_inactive(unit) for unit in (c_unit, c_watchdog_unit, c_affinity_unit)):
            break
        time.sleep(2)
    else:
        atomic(args.state_output, {
            "status": "ALGORITHM_C_UNITS_NOT_TERMINAL",
            "algorithm_b_launch_performed": False,
        })
        return 3

    checkpoint_dir = output_root / "algorithm_c_final_checkpoint"
    checkpoint_result = run([
        str(PYTHON), "-m", "scripts.build_mysql_algorithm_c_outer_checkpoint_v4",
        "--run-root", str(c_root), "--output-dir", str(checkpoint_dir),
    ])
    checkpoint = checkpoint_dir / "prepared_binding.json"
    release = output_root / "algorithm_b_from_c_interval_release_v2.json"
    release_result = run([
        str(PYTHON), "scripts/build_mysql_algorithm_b_from_algorithm_c_release_v2.py",
        "--algorithm-c-checkpoint", str(checkpoint), "--output", str(release),
    ])
    quarantine = output_root / "algorithm_b_replay_quarantine/replay_quarantine.json"
    quarantine_result = run([
        str(PYTHON), "scripts/rebind_mysql_algorithm_c_empty_replay_quarantine_v2.py",
        "--prior", str(PRIOR_QUARANTINE), "--checkpoint", str(checkpoint),
        "--output", str(quarantine),
    ])
    launch_result = run([
        str(PYTHON), "scripts/launch_mysql_four_scene_algorithm_b_from_c_v2.py",
        "--run-id", args.algorithm_b_run_id,
        "--algorithm-b-release", str(release), "--checkpoint", str(checkpoint),
        "--runtime-preflight", str(PREFLIGHT),
        "--prepared-binding", str(PREPARED),
        "--benchmark-accounts", str(ACCOUNTS),
        "--fresh-r0", str(R0), "--replay-quarantine", str(quarantine),
    ], timeout=360)
    receipt = json.loads(launch_result.stdout.strip().splitlines()[-1])
    if (
        receipt.get("status") != "STARTED"
        or receipt.get("algorithm") != "B"
        or receipt.get("formal_scope") != "mysql_only"
        or receipt.get("cross_engine_comparison_prohibited") is not True
        or receipt.get("external_affinity_guard_launched") is not True
    ):
        raise RuntimeError("Algorithm B launch receipt differs")
    atomic(args.state_output, {
        "status": "ALGORITHM_B_STARTED",
        "completed_at": datetime.now().astimezone().isoformat(),
        "algorithm_c_run_id": args.algorithm_c_run_id,
        "algorithm_c_outer_attempts": terminal.get("outer_attempt"),
        "algorithm_b_run_id": args.algorithm_b_run_id,
        "algorithm_b_systemd_unit": receipt.get("systemd_unit"),
        "algorithm_b_run_state_path": receipt.get("run_state_path"),
        "algorithm_b_launch_performed": True,
        "checkpoint_stdout": checkpoint_result.stdout.strip(),
        "release_stdout": release_result.stdout.strip(),
        "quarantine_stdout": quarantine_result.stdout.strip(),
    })
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        raise
