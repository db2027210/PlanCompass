#!/usr/bin/env python3
"""Start the v2 four-scene Algorithm C controller and verify startup once."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTROLLER = ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py"
R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v26_20260811T144000p0800/r0_binding.json"
)
MANAGEMENT_CPUSET = "240-241,496-497"
SCENES = ["job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n", encoding="ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh formal output exists: {output}")
    r0 = json.loads(R0.read_text(encoding="utf-8"))
    if (
        r0.get("status") != "PASS"
        or r0.get("fresh_r0_complete") is not True
        or r0.get("formal_outer_launch_authorized") is not True
        or any((r0.get("accepted_rounds") or {}).values())
    ):
        raise RuntimeError("fresh R0 does not authorize formal Algorithm C")
    log_root = ROOT / "history/mysql_four_scene_20260809/formal_launch_logs"
    log_root.mkdir(parents=True, exist_ok=True)
    stdout_path = log_root / f"{args.run_id}.stdout.log"
    stderr_path = log_root / f"{args.run_id}.stderr.log"
    unit = f"mysql-four-scene-algc-{args.run_id}.service"
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        "--description=MySQL four-scene formal Algorithm C",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout_path}",
        f"--property=StandardError=append:{stderr_path}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
        str(CONTROLLER), "--run-id", args.run_id,
    ]
    started = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )
    if started.returncode != 0:
        raise RuntimeError(f"systemd formal launch failed: {started.stderr[-1000:]}")
    deadline = time.monotonic() + 20.0
    state_path = output / "run_state.json"
    state = None
    main_pid = 0
    while time.monotonic() < deadline:
        shown = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "ActiveState",
             "-p", "SubState", "-p", "MainPID", "-p", "Result"],
            text=True, capture_output=True, check=True, timeout=10,
        ).stdout
        fields = dict(
            line.split("=", 1) for line in shown.splitlines() if "=" in line
        )
        main_pid = int(fields.get("MainPID", "0"))
        if fields.get("ActiveState") == "failed" or (
            fields.get("ActiveState") == "inactive" and main_pid == 0
        ):
            raise RuntimeError(
                f"formal controller exited during systemd startup: {fields}; "
                f"stderr={stderr_path}"
            )
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if (
                main_pid > 0
                and state.get("status") == "RUNNING"
                and state.get("active_scenes") == SCENES
            ):
                break
        time.sleep(0.25)
    else:
        subprocess.run(
            ["systemctl", "--user", "stop", unit], check=False, timeout=30,
        )
        raise RuntimeError("formal controller did not publish RUNNING state within 20 seconds")
    receipt = {
        "schema_version": "mysql_four_scene_algorithm_c_launch_receipt_v2",
        "status": "STARTED", "verified_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "active_scenes": SCENES, "pid": main_pid, "pid_alive_at_verification": True,
        "systemd_unit": unit, "systemd_managed": True,
        "management_cpuset": MANAGEMENT_CPUSET,
        "controller_source_path": str(CONTROLLER), "controller_source_sha256": sha(CONTROLLER),
        "r0_path": str(R0), "r0_sha256": sha(R0),
        "run_state_path": str(state_path), "run_state_sha256_at_verification": sha(state_path),
        "stage_at_verification": state.get("stage"),
        "stdout_log": str(stdout_path), "stderr_log": str(stderr_path),
        "monitoring_started": False,
    }
    atomic(output / "launch_receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
