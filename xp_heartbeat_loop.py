from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

from write_xp_heartbeat import write_heartbeat


ROOT = Path(__file__).resolve().parent
HEARTBEAT = ROOT / "history/mysql_sgd_env_agents_20260723/manual_controllers/hybench_xp_io/subagent_heartbeat.json"
CAMPAIGN_LOCK = ROOT / "history/mysql_sgd_env_agents_20260723/host02_job_xp_campaign.lock"
PRESSURE_LOCK = Path("/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_pressure.lock")


def lock_state(path: Path) -> str:
    if not path.exists():
        return "free_absent"
    try:
        with path.open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "held"
            finally:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        return "free_present"
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}"


while True:
    campaign = lock_state(CAMPAIGN_LOCK)
    pressure = lock_state(PRESSURE_LOCK)
    try:
        active = subprocess.check_output(
            ["pgrep", "-af", "(manual_mysql_sgd_r0_campaign.py --scenario xp|run_xp_r0_with_candidate_jar.py)"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        active = ""
    running = bool(active)
    write_heartbeat(
        HEARTBEAT,
        status="xp_r0_capture_running" if running else "waiting_or_post_r0_fail_closed",
        current_task="xp_production_capture_and_fresh_r0" if running else "xp_startup_evidence",
        mutex_state=f"campaign={campaign};pressure={pressure};shared_host_concurrent_interference=true",
        last_command=active or "xp_heartbeat_loop_lock_probe",
        last_evidence="xp_capture_r0_runtime_samples" if running else "xp_static_preflight_passed_contract_execution_unauthorized",
        next_action="complete_r0_then_validate_capture_and_registry" if running else "wait_for_capture_completion_and_reconcile_registry",
    )
    time.sleep(300)
