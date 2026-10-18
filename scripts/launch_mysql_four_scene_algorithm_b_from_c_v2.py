#!/usr/bin/env python3
"""Launch Algorithm B with the current watchdog and affinity observer."""

from __future__ import annotations

import argparse
import contextlib
from io import StringIO
import json
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importing the v2 release module installs its source bindings in the shared
# v1 verifier used by the predecessor launcher.
from scripts import build_mysql_algorithm_b_from_algorithm_c_release_v2 as release_v2
from scripts import launch_mysql_four_scene_algorithm_b_from_c_v1 as predecessor


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
WATCHDOG = ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v11.py"
AFFINITY_GUARD = ROOT / "scripts/observe_frozen_external_pg_client_affinity_v1.py"
MANAGEMENT_CPUSET = "240-241,496-497"


def unit_fields(unit: str, *names: str) -> dict[str, str]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            command = ["systemctl", "--user", "show", unit]
            for name in names:
                command.extend(["-p", name])
            shown = subprocess.run(
                command, text=True, capture_output=True, check=True, timeout=10,
            ).stdout
            fields = dict(
                line.split("=", 1) for line in shown.splitlines() if "=" in line
            )
            if fields:
                return fields
            raise RuntimeError("empty systemd readback")
        except (subprocess.SubprocessError, RuntimeError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
    raise RuntimeError(f"systemd readback failed after retries: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--run-id", required=True)
    known, _ = parser.parse_known_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", known.run_id) is None:
        parser.error("unsafe run-id")
    if not WATCHDOG.is_file() or not AFFINITY_GUARD.is_file():
        raise RuntimeError("current Algorithm B runtime guard source absent")

    formal_unit = f"mysql-four-scene-algb-{known.run_id}.service"
    guard_unit = f"mysql-four-scene-algb-affinity-{known.run_id}.service"
    log_root = ROOT / "history/mysql_four_scene_20260809/formal_launch_logs"
    log_root.mkdir(parents=True, exist_ok=True)
    guard_log = log_root / f"{known.run_id}.external_pg_affinity.jsonl"
    guard_stdout = log_root / f"{known.run_id}.affinity.stdout.log"
    guard_stderr = log_root / f"{known.run_id}.affinity.stderr.log"
    started = subprocess.run([
        "systemd-run", "--user", f"--unit={guard_unit.removesuffix('.service')}",
        "--description=MySQL Algorithm B external PG affinity observer",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{guard_stdout}",
        f"--property=StandardError=append:{guard_stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
        str(AFFINITY_GUARD), "--formal-unit", formal_unit,
        "--log", str(guard_log), "--interval-seconds", "0.25",
        "--startup-grace-seconds", "60",
    ], cwd=ROOT, text=True, capture_output=True, check=False, timeout=30)
    if started.returncode != 0:
        raise RuntimeError("Algorithm B affinity observer launch failed: " + started.stderr[-1000:])

    guard_pid = 0
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        fields = unit_fields(guard_unit, "ActiveState", "SubState", "MainPID")
        guard_pid = int(fields.get("MainPID", "0"))
        if fields.get("ActiveState") == "active" and guard_pid > 0:
            break
        if fields.get("ActiveState") in {"failed", "inactive"}:
            raise RuntimeError(f"Algorithm B affinity observer exited: {fields}")
        time.sleep(0.25)
    else:
        predecessor.stop(guard_unit)
        raise RuntimeError("Algorithm B affinity observer did not become active")

    predecessor.WATCHDOG = WATCHDOG
    predecessor.unit_fields = unit_fields
    captured = StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            result = predecessor.main()
    except Exception:
        predecessor.stop(guard_unit)
        raise
    if result != 0:
        predecessor.stop(guard_unit)
        return result

    output = ROOT / "history/mysql_four_scene_20260809" / known.run_id
    receipt_path = output / "launch_receipt.json"
    receipt = predecessor.load(receipt_path)
    fields = unit_fields(guard_unit, "ActiveState", "SubState", "MainPID")
    if (
        fields.get("ActiveState") != "active"
        or int(fields.get("MainPID", "0")) != guard_pid
    ):
        predecessor.stop(formal_unit)
        raise RuntimeError("Algorithm B affinity observer lost after controller start")
    receipt.update({
        "schema_version": "mysql_four_scene_algorithm_b_launch_receipt_v2",
        "runtime_release": "current_v27_executor_v11_isolation",
        "runtime_watchdog_source": {
            "path": str(WATCHDOG), "sha256": predecessor.sha(WATCHDOG),
        },
        "external_affinity_guard_launched": True,
        "external_pg_affinity_guard_unit": guard_unit,
        "external_pg_affinity_guard_pid": guard_pid,
        "external_pg_affinity_guard_log": str(guard_log),
        "external_pg_affinity_guard_source": {
            "path": str(AFFINITY_GUARD),
            "sha256": predecessor.sha(AFFINITY_GUARD),
        },
    })
    predecessor.atomic(receipt_path, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
