#!/usr/bin/env python3
"""Launch CPU-A v7 after its unlocked eight-lane authority recovery."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_mysql_algorithm_a_xp_cpu_round2_wave1_committed_resume_v2.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file() or sidecar.read_text(encoding="ascii").split()[0] != sha(path):
        raise RuntimeError(f"authority sidecar differs: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery-unit", required=True)
    parser.add_argument("--authority-root", required=True)
    parser.add_argument("--pipeline-root", required=True)
    parser.add_argument("--a-run-id", required=True)
    parser.add_argument("--a-output", required=True)
    parser.add_argument("--a-unit", required=True)
    parser.add_argument("--maximum-wait-seconds", type=int, default=3600)
    args = parser.parse_args()
    authority_root = Path(args.authority_root).resolve()
    authority_path = authority_root / "live_authority.json"
    pipeline = Path(args.pipeline_root).resolve()
    if pipeline.exists() or pipeline.is_symlink():
        raise RuntimeError(f"fresh pipeline root required: {pipeline}")
    pipeline.mkdir(parents=True, mode=0o700)
    started = time.monotonic()
    while not authority_path.is_file():
        if time.monotonic() - started >= args.maximum_wait_seconds:
            raise RuntimeError("CPU authority recovery timed out")
        status = subprocess.run(
            ["systemctl", "--user", "is-active", args.recovery_unit],
            capture_output=True, text=True, check=False, timeout=30,
        ).stdout.strip()
        if status in {"failed", "inactive"}:
            raise RuntimeError(f"CPU authority recovery ended: {status}")
        time.sleep(5)
    authority = load(authority_path)
    if (
        authority.get("status") != "PASS"
        or authority.get("scene") != "hybench_xp_cpu"
        or int(authority.get("physical_replay_parallelism") or 0) != 8
    ):
        raise RuntimeError("CPU unlocked live authority differs")
    command = [
        "systemd-run", "--user", f"--unit={args.a_unit}",
        "--description=MySQL XP8 CPU Algorithm A v7 Round2 Wave1 continuation",
        "--property=WorkingDirectory=" + str(ROOT),
        "--property=KillMode=control-group", "--property=TimeoutStopSec=120",
        "/usr/bin/taskset", "-c", "488-489,498-499",
        "/home/anonymous/miniconda3/envs/py313/bin/python", str(SCRIPT),
        "--run-id", args.a_run_id, "--scene", "hybench_xp_cpu",
        "--launch-authority", str(authority_path),
        "--output-dir", str(Path(args.a_output).resolve()),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"CPU-A launch failed: {completed.stderr[-1000:]}")
    time.sleep(3)
    status = subprocess.run(
        ["systemctl", "--user", "is-active", args.a_unit],
        capture_output=True, text=True, check=False, timeout=30,
    ).stdout.strip()
    if status != "active":
        raise RuntimeError(f"CPU-A did not remain active: {status}")
    atomic(pipeline / "recovery_handoff.json", {
        "schema_version": "mysql_xp_cpu_unlocked_recovery_then_a_v1",
        "status": "PASS", "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "authority_path": str(authority_path), "authority_sha256": sha(authority_path),
        "a_run_id": args.a_run_id, "a_unit": args.a_unit,
        "committed_round2_wave1_reused": True,
        "cross_scene_docker_control_lock": False, "worker_reset_parallelism": 8,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

