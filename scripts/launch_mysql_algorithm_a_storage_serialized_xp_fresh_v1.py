#!/usr/bin/env python3
"""Launch one fresh two-worker XP A scene after JOB with NVMe serialization."""

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
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_storage_serialized_xp_fresh_v1.py"
LEASE_SOURCE = ROOT / "scripts/mysql_algorithm_a_shared_nvme_wave_lease_v1.py"
SCHEDULE = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_storage_compatible_schedule_20260823_v1"
    / "schedule_contract.json"
)
LOG_ROOT = ROOT / "history/mysql_four_scene_20260809/formal_launch_logs"
MANAGEMENT_CPUSET = "240-241,496-497"
SUFFIX = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise argparse.ArgumentTypeError(f"regular non-symlink file required: {path}")
    return path


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def unit_fields(unit: str) -> dict[str, str]:
    shown = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState",
         "-p", "MainPID", "-p", "Result", "-p", "ExecMainStatus"],
        cwd=ROOT, text=True, capture_output=True, check=True, timeout=15,
    ).stdout
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=sorted(SUFFIX), required=True)
    parser.add_argument("--job-state", type=regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=regular_file, required=True)
    parser.add_argument("--prepared-binding", type=regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=regular_file, required=True)
    parser.add_argument("--fresh-r0", type=regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--startup-timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,140}", args.run_id) is None:
        parser.error("unsafe storage-serialized XP run id")
    if not args.run_id.endswith("_" + SUFFIX[args.scene]):
        parser.error("XP run id suffix differs from scene")
    schedule = load(SCHEDULE)
    job = load(args.job_state)
    if (
        schedule.get("status") != "FROZEN_READY_TO_LAUNCH"
        or schedule.get("formal_scope") != "mysql_only"
        or schedule.get("cross_engine_comparison_prohibited") is not True
        or job.get("algorithm") != "A"
        or job.get("async_scene") != "job_memory"
        or job.get("status") != "COMPLETE"
        or job.get("stage") != "COMPLETE"
        or job.get("converged") is not True
        or int(job.get("accepted") or 0) != 10
    ):
        raise RuntimeError("storage-compatible XP launch requires completed JOB A")
    output = args.output_dir.expanduser().resolve()
    unit = f"mysql-alga-storage-xp-{args.run_id}.service"
    stdout = LOG_ROOT / f"{args.run_id}.stdout.log"
    stderr = LOG_ROOT / f"{args.run_id}.stderr.log"
    manifest = LOG_ROOT / f"{args.run_id}.launch_manifest.json"
    if output.exists() or output.is_symlink() or any(
        path.exists() or path.is_symlink() for path in (stdout, stderr, manifest)
    ):
        raise RuntimeError("fresh storage-serialized XP launch artifacts required")
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm A fresh storage-serialized {args.scene}",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER), "--run-id", args.run_id,
        "--scene", args.scene, "--runtime-preflight", str(args.runtime_preflight),
        "--prepared-binding", str(args.prepared_binding),
        "--benchmark-accounts", str(args.benchmark_accounts),
        "--fresh-r0", str(args.fresh_r0), "--output-dir", str(output),
    ]
    started = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )
    if started.returncode != 0:
        raise RuntimeError(f"storage-serialized XP launch failed: {started.stderr[-1000:]}")
    deadline = time.monotonic() + args.startup_timeout_seconds
    state: dict[str, Any] = {}
    fields: dict[str, str] = {}
    while time.monotonic() < deadline:
        fields = unit_fields(unit)
        if fields.get("ActiveState") in {"inactive", "failed"}:
            raise RuntimeError(f"storage-serialized XP exited during startup: {fields}")
        state_path = output / "algorithm_a_state.json"
        if state_path.is_file():
            state = load(state_path)
            if (
                state.get("status") == "RUNNING"
                and state.get("async_scene") == args.scene
                and int(state.get("physical_replay_parallelism") or 0) == 2
                and int(state.get("accepted") or 0) == 0
            ):
                break
        time.sleep(0.5)
    else:
        subprocess.run(
            ["systemctl", "--user", "stop", unit], cwd=ROOT,
            text=True, capture_output=True, check=False, timeout=30,
        )
        raise RuntimeError("storage-serialized XP startup timed out")
    atomic(manifest, {
        "schema_version": "mysql_algorithm_a_storage_serialized_xp_launch_v1",
        "status": "LAUNCHED",
        "launched_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A", "scene": args.scene, "run_id": args.run_id,
        "unit": unit, "main_pid": int(fields.get("MainPID") or 0),
        "output": str(output), "state": str(output / "algorithm_a_state.json"),
        "stage": state.get("stage"), "physical_replay_parallelism": 2,
        "historical_accepted_rounds_reused": False,
        "fresh_baseline_required": True,
        "shared_storage_measurements_mutually_exclusive": True,
        "shared_storage_lock": "/tmp/mysql_algorithm_a_nvme2_xp_formal_wave_v1.lock",
        "controller_path": str(CONTROLLER), "controller_sha256": sha(CONTROLLER),
        "lease_source_path": str(LEASE_SOURCE), "lease_source_sha256": sha(LEASE_SOURCE),
        "schedule_contract_path": str(SCHEDULE), "schedule_contract_sha256": sha(SCHEDULE),
        "job_state": str(args.job_state), "job_state_sha256": sha(args.job_state),
        "post_convergence_stability_required": False
    })
    print(json.dumps({"status": "LAUNCHED", "unit": unit,
                      "output": str(output), "stage": state.get("stage")},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
