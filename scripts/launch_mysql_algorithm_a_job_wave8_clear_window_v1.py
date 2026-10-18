#!/usr/bin/env python3
"""Resume JOB wave 8 at the first BFS-free window and attach its observer."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import watch_mysql_algorithm_c_async_noninterference_v1 as scan


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
RUNNER = ROOT / "scripts/run_mysql_algorithm_a_job_round10_wave8_interference_resume_v2.py"
OBSERVER = ROOT / "scripts/watch_mysql_algorithm_a_job_noninterference_v1.py"
HISTORY = ROOT / "history/mysql_four_scene_20260809"
AUTHORITY = HISTORY / "v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1/job_memory.transition_authority.json"
PREDECESSOR = HISTORY / "v147_to_a_job_round10_wave8_width1_resume_20260825_attempt006_job/algorithm_a_state.json"
RUN_ID = "v148_to_a_job_round10_wave8_width1_clear_resume_20260825_attempt007_job"
FORMAL_UNIT = "mysql-alga-v148-job-wave8-width1-clear-resume.service"
OBSERVER_UNIT = "mysql-alga-observer-v148-job-wave8-width1-clear-resume.service"
OUTPUT = HISTORY / RUN_ID


def unit_fields(unit: str) -> dict[str, str]:
    result = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "MainPID", "-p", "SubState", "-p", "Result"],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=10,
    )
    return dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    if args.wait_seconds <= 0 or OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError("positive wait and fresh JOB output are required")
    deadline = time.monotonic() + args.wait_seconds
    while time.monotonic() < deadline:
        if not scan.heavyweight_external_processes():
            time.sleep(2.0)
            if not scan.heavyweight_external_processes():
                break
        time.sleep(0.5)
    else:
        raise RuntimeError("no two-second BFS-free JOB launch window")

    log_root = HISTORY / "formal_launch_logs"
    log_root.mkdir(parents=True, exist_ok=True)
    started = subprocess.run(
        [
            "systemd-run", "--user", f"--unit={FORMAL_UNIT.removesuffix('.service')}",
            "--description=MySQL Algorithm A JOB wave8 clear-window resume",
            f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
            f"--property=StandardOutput=append:{log_root / (RUN_ID + '.stdout.log')}",
            f"--property=StandardError=append:{log_root / (RUN_ID + '.stderr.log')}",
            "/usr/bin/taskset", "-c", "240-241,496-497", str(PYTHON), str(RUNNER),
            "--run-id", RUN_ID, "--scene", "job_memory",
            "--predecessor-state", str(PREDECESSOR),
            "--runtime-preflight", str(AUTHORITY),
            "--prepared-binding", str(AUTHORITY),
            "--benchmark-accounts", str(AUTHORITY),
            "--fresh-r0", str(AUTHORITY), "--output-dir", str(OUTPUT),
        ],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )
    if started.returncode != 0:
        raise RuntimeError(started.stderr[-1000:])
    deadline = time.monotonic() + 60.0
    state = OUTPUT / "algorithm_a_state.json"
    while time.monotonic() < deadline and not state.is_file():
        fields = unit_fields(FORMAL_UNIT)
        if fields.get("ActiveState") in {"failed", "inactive"}:
            raise RuntimeError(f"JOB exited before state creation: {fields}")
        time.sleep(0.25)
    if not state.is_file():
        raise RuntimeError("JOB state creation timed out")
    observed = subprocess.run(
        [
            "systemd-run", "--user", f"--unit={OBSERVER_UNIT.removesuffix('.service')}",
            "--description=MySQL Algorithm A JOB v148 protected observer",
            f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
            str(PYTHON), str(OBSERVER), "--formal-unit", FORMAL_UNIT,
            "--formal-run-id", RUN_ID,
            "--output", str(OUTPUT / "runtime_noninterference_observer.jsonl"),
            "--interval-seconds", "1",
        ],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )
    if observed.returncode != 0:
        raise RuntimeError(observed.stderr[-1000:])
    print(FORMAL_UNIT, OBSERVER_UNIT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
