#!/usr/bin/env python3
"""Start and verify the sealed outer-1 Algorithm C checkpoint resume."""

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
CONTROLLER = ROOT / "scripts/run_mysql_four_scene_algorithm_c_resume_v1.py"
CHECKPOINT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "algorithm_c_outer1_checkpoint_v1_20260811T184000p0800"
    / "prepared_binding.json"
)
RUNTIME_PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v31.json"
)
PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v29_20260812T005100p0800"
    / "prepared_binding.json"
)
BENCHMARK_ACCOUNTS = (
    ROOT / "history/mysql_four_scene_20260809"
    / "benchmark_accounts_v32_20260812T005200p0800"
    / "benchmark_accounts_binding.json"
)
MANAGEMENT_CPUSET = "240-241,496-497"
SCENES = ["job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh formal output exists: {output}")
    if not PYTHON.is_file():
        raise RuntimeError("resume Python interpreter is absent")
    if not all(
        path.is_file() and not path.is_symlink()
        for path in (
            CONTROLLER, CHECKPOINT, RUNTIME_PREFLIGHT,
            PREPARED, BENCHMARK_ACCOUNTS,
        )
    ):
        raise RuntimeError("resume evidence is absent or is a symlink")
    checkpoint = load(CHECKPOINT)
    preflight = load(RUNTIME_PREFLIGHT)
    prepared = load(PREPARED)
    accounts = load(BENCHMARK_ACCOUNTS)
    if (
        checkpoint.get("status") != "PREPARED"
        or checkpoint.get("completed_outer_attempts") != 1
        or preflight.get("status") != "PASS"
        or float(preflight.get("observed_seconds", 0)) < 60.0
        or preflight.get("formal_launch_authorized") is not True
        or prepared.get("status") != "PREPARED"
        or prepared.get("preflight_sha256") != sha(RUNTIME_PREFLIGHT)
        or prepared.get("xp_reset_prepared_sha256") != sha(CHECKPOINT)
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != sha(PREPARED)
        or accounts.get("passwords_emitted") is not False
        or len(accounts.get("workers") or []) != 8
    ):
        raise RuntimeError("resume launch authority differs")

    log_root = ROOT / "history/mysql_four_scene_20260809/formal_launch_logs"
    log_root.mkdir(parents=True, exist_ok=True)
    stdout_path = log_root / f"{args.run_id}.stdout.log"
    stderr_path = log_root / f"{args.run_id}.stderr.log"
    unit = f"mysql-four-scene-algc-{args.run_id}.service"
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        "--description=MySQL four-scene formal Algorithm C outer-1 resume",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout_path}",
        f"--property=StandardError=append:{stderr_path}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
        str(CONTROLLER), "--run-id", args.run_id,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(RUNTIME_PREFLIGHT),
        "--prepared-binding", str(PREPARED),
        "--benchmark-accounts", str(BENCHMARK_ACCOUNTS),
    ]
    started = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True,
        check=False, timeout=30,
    )
    if started.returncode != 0:
        raise RuntimeError(f"systemd resume launch failed: {started.stderr[-1000:]}")

    deadline = time.monotonic() + 30.0
    state_path = output / "run_state.json"
    state = None
    main_pid = 0
    fields: dict[str, str] = {}
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
                f"resume controller exited during startup: {fields}; "
                f"stderr={stderr_path}"
            )
        if state_path.is_file():
            state = load(state_path)
            if (
                main_pid > 0
                and state.get("status") == "RUNNING"
                and state.get("active_scenes") == SCENES
                and state.get("completed_predecessor_outer_attempts") == 1
            ):
                break
        time.sleep(0.25)
    else:
        subprocess.run(
            ["systemctl", "--user", "stop", unit], check=False, timeout=30
        )
        raise RuntimeError("resume controller did not publish RUNNING state in 30s")

    receipt = {
        "schema_version": "mysql_four_scene_algorithm_c_resume_launch_receipt_v1",
        "status": "STARTED",
        "verified_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "active_scenes": SCENES,
        "pid": main_pid,
        "pid_alive_at_verification": True,
        "systemd_unit": unit,
        "systemd_managed": True,
        "management_cpuset": MANAGEMENT_CPUSET,
        "controller_source_path": str(CONTROLLER),
        "controller_source_sha256": sha(CONTROLLER),
        "checkpoint_path": str(CHECKPOINT),
        "checkpoint_sha256": sha(CHECKPOINT),
        "runtime_preflight_path": str(RUNTIME_PREFLIGHT),
        "runtime_preflight_sha256": sha(RUNTIME_PREFLIGHT),
        "prepared_binding_path": str(PREPARED),
        "prepared_binding_sha256": sha(PREPARED),
        "benchmark_accounts_path": str(BENCHMARK_ACCOUNTS),
        "benchmark_accounts_sha256": sha(BENCHMARK_ACCOUNTS),
        "run_state_path": str(state_path),
        "run_state_sha256_at_verification": sha(state_path),
        "stage_at_verification": state.get("stage"),
        "accepted_rounds_at_verification": state.get("accepted_rounds"),
        "rejected_outer_attempts_at_verification":
            state.get("rejected_outer_attempts"),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "monitoring_started": False,
    }
    atomic(output / "launch_receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
