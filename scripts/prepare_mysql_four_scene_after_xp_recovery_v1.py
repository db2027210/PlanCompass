#!/usr/bin/env python3
"""Create a fresh formal preflight, live binding, accounts binding, and R0."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination


PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
HISTORY = ROOT / "history/mysql_four_scene_20260809"
PREFLIGHT_ROOT = ROOT / "preflight/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18"
    / "four_scene_effective_resource_successor.json"
)
PREFLIGHT = ROOT / "scripts/preflight_mysql_four_scene_live_v18_recovered_xp_v1.py"
REBIND = ROOT / "scripts/rebind_mysql_four_scene_live_v18_recovered_xp_v1.py"
ACCOUNTS = ROOT / "scripts/provision_mysql_four_scene_benchmark_users_v18_formal.py"
R0 = ROOT / "scripts/run_mysql_four_scene_r0_v18_recovered_xp_v1.py"
XP_IDS = {"xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run(command: list[str], env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=ROOT, env=env, text=True, capture_output=True,
        check=False, timeout=timeout,
    )


def run_preflight_in_process(
    *, output: Path, recovered_path: Path, lease_holder_pid: int,
) -> int:
    """Keep the flock owner and its kernel PID in the validating process."""

    os.environ["MYSQL_V18_RECOVERED_XP_BINDING"] = str(recovered_path)
    from scripts import preflight_mysql_four_scene_live_v18_recovered_xp_v1 as release

    prior_argv = sys.argv
    sys.argv = [
        str(PREFLIGHT), "--output", str(output),
        "--window-seconds", "60", "--interval-seconds", "1",
        "--coordinated-workload-lease-holder-pid", str(lease_holder_pid),
        "--coordination-contract", str(release.watchdog.isolation.CONTRACT_PATH),
    ]
    try:
        return int(release.main())
    finally:
        sys.argv = prior_argv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--recovered-xp-binding", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("safe run id required")

    recovered_path = args.recovered_xp_binding.expanduser().resolve()
    recovered = load(recovered_path)
    freeze = load(FREEZE)
    worker_ids = {str(row.get("worker_id")) for row in recovered.get("workers") or []}
    if (
        recovered_path.parent.parent != HISTORY
        or recovered.get("status") != "PASS"
        or recovered.get("formal_scope") != "mysql_only"
        or recovered.get("cross_engine_comparison_prohibited") is not True
        or recovered.get("freeze_sha256") != sha(FREEZE)
        or worker_ids != XP_IDS
        or any(row.get("ready") is not True for row in recovered.get("workers") or [])
    ):
        raise RuntimeError("recovered XP preparation authority differs")

    output = HISTORY / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh preparation output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    state_path = output / "preparation_state.json"
    state: dict[str, Any] = {
        "schema_version": "mysql_four_scene_post_xp_recovery_preparation_v1",
        "status": "STARTING",
        "started_at": now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "recovered_xp_binding": str(recovered_path),
        "recovered_xp_binding_sha256": sha(recovered_path),
        "external_container_mutation": False,
    }
    atomic(state_path, state)
    lease = None
    try:
        lease = coordination.acquire_exclusive_blocking(timeout_seconds=10.0)
        state.update({
            "status": "LEASE_ACQUIRED",
            "coordination_lock_path": str(coordination.LOCK_PATH),
            "lease_acquired_at": lease.acquired_at,
            "lease_wait_seconds": lease.wait_seconds,
        })
        atomic(state_path, state)
        env = dict(
            os.environ,
            MYSQL_V18_RECOVERED_XP_BINDING=str(recovered_path),
        )
        preflight = PREFLIGHT_ROOT / f"{args.run_id}_preflight.json"
        preflight_returncode = run_preflight_in_process(
            output=preflight,
            recovered_path=recovered_path,
            lease_holder_pid=os.getpid(),
        )
        state.update({
            "preflight": str(preflight),
            "preflight_returncode": preflight_returncode,
            "preflight_lease_holder_pid": os.getpid(),
        })
        if preflight_returncode != 0:
            raise RuntimeError("fresh recovered-XP preflight failed closed")

        rebind_id = f"{args.run_id}_rebind"
        env["MYSQL_V18_PASS_PREFLIGHT"] = str(preflight)
        completed = run([
            str(PYTHON), str(REBIND), "--run-id", rebind_id,
        ], env, 300)
        prepared = HISTORY / rebind_id / "prepared_binding.json"
        state.update({
            "prepared": str(prepared),
            "rebind_returncode": completed.returncode,
            "rebind_stdout_tail": completed.stdout[-2000:],
            "rebind_stderr_tail": completed.stderr[-2000:],
        })
        if completed.returncode != 0:
            raise RuntimeError("fresh recovered-XP rebind failed closed")

        accounts_id = f"{args.run_id}_accounts"
        env["MYSQL_V18_FORMAL_PREPARED"] = str(prepared)
        completed = run([
            str(PYTHON), str(ACCOUNTS), "--run-id", accounts_id,
            "--verify-existing",
        ], env, 300)
        accounts = HISTORY / accounts_id / "benchmark_accounts_binding.json"
        state.update({
            "accounts": str(accounts),
            "accounts_returncode": completed.returncode,
            "accounts_stdout_tail": completed.stdout[-2000:],
            "accounts_stderr_tail": completed.stderr[-2000:],
        })
        if completed.returncode != 0:
            raise RuntimeError("fresh recovered-XP account verification failed closed")

        env["MYSQL_V18_FORMAL_ACCOUNTS"] = str(accounts)
        env["MYSQL_V18_COORDINATION_LEASE_HOLDER_PID"] = str(os.getpid())
        env["MYSQL_V18_COORDINATION_CONTRACT"] = str(coordination.CONTRACT_PATH)
        r0_id = f"{args.run_id}_r0"
        state["status"] = "R0_RUNNING"
        state["r0"] = str(HISTORY / r0_id / "r0_binding.json")
        atomic(state_path, state)
        completed = subprocess.run(
            [str(PYTHON), "-B", "-u", str(R0), "--run-id", r0_id],
            cwd=ROOT, env=env, text=True, check=False, timeout=1500,
        )
        state["r0_returncode"] = completed.returncode
        if completed.returncode != 0:
            raise RuntimeError("fresh recovered-XP R0 failed closed")
        r0_path = HISTORY / r0_id / "r0_binding.json"
        state.update({
            "status": "PASS",
            "completed_at": now(),
            "preflight_sha256": sha(preflight),
            "prepared_sha256": sha(prepared),
            "accounts_sha256": sha(accounts),
            "r0_sha256": sha(r0_path),
        })
        return 0
    except BaseException as error:
        state.update({
            "status": "FAILED_CLOSED",
            "failed_at": now(),
            "error": f"{type(error).__name__}: {error}",
        })
        return 4
    finally:
        if lease is not None:
            state["lease_held_seconds"] = coordination.release(lease)
        atomic(state_path, state)


if __name__ == "__main__":
    raise SystemExit(main())
