#!/usr/bin/env python3
"""Recover stopped CPU-A-slot XP containers and relaunch their C scenes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HISTORY = ROOT / "history/mysql_four_scene_20260809"
AUTHORITY = HISTORY / "v870_to_c_four_scene_cpu_a_slots_live_authority_20260828"
PREPARED = AUTHORITY / "prepared_binding.json"
CHECKPOINT = HISTORY / "v868_to_c_cpu_a_slots_fresh_checkpoint_20260828/checkpoint/prepared_binding.json"
QUARANTINE = HISTORY / "v868_to_c_cpu_a_slots_fresh_checkpoint_20260828/replay_quarantine/replay_quarantine.json"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_async_v75.py"
OUTPUT = HISTORY / "v885_to_c_xp_stopped_slots_recovered_20260828"
TARGET_IDS = ["xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"]


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def inspect(name: str, maximum_seconds: float = 600.0) -> dict[str, Any]:
    deadline = time.monotonic() + maximum_seconds
    last = ""
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["docker", "inspect", name], text=True, capture_output=True,
                check=False, timeout=120,
            )
        except subprocess.TimeoutExpired:
            time.sleep(2)
            continue
        last = result.stderr.strip()
        if result.returncode == 0:
            values = json.loads(result.stdout)
            if isinstance(values, list) and len(values) == 1:
                return dict(values[0])
        time.sleep(2)
    raise TimeoutError(f"docker inspect {name} unavailable: {last}")


def start(row: Mapping[str, Any]) -> dict[str, Any]:
    name = str(row["container"])
    expected_id = str(row["container_id"])
    before = inspect(name)
    if before.get("Id") != expected_id:
        raise RuntimeError(f"{row['worker_id']} stopped identity differs")
    if (before.get("State") or {}).get("Running") is not True:
        try:
            subprocess.run(
                ["docker", "start", name], text=True, capture_output=True,
                check=True, timeout=600,
            )
        except subprocess.TimeoutExpired:
            pass
    after = inspect(name)
    if after.get("Id") != expected_id or (after.get("State") or {}).get("Running") is not True:
        raise RuntimeError(f"{row['worker_id']} did not recover running identity")
    deadline = time.monotonic() + 360
    defaults = str(row["credential_defaults"])
    while time.monotonic() < deadline:
        probe = subprocess.run([
            "mysql", f"--defaults-file={defaults}", "--host=127.0.0.1",
            f"--port={row['host_port']}", "--connect-timeout=5", "-NBe", "SELECT 1",
        ], cwd=ROOT, text=True, capture_output=True, check=False, timeout=20)
        if probe.returncode == 0 and probe.stdout.strip() == "1":
            return {"worker_id": row["worker_id"], "container": name,
                    "container_id": expected_id, "pid": (after.get("State") or {}).get("Pid")}
        time.sleep(3)
    raise TimeoutError(f"{row['worker_id']} MySQL readiness timed out")


def launch(run_id: str, scene: str) -> dict[str, Any]:
    command = [
        "/home/anonymous/miniconda3/envs/py313/bin/python", str(LAUNCHER),
        "--run-id", run_id, "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(AUTHORITY / "transition_preflight.json"),
        "--prepared-binding", str(PREPARED),
        "--benchmark-accounts", str(AUTHORITY / "benchmark_accounts_binding.json"),
        "--fresh-r0", str(AUTHORITY / "fresh_r0_binding.json"),
        "--replay-quarantine", str(QUARANTINE), "--fresh-search",
        "--startup-timeout-seconds", "300", "--scene", scene,
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=600)
    return {"scene": scene, "run_id": run_id, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh output required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    rows = {str(row["worker_id"]): dict(row) for row in load(PREPARED).get("workers") or []}
    recovered = []
    for worker_id in TARGET_IDS:
        recovered.append(start(rows[worker_id]))
        print(json.dumps({"stage": "RUNNING", **recovered[-1]}), flush=True)
    launches = [
        launch("v886_to_c_cpu_after_stopped_slot_recovery_20260828", "hybench_xp_cpu"),
        launch("v887_to_c_xpio_after_stopped_slot_recovery_20260828", "hybench_xp_io"),
    ]
    document = {"created_at": now(), "status": "LAUNCHED" if all(row["returncode"] == 0 for row in launches) else "FAILED_CLOSED",
                "recovered": recovered, "launches": launches,
                "external_process_control_or_stop_performed": False}
    (OUTPUT / "recovery_and_launch.json").write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(document, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if document["status"] == "LAUNCHED" else 4


if __name__ == "__main__":
    raise SystemExit(main())
