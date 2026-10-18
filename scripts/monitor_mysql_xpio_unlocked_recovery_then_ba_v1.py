#!/usr/bin/env python3
"""Launch IO-B v8 and its A-v7 handoff after unlocked authority recovery."""

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
HISTORY = ROOT / "history/mysql_four_scene_20260809"
B_SCRIPT = ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_eight_worker_io_outer11_resume_v1.py"
BA_MONITOR = ROOT / "scripts/monitor_mysql_b_then_a_mixed_eight_worker_scene_v8.py"
COMPOSED_SOURCE = HISTORY / "v593_to_b_io_outer11_unlocked_continuation_source_20260827"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
    sidecar = path.with_name(path.name + ".sha256")
    if (
        not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii").split()[0] != sha(path)
    ):
        raise RuntimeError(f"JSON sidecar differs: {path}")
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


def run(argv: list[str]) -> None:
    completed = subprocess.run(
        argv, cwd=ROOT, capture_output=True, text=True, check=False, timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {argv!r}: "
            f"{completed.stderr[-1000:]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery-unit", required=True)
    parser.add_argument("--authority-root", required=True)
    parser.add_argument("--pipeline-root", required=True)
    parser.add_argument("--b-run-id", required=True)
    parser.add_argument("--b-unit", required=True)
    parser.add_argument("--a-run-id", required=True)
    parser.add_argument("--a-unit", required=True)
    parser.add_argument("--a-authority-root", required=True)
    parser.add_argument("--ba-unit", required=True)
    parser.add_argument("--maximum-wait-seconds", type=int, default=3600)
    args = parser.parse_args()
    authority_root = Path(args.authority_root).resolve()
    pipeline_root = Path(args.pipeline_root).resolve()
    if pipeline_root.exists() or pipeline_root.is_symlink():
        raise RuntimeError(f"fresh pipeline root required: {pipeline_root}")
    pipeline_root.mkdir(parents=True, mode=0o700)
    started = time.monotonic()
    authority_path = authority_root / "live_authority.json"
    while not authority_path.is_file():
        if time.monotonic() - started >= args.maximum_wait_seconds:
            raise RuntimeError("unlocked XP/IO authority recovery timed out")
        status = subprocess.run(
            ["systemctl", "--user", "is-active", args.recovery_unit],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
        ).stdout.strip()
        if status in {"failed", "inactive"}:
            raise RuntimeError(f"unlocked XP/IO authority service ended: {status}")
        time.sleep(5)
    authority = load(authority_path)
    if (
        authority.get("status") != "PASS"
        or authority.get("scene") != "hybench_xp_io"
        or int(authority.get("physical_replay_parallelism") or 0) != 8
    ):
        raise RuntimeError("unlocked XP/IO live authority differs")
    b_root = HISTORY / args.b_run_id
    run([
        "systemd-run", "--user", f"--unit={args.b_unit}",
        "--description=MySQL Algorithm B IO XP8 Outer11 unlocked resume",
        "--property=WorkingDirectory=" + str(ROOT),
        "--property=KillMode=control-group", "--property=TimeoutStopSec=120",
        "--setenv=MYSQL_MIXED_FOUR_WORKER_AUTHORITY_ROOT=" + str(authority_root),
        "/usr/bin/taskset", "-c", "492-493,504-505",
        "/home/anonymous/miniconda3/envs/py313/bin/python", str(B_SCRIPT),
        "--run-id", args.b_run_id, "--resume-from", str(COMPOSED_SOURCE),
        "--scene", "hybench_xp_io",
    ])
    time.sleep(3)
    b_status = subprocess.run(
        ["systemctl", "--user", "is-active", args.b_unit],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=30,
    ).stdout.strip()
    if b_status != "active":
        raise RuntimeError(f"unlocked IO-B service did not remain active: {b_status}")
    run([
        "systemd-run", "--user", f"--unit={args.ba_unit}",
        "--description=MySQL XP8 IO B-v8 to A-v7 handoff monitor",
        "--property=WorkingDirectory=" + str(ROOT),
        "--property=KillMode=control-group", "--property=TimeoutStopSec=60",
        "/usr/bin/taskset", "-c", "494-495",
        "/home/anonymous/miniconda3/envs/py313/bin/python", str(BA_MONITOR),
        "--scene", "hybench_xp_io", "--b-state", str(b_root / "run_state.json"),
        "--a-run-id", args.a_run_id,
        "--pipeline-root", str(pipeline_root / "b_to_a"),
        "--authority-root", str(Path(args.a_authority_root).resolve()),
        "--a-unit", args.a_unit,
    ])
    atomic(pipeline_root / "recovery_handoff.json", {
        "schema_version": "mysql_xpio_unlocked_recovery_then_ba_v1",
        "status": "PASS",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "authority_path": str(authority_path),
        "authority_sha256": sha(authority_path),
        "b_run_id": args.b_run_id,
        "b_unit": args.b_unit,
        "ba_unit": args.ba_unit,
        "a_run_id": args.a_run_id,
        "a_unit": args.a_unit,
        "cross_scene_docker_control_lock": False,
        "worker_reset_parallelism": 8,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

