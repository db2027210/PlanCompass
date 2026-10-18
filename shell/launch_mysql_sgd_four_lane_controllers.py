#!/usr/bin/env python3
"""Start the four mysql_only Algorithm C lane controllers without bypassing gates.

The controllers are persistent, duplicate-safe watchers.  A controller may
start a campaign only after its lane receives a sealed launch_request.json
whose scope is mysql_only and whose preflight status is PASS.  This launcher
does not create such a request and therefore cannot turn NOT_READY into READY.
"""

from __future__ import annotations

from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_sgd_env_agents_20260723"
SUPERVISOR = HISTORY / "four_lane_supervisor"
CONTROLLER = ROOT / "shell/manual_mysql_sgd_lane_controller.py"
READINESS = ROOT / "shell/verify_mysql_sgd_v4_readiness.py"
GATE_POLICY = ROOT / "config/mysql_sgd_pre_tuning_gate_v1.json"
SCENARIOS = (
    "job_memory",
    "hybench_xp_io",
    "tpch_memory",
    "tpcc_cpu",
)
EXPECTED_CONTRACT_VERSION = "mysql_formal_algorithm_b_launch_v5"


def pid_live(value: Any) -> bool:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return False
    if pid <= 1:
        return False
    return Path(f"/proc/{pid}/stat").is_file()


def controller_pid_live(value: Any, scenario: str) -> bool:
    if not pid_live(value):
        return False
    try:
        command = Path(f"/proc/{int(value)}/cmdline").read_bytes().replace(b"\0", b" ")
    except OSError:
        return False
    return (
        b"manual_mysql_sgd_lane_controller.py" in command
        and f"--scenario {scenario}".encode() in command
    )


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def readiness() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(READINESS), "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        value = {}
    if not isinstance(value, dict):
        value = {}
    value["verifier_returncode"] = result.returncode
    return value


def existing_controller(scenario: str) -> tuple[int | None, dict[str, Any]]:
    state_path = HISTORY / "manual_controllers" / scenario / "state.json"
    state = load_json(state_path)
    pid = state.get("controller_pid")
    heartbeat_live = False
    try:
        heartbeat_live = time.time() - state_path.stat().st_mtime <= 90.0
    except OSError:
        pass
    active = controller_pid_live(pid, scenario) or (
        heartbeat_live and isinstance(pid, int) and pid > 1
    )
    return (int(pid) if active else None), state


def stop_outdated_controller(
    scenario: str, pid: int, state: dict[str, Any]
) -> None:
    if (
        state.get("scenario") != scenario
        or state.get("controller_mode") not in {
            "manual_local_no_agent",
            "formal_config_auto_launch",
        }
    ):
        raise RuntimeError(f"refusing to signal unverified controller PID {pid}")
    if state.get("campaign_pid") is not None:
        raise RuntimeError(
            f"cannot replace outdated {scenario} controller while a campaign is active"
        )
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise RuntimeError(f"outdated {scenario} controller PID {pid} did not stop")


def main() -> int:
    if not CONTROLLER.is_file():
        raise SystemExit(f"controller missing: {CONTROLLER}")
    if not GATE_POLICY.is_file() or GATE_POLICY.is_symlink():
        raise SystemExit(f"pre-tuning gate policy missing or unsafe: {GATE_POLICY}")
    SUPERVISOR.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(SUPERVISOR / "launcher.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another four-lane launcher is active")

        gate = readiness()
        rows: list[dict[str, Any]] = []
        launched: list[subprocess.Popen[bytes]] = []
        for scenario in SCENARIOS:
            pid, state = existing_controller(scenario)
            action = "active_skip"
            if (
                pid is not None
                and state.get("controller_contract_version")
                != EXPECTED_CONTRACT_VERSION
            ):
                stop_outdated_controller(scenario, pid, state)
                pid = None
                action = "replaced_outdated_controller"
            if pid is None:
                lane = HISTORY / "manual_controllers" / scenario
                lane.mkdir(parents=True, exist_ok=True)
                stdout = (lane / "controller.stdout.log").open("ab", buffering=0)
                stderr = (lane / "controller.stderr.log").open("ab", buffering=0)
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(CONTROLLER),
                        "--scenario",
                        scenario,
                        "--interval-seconds",
                        "30",
                    ],
                    cwd=ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                    close_fds=True,
                )
                launched.append(process)
                pid = process.pid
                if action != "replaced_outdated_controller":
                    action = "launched_controller"
            rows.append(
                {
                    "scenario": scenario,
                    "controller_pid": pid,
                    "action": action,
                }
            )

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if all(
                load_json(
                    HISTORY
                    / "manual_controllers"
                    / str(row["scenario"])
                    / "state.json"
                ).get("controller_contract_version") == EXPECTED_CONTRACT_VERSION
                for row in rows
            ):
                break
            time.sleep(0.1)
        for row in rows:
            state_path = (
                HISTORY / "manual_controllers" / str(row["scenario"]) / "state.json"
            )
            heartbeat_live = False
            try:
                heartbeat_live = time.time() - state_path.stat().st_mtime <= 90.0
            except OSError:
                pass
            row["controller_live"] = heartbeat_live or controller_pid_live(
                row["controller_pid"], str(row["scenario"])
            )
            state = load_json(state_path)
            row["controller_contract_version"] = state.get(
                "controller_contract_version"
            )
            row["hard_gate_active"] = (
                row["controller_live"]
                and row["controller_contract_version"] == EXPECTED_CONTRACT_VERSION
            )

        manifest = {
            "schema_version": "mysql_four_lane_controller_launch_v1",
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "paired_result_merge_prohibited": True,
            "controller_only_until_sealed_request": True,
            "required_controller_contract_version": EXPECTED_CONTRACT_VERSION,
            "gate_policy": {
                "path": str(GATE_POLICY.relative_to(ROOT)),
                "sha256": sha256(GATE_POLICY),
            },
            "readiness": {
                "status": gate.get("status"),
                "ready": gate.get("ready", False),
                "passed": gate.get("passed"),
                "failed": gate.get("failed"),
                "launch_authorized": False,
            },
            "lanes": rows,
        }
        manifest_path = SUPERVISOR / "latest_launch.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        for row in rows:
            print(
                f"{row['scenario']}: controller_pid={row['controller_pid']} "
                f"live={row['controller_live']} hard_gate={row['hard_gate_active']} "
                f"action={row['action']}"
            )
        print(
            f"readiness={gate.get('status', 'VERIFY_ERROR')} "
            f"ready={gate.get('ready', False)} formal_launch_started=false"
        )
        print(f"manifest={manifest_path}")
        return 0 if all(row["hard_gate_active"] for row in rows) else 1
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
