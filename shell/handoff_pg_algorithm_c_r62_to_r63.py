#!/usr/bin/env python3
"""Cleanly hand off r62 at a wave boundary to fresh r63."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict


ROOT = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809"
)
R62 = ROOT / "20260813_algorithm_c_same_plan_numeric_clean_r62"
R63 = ROOT / "20260813_algorithm_c_prephysical_history_clean_r63"
LANES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
PIDS = (3602239, 3602242, 3602245, 3602249)
SUPERVISOR_PID = 3602252
PYTHON = "/home/anonymous/miniconda3/envs/resource_adaptive/bin/python"
PREPARE = Path(
    "/home/anonymous/program/resource_adaptive/shell/"
    "prepare_pg_algorithm_c_nomutex_cohort_r63.py"
)
LAUNCH = Path(
    "/home/anonymous/program/resource_adaptive/shell/"
    "launch_pg_algorithm_c_nomutex_cohort_r63.sh"
)
RECEIPT = R63 / "r62_to_r63_handoff_receipt.json"


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def alive(pid: int) -> bool:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        fields = stat_path.read_text(encoding="utf-8").split()
    except FileNotFoundError:
        return False
    if len(fields) >= 3 and fields[2] == "Z":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def run_checked(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        cwd="/home/anonymous/program/resource_adaptive",
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed rc={completed.returncode}: {' '.join(argv)}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    started = time.time()
    deadline = started + 3600.0
    while time.time() < deadline:
        states = {
            lane: read_json(R62 / lane / "algorithm_c_state.json")
            for lane in LANES
        }
        if all(
            state.get("phase") == "stopped_by_signal_clean"
            and isinstance(state.get("signal_stop_clean_receipt"), dict)
            and state["signal_stop_clean_receipt"].get("passed") is True
            for state in states.values()
        ):
            break
        time.sleep(5.0)
    else:
        raise TimeoutError("r62 did not reach four clean-stop receipts")

    process_deadline = time.time() + 300.0
    while time.time() < process_deadline and any(alive(pid) for pid in PIDS):
        time.sleep(2.0)
    if any(alive(pid) for pid in PIDS):
        raise RuntimeError("r62 controllers remained alive after clean receipts")
    if alive(SUPERVISOR_PID):
        os.kill(SUPERVISOR_PID, signal.SIGTERM)

    prepare = run_checked([PYTHON, str(PREPARE)], timeout=180.0)
    launch = run_checked([str(LAUNCH)], timeout=60.0)
    states = {
        lane: read_json(R62 / lane / "algorithm_c_state.json")
        for lane in LANES
    }
    manifest = read_json(R63 / "fresh_campaign_manifest.json")
    payload = {
        "contract": "algorithm_c_clean_prephysical_history_handoff_v1",
        "started_at_epoch_s": started,
        "completed_at_epoch_s": time.time(),
        "r62_clean_states": {
            lane: {
                "accepted": int(state.get("accepted") or 0),
                "round_index": int(state.get("round_index") or 0),
                "physical_workload_invocations": int(
                    state.get("physical_workload_invocations") or 0
                ),
                "valid_replays": int(state.get("valid_replays") or 0),
                "technical_failures": int(
                    state.get("technical_failures") or 0
                ),
                "phase": state.get("phase"),
                "clean_receipt_passed": True,
            }
            for lane, state in states.items()
        },
        "r63_campaign": str(R63),
        "r63_manifest_contract": manifest.get("contract"),
        "r63_seed_rows": {
            lane: int((manifest["scenes"][lane]).get("seed_row_count") or 0)
            for lane in LANES
        },
        "prephysical_historical_redecision": True,
        "accepted_state_inherited": False,
        "ranges_inherited": False,
        "gradient_state_inherited": False,
        "technical_failures_inherited": False,
        "physical_workload_counters_inherited": False,
        "prepare_stdout": prepare.stdout.splitlines(),
        "launch_stdout": launch.stdout.splitlines(),
    }
    atomic_json(RECEIPT, payload)
    print(json.dumps(payload, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
