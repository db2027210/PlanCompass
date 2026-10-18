#!/usr/bin/env python3
"""Launch a fresh four-scene R0 in an independent OS session."""

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
R0 = ROOT / "scripts/run_mysql_four_scene_r0_v5.py"
HISTORY = ROOT / "history/mysql_four_scene_20260809"
MANAGEMENT_CPUSET = "240-241,496-497"


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")

    output = HISTORY / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh R0 output exists: {output}")
    active = subprocess.run(
        ["pgrep", "-af", "scripts/run_mysql_four_scene_r0_v5.py"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    active_lines = [line for line in active.stdout.splitlines() if line.strip()]
    if active_lines:
        raise RuntimeError(f"another R0 is already active: {active_lines}")

    log_root = HISTORY / "r0_launch_logs"
    receipt_root = HISTORY / "r0_launch_receipts"
    log_root.mkdir(parents=True, exist_ok=True)
    receipt_root.mkdir(parents=True, exist_ok=True)
    stdout_path = log_root / f"{args.run_id}.stdout.log"
    stderr_path = log_root / f"{args.run_id}.stderr.log"
    receipt_path = receipt_root / f"{args.run_id}.json"
    if any(path.exists() for path in (stdout_path, stderr_path, receipt_path)):
        raise RuntimeError("fresh R0 launch artifact already exists")

    command = [
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(R0), "--run-id", args.run_id,
    ]
    with stdout_path.open("ab", buffering=0) as stdout, stderr_path.open(
        "ab", buffering=0
    ) as stderr:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            close_fds=True,
        )

    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"R0 exited during detached startup: rc={process.returncode}; "
                f"stderr={stderr_path}"
            )
        if output.is_dir() and (output / "resource_samples.jsonl").is_file():
            break
        time.sleep(0.25)
    else:
        process.terminate()
        raise RuntimeError("R0 did not publish initial resource samples within 60 seconds")

    receipt = {
        "schema_version": "mysql_four_scene_detached_r0_launch_receipt_v1",
        "status": "STARTED",
        "verified_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "run_id": args.run_id,
        "pid": process.pid,
        "pid_alive_at_verification": process.poll() is None,
        "independent_os_session": True,
        "management_cpuset": MANAGEMENT_CPUSET,
        "r0_source_path": str(R0),
        "r0_source_sha256": sha(R0),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "result_path": str(output / "r0_binding.json"),
    }
    atomic(receipt_path, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
