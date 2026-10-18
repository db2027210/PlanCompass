#!/usr/bin/env python3
"""Materialize a node04-compatible, non-executing systemd bootstrap plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Any, Mapping


sys.dont_write_bytecode = True
AUTHORITY_RELATIVE = "mysql_tpch_distributed_v2_authority.py"
TOKEN_FLAG = "--main-agent-token-file"
PROHIBITED_OPTIONS = ("--wait", "--collect")
REQUIRED_OPTIONS = ("--service-type=oneshot", "--remain-after-exit")


class MaterializeError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise MaterializeError("input must be a regular file: " + str(path))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MaterializeError("JSON object required: " + str(path))
    return value


def validate_help_fixture(fixture: Mapping[str, Any]) -> dict[str, Any]:
    text = fixture.get("stdout")
    if (
        fixture.get("schema_version")
        != "node04_systemd_run_help_fixture_v1"
        or fixture.get("host_id") != "node04"
        or fixture.get("command") != ["systemd-run", "--help"]
        or fixture.get("exit_code") != 0
        or not isinstance(text, str)
        or not text.strip()
        or hashlib.sha256(text.encode("utf-8")).hexdigest()
        != fixture.get("stdout_sha256")
        or "--service-type=" not in text
        or "--remain-after-exit" not in text
        or "--wait" in text
        or "--collect" not in text
    ):
        raise MaterializeError("node04 systemd-run help fixture differs")
    return dict(fixture)


def materialize(
    spec: Mapping[str, Any],
    *,
    bundle_path: str,
    remote_spec: str,
    remote_token_file: str,
    remote_output: str,
    help_fixture: Mapping[str, Any],
) -> dict[str, Any]:
    fixture = validate_help_fixture(help_fixture)
    execution = spec.get("bootstrap_execution") or {}
    attempt = spec.get("attempt_token")
    expected_unit = "ra-mysql-tpch-bootstrap-" + str(attempt) + ".service"
    if execution != {
        "mode": "direct_host_systemd_transient_oneshot",
        "unit": expected_unit,
        "service_type": "oneshot",
        "remain_after_exit": True,
        "ppid": 1,
        "token_transport": "token_file_or_in_process_only",
        "docker_ancestry_prohibited": True,
        "unit_retained_for_audit": True,
    }:
        raise MaterializeError("bootstrap execution contract differs")
    authority = bundle_path.rstrip("/") + "/" + AUTHORITY_RELATIVE
    argv = [
        "systemd-run", "--quiet", "--unit", expected_unit,
        "--service-type=oneshot", "--remain-after-exit",
        spec["runtime"]["target_root"] + "/bin/python3.11",
        authority, "--spec", remote_spec, "--execute-bootstrap",
        "--output", remote_output, TOKEN_FLAG, remote_token_file,
    ]
    if (
        any(option in argv for option in PROHIBITED_OPTIONS)
        or any(option not in argv for option in REQUIRED_OPTIONS)
    ):
        raise MaterializeError("systemd-run compatibility closure differs")
    return {
        "schema_version": "mysql_tpch_systemd_oneshot_launch_plan_v1",
        "status": "SEALED_PLAN_NOT_EXECUTED",
        "unit": expected_unit,
        "mode": "direct_host_systemd_transient_oneshot",
        "service_type": "oneshot",
        "remain_after_exit": True,
        "expected_running_identity": {
            "main_pid_equals_bootstrap_pid": True,
            "ppid": 1,
            "bind_startticks_namespaces_cgroup": True,
            "docker_ancestry_prohibited": True,
        },
        "argv": argv,
        "token_transport": "token_file",
        "token_content_in_os_argv_prohibited": True,
        "systemd_run_help_fixture_sha256": hashlib.sha256(
            canonical(fixture)
        ).hexdigest(),
        "controller_poll": {
            "argv": [
                "systemctl", "show", expected_unit,
                "--property=ActiveState", "--property=SubState",
                "--property=ExecMainStatus", "--property=Result",
                "--property=MainPID", "--property=ControlGroup",
                "--property=Transient",
            ],
            "running_states": [
                {"ActiveState": "activating", "SubState": "start"}
            ],
            "success_state": {
                "ActiveState": "active",
                "SubState": "exited",
                "ExecMainStatus": "0",
                "Result": "success",
            },
            "failure_policy": "fail_closed",
            "unit_reset_or_collect_prohibited": True,
            "unit_retained_for_audit": True,
        },
        "qualification_only": True,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def atomic_new(path: Path, value: Mapping[str, Any]) -> None:
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if any(item.exists() or item.is_symlink() for item in (path, sidecar)):
        raise MaterializeError("refusing to overwrite output")
    payload = canonical(value)
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(8))
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temporary, path)
    sidecar.write_text(
        hashlib.sha256(payload).hexdigest() + "  " + path.name + "\n",
        encoding="ascii",
    )
    os.chmod(path, 0o444)
    os.chmod(sidecar, 0o444)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--bundle-path", required=True)
    parser.add_argument("--remote-spec", required=True)
    parser.add_argument("--remote-token-file", required=True)
    parser.add_argument("--remote-output", required=True)
    parser.add_argument("--help-fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = materialize(
        load(args.spec),
        bundle_path=args.bundle_path,
        remote_spec=args.remote_spec,
        remote_token_file=args.remote_token_file,
        remote_output=args.remote_output,
        help_fixture=load(args.help_fixture),
    )
    atomic_new(args.output, result)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MaterializeError, OSError, UnicodeError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
