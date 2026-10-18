#!/usr/bin/env python3
"""Execute one sealed remote systemd stage and poll its retained unit."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import secrets
import shlex
import stat
import subprocess
import sys
import time
from typing import Any


class StageError(RuntimeError):
    pass


def run(argv: list[str], timeout: int = 30) -> str:
    completed = subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode:
        raise StageError(
            f"command failed rc={completed.returncode}: "
            + (completed.stderr or completed.stdout)[-2000:]
        )
    return completed.stdout


def properties(host: str, unit: str) -> dict[str, str]:
    raw = run([
        "ssh", host, shlex.join([
            "sudo", "-n", "systemctl", "show", unit,
            "--property=LoadState", "--property=ActiveState",
            "--property=SubState", "--property=MainPID",
            "--property=ExecMainStatus", "--property=Result",
        ]),
    ])
    return dict(
        line.split("=", 1) for line in raw.splitlines() if "=" in line
    )


def successful(value: dict[str, str]) -> bool:
    return (
        value.get("ActiveState") == "active"
        and value.get("SubState") == "exited"
        and value.get("MainPID") == "0"
        and value.get("ExecMainStatus") == "0"
        and value.get("Result") == "success"
    )


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load_materials(materials: Path) -> dict[str, Any]:
    value = json.loads(materials.read_text(encoding="utf-8"))
    expires = dt.datetime.fromisoformat(
        str(value.get("expires_at_utc")).replace("Z", "+00:00")
    )
    if (
        value.get("status") != "ISSUED_NOT_EXECUTED"
        or value.get("postgresql_mutation_prohibited") is not True
        or value.get("accepted_round_authorized") is not False
        or expires <= dt.datetime.now(dt.timezone.utc)
    ):
        raise StageError("sealed materials authority differs or expired")
    return value


def install_remote_token(materials: Path, host: str) -> dict[str, Any]:
    value = load_materials(materials)
    gate = value.get("remote_install_gate") or {}
    source = materials.parent / str(gate.get("source") or "")
    target = str(gate.get("target") or "")
    authorization_root = str(value.get("authorization_root") or "")
    attempt_root = str(value.get("attempt_root") or "")
    fixed_root = (
        "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
        "runtime_algorithm_c_successor_20260729/"
    )
    if (
        source.is_symlink()
        or not source.is_file()
        or stat.S_IMODE(source.stat().st_mode) != 0o600
        or sha256(source) != gate.get("sha256_expected")
        or gate.get("exclusive_create_required") is not True
        or gate.get("file_mode") != "0600"
        or gate.get("destination_directory_mode") != "0700"
        or gate.get("owner_uid_gid") != [0, 0]
        or not authorization_root.startswith(fixed_root + "authorizations/")
        or not attempt_root.startswith(fixed_root + "tpcc-dd-")
        or target != authorization_root + "/root-token"
    ):
        raise StageError("sealed remote token install gate differs")
    absent_probe = run([
        "ssh", host, shlex.join([
            "sudo", "-n", "test", "!", "-e", authorization_root,
        ]),
    ])
    if absent_probe:
        raise StageError("unexpected remote authorization-root probe output")
    run([
        "ssh", host, shlex.join([
            "sudo", "-n", "test", "!", "-e", attempt_root,
        ]),
    ])
    remote_stage = (
        "/tmp/ra-tpcc-token-" + value["attempt_id"] + "-"
        + secrets.token_hex(8)
    )
    run(["scp", "-p", str(source), host + ":" + remote_stage])
    try:
        run([
            "ssh", host, shlex.join([
                "sudo", "-n", "mkdir", "--mode=0700", authorization_root,
            ]),
        ])
        run([
            "ssh", host, shlex.join([
                "sudo", "-n", "cp", "--no-clobber", remote_stage, target,
            ]),
        ])
        run([
            "ssh", host, shlex.join([
                "sudo", "-n", "chown", "root:root", target,
            ]),
        ])
        run([
            "ssh", host, shlex.join([
                "sudo", "-n", "chmod", "0600", target,
            ]),
        ])
        remote_hash = run([
            "ssh", host, shlex.join([
                "sudo", "-n", "sha256sum", target,
            ]),
        ]).split()[0]
        remote_stat = run([
            "ssh", host, shlex.join([
                "sudo", "-n", "stat", "-c", "%u:%g:%a:%F", target,
            ]),
        ]).strip()
        if (
            remote_hash != gate["sha256_expected"]
            or remote_stat != "0:0:600:regular file"
        ):
            raise StageError("remote token readback differs")
    finally:
        run(["ssh", host, shlex.join(["unlink", remote_stage])])
    return {
        "status": "PASS_REMOTE_TOKEN_EXCLUSIVE_INSTALL",
        "attempt_id": value["attempt_id"],
        "bundle_id": value["bundle_id"],
        "target": target,
        "token_sha256": gate["sha256_expected"],
        "accepted_rounds_credit": 0,
    }


def execute(materials: Path, host: str, stage_name: str, timeout: int) -> dict[str, Any]:
    value = load_materials(materials)
    stages = value.get("stages") or {}
    stage = stages.get(stage_name)
    if (
        not isinstance(stage, dict)
    ):
        raise StageError("sealed materials/stage authority differs or expired")
    argv = stage.get("argv")
    unit = stage.get("unit")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
        or not isinstance(unit, str)
        or "--wait" in argv
        or "--collect" in argv
    ):
        raise StageError("sealed stage argv/unit differs")
    for dependency in stage.get("dependencies") or []:
        dependency_stage = stages.get(dependency) or {}
        state = properties(host, str(dependency_stage.get("unit") or ""))
        if not successful(state):
            raise StageError(
                f"dependency is not successful: {dependency}: {state}"
            )
    initial = properties(host, unit)
    if initial.get("LoadState") != "not-found":
        raise StageError("refusing to reuse an existing retained unit")
    launch = [
        "sudo", "-n", "systemd-run", "--quiet", "--unit", unit,
        "--service-type=oneshot", "--remain-after-exit", *argv,
    ]
    run(["ssh", host, shlex.join(launch)], timeout=30)
    deadline = time.monotonic() + timeout
    while True:
        state = properties(host, unit)
        if successful(state):
            return {
                "status": "PASS_RETAINED_SYSTEMD_STAGE",
                "attempt_id": value["attempt_id"],
                "bundle_id": value["bundle_id"],
                "stage": stage_name,
                "unit": unit,
                "unit_state": state,
                "accepted_rounds_credit": 0,
            }
        if state.get("ActiveState") == "failed":
            raise StageError(f"stage failed: {state}")
        if time.monotonic() >= deadline:
            raise StageError(f"stage poll timed out: {state}")
        time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--host", required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--stage")
    action.add_argument("--install-token-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if args.install_token_only:
        result = install_remote_token(args.materials, args.host)
    else:
        result = execute(args.materials, args.host, args.stage, args.timeout)
    print(json.dumps(
        result,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, StageError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write("NOT_READY: " + str(exc) + "\n")
        raise SystemExit(2)
