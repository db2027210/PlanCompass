#!/usr/bin/env python3
"""Launch a same-cohort, two-worker Algorithm A JOB boundary resume."""

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
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_same_cohort_codefix_resume_v1.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_async_20260822_v4"
    / "algorithm_a_contract.json"
)
LOG_ROOT = ROOT / "history/mysql_four_scene_20260809/formal_launch_logs"
MANAGEMENT_CPUSET = "240-241,496-497"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
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
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def unit_fields(unit: str) -> dict[str, str]:
    shown = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState",
         "-p", "SubState", "-p", "MainPID", "-p", "Result",
         "-p", "ExecMainStatus"],
        cwd=ROOT, text=True, capture_output=True, check=True, timeout=15,
    ).stdout
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--predecessor-state", type=regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=regular_file, required=True)
    parser.add_argument("--prepared-binding", type=regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=regular_file, required=True)
    parser.add_argument("--fresh-r0", type=regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--startup-timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.run_id) is None:
        parser.error("unsafe JOB resume run id")
    if not args.run_id.endswith("_job"):
        parser.error("JOB resume run id must end with _job")
    predecessor = load(args.predecessor_state)
    checkpoint = predecessor.get("round_checkpoint")
    if (
        predecessor.get("algorithm") != "A"
        or predecessor.get("async_scene") != "job_memory"
        or predecessor.get("formal_scope") != "mysql_only"
        or predecessor.get("cross_engine_comparison_prohibited") is not True
        or int(predecessor.get("accepted") or 0) >= 10
        or not isinstance(checkpoint, Mapping)
        or int(checkpoint.get("round_index") or 0)
        != int(predecessor.get("round_index") or 0)
    ):
        raise RuntimeError("JOB predecessor is not an incomplete two-worker round")
    output = args.output_dir.expanduser().resolve()
    unit = f"mysql-alga-job2-resume-{args.run_id}.service"
    stdout = LOG_ROOT / f"{args.run_id}.stdout.log"
    stderr = LOG_ROOT / f"{args.run_id}.stderr.log"
    manifest = LOG_ROOT / f"{args.run_id}.launch_manifest.json"
    if output.exists() or output.is_symlink() or any(
        path.exists() or path.is_symlink() for path in (stdout, stderr, manifest)
    ):
        raise RuntimeError("fresh JOB resume artifacts required")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        "--description=MySQL Algorithm A JOB same-cohort two-worker resume",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER), "--run-id", args.run_id,
        "--scene", "job_memory", "--predecessor-state", str(args.predecessor_state),
        "--runtime-preflight", str(args.runtime_preflight),
        "--prepared-binding", str(args.prepared_binding),
        "--benchmark-accounts", str(args.benchmark_accounts),
        "--fresh-r0", str(args.fresh_r0), "--output-dir", str(output),
    ]
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    started = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )
    if started.returncode != 0:
        raise RuntimeError(f"JOB resume launch failed: {started.stderr[-1000:]}")
    deadline = time.monotonic() + args.startup_timeout_seconds
    state: dict[str, Any] = {}
    fields: dict[str, str] = {}
    while time.monotonic() < deadline:
        fields = unit_fields(unit)
        if fields.get("ActiveState") in {"inactive", "failed"}:
            raise RuntimeError(f"JOB resume exited during startup: {fields}; {stderr}")
        state_path = output / "algorithm_a_state.json"
        if state_path.is_file():
            state = load(state_path)
            if (
                state.get("status") == "RUNNING"
                and state.get("async_scene") == "job_memory"
                and state.get("physical_replay_parallelism") == 2
                and state.get("four_scene_wave_barrier") is False
            ):
                break
        time.sleep(0.5)
    else:
        subprocess.run(["systemctl", "--user", "stop", unit], cwd=ROOT,
                       text=True, capture_output=True, check=False, timeout=30)
        raise RuntimeError("JOB two-worker resume startup timed out")
    atomic(manifest, {
        "schema_version": "mysql_algorithm_a_job_two_worker_resume_launch_v1",
        "status": "LAUNCHED", "launched_at": datetime.now(timezone.utc).isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "algorithm": "A", "scene": "job_memory", "run_id": args.run_id,
        "unit": unit, "main_pid": int(fields.get("MainPID") or 0),
        "output": str(output), "state": str(output / "algorithm_a_state.json"),
        "stage": state.get("stage"), "accepted": state.get("accepted"),
        "completed_round_index": int(predecessor["round_index"]) - 1,
        "interrupted_round_credited": False, "physical_replay_parallelism": 2,
        "predecessor_state": str(args.predecessor_state),
        "predecessor_state_sha256": sha(args.predecessor_state),
        "controller": str(CONTROLLER), "controller_sha256": sha(CONTROLLER),
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "runtime_preflight": str(args.runtime_preflight),
        "prepared_binding": str(args.prepared_binding),
        "benchmark_accounts": str(args.benchmark_accounts),
        "fresh_r0": str(args.fresh_r0), "stdout": str(stdout), "stderr": str(stderr),
        "post_convergence_stability_required": False,
        "post_target_physical_replays": 0,
    })
    print(json.dumps({"status": "LAUNCHED", "unit": unit,
                      "output": str(output), "stage": state.get("stage")},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
