from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts/mysql_external_pg_lane_pause_guard_v1.py"
LANE_FRAGMENT = "/home/anonymous/program/resource_adaptive/shell/pg_algorithm_c_lane_entry.py"
SCENE_FRAGMENT = "/home/anonymous/program/resource_adaptive/shell/run_pg_algorithm_c_scene.py"
ALGORITHM_B_FRAGMENT = "/home/anonymous/program/resource_adaptive/shell/run_pg_algorithm_b_scene.py"
SUPPORT_FRAGMENT = "/home/anonymous/program/resource_adaptive/shell/run_pg_algorithm_c_cohort_support_supervisor.py"
ANCHOR_FRAGMENT = "scripts/mysql_external_compose_stop_maintenance_guard_v1.py"


def sleeper(marker: str, seconds: float, *, session: bool = False) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({seconds})", marker],
        text=True,
        start_new_session=session,
    )


def proc_state(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    return stat[stat.rfind(")") + 2 :].split()[0]


@pytest.mark.parametrize(
    "controller_fragment",
    [LANE_FRAGMENT, SCENE_FRAGMENT, ALGORITHM_B_FRAGMENT, SUPPORT_FRAGMENT],
)
def test_guard_pauses_exact_four_controllers_and_always_resumes(
    tmp_path: Path, controller_fragment: str,
) -> None:
    lanes = [sleeper(controller_fragment, 20, session=True) for _ in range(4)]
    anchor = sleeper(ANCHOR_FRAGMENT, 1.5)
    result = tmp_path / "result.json"
    log = tmp_path / "events.jsonl"
    command = [
        sys.executable,
        str(GUARD),
        "--anchor-pid", str(anchor.pid),
        "--maximum-seconds", "600",
        "--poll-seconds", "0.1",
        "--result", str(result),
        "--log", str(log),
        "--execute",
        "--acknowledge-external-pause",
        "pause-external-pg-lane-controllers-max-8h",
    ]
    for lane in lanes:
        command.extend(["--pid", str(lane.pid)])
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=10)
        assert completed.returncode == 0, completed.stderr
        document = json.loads(result.read_text(encoding="utf-8"))
        assert document["termination_reason"] == "mysql_maintenance_guard_exited"
        assert document["paused_pid_count"] == 4
        assert document["all_controllers_resumed"] is True
        assert len(document["lanes"]) == 4
        assert all(proc_state(lane.pid) not in {"T", "t"} for lane in lanes)
        events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        assert sum(row["event"] == "lane_controller_paused" for row in events) == 4
        assert sum(row["event"] == "lane_controller_resumed" for row in events) == 4
    finally:
        for lane in lanes:
            try:
                os.kill(lane.pid, signal.SIGCONT)
                lane.terminate()
                lane.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                lane.kill()
        if anchor.poll() is None:
            anchor.terminate()
            anchor.wait(timeout=3)
