#!/usr/bin/env python3
"""Start/resume one sealed JOB qualification under node02 systemd 219.

The worker token is accepted only through the 0600 file named by launch.json.
The retained transient oneshot never uses --wait, --collect, or --pipe.  This
controller grants no R0, formal, candidate, Top-5, or accepted-round credit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any, Mapping


sys.dont_write_bytecode = True
JOB_ID = "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"
CONTROL_ID = "7801df9f783c61fb5a72341c8bfc6ac04c28bd6f7729d12f7a5e987d28e3b1b3"
DISPATCH_SHA = "9447d3353e1cb52fe93b95d48bb12e2981632c0e494d8c0fd80c0ff6d26100c1"
BACKEND_SHA = "02ef703b51f923bdc3cbe0127830e929872eb212468e1b6dcbd1577ee00bb7ad"
PYTHON_PATH = Path("/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11")
PYTHON_SHA = "05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39"
UNIT_RE = re.compile(r"ra-mysql-job-qualification-[a-z0-9][a-z0-9-]{15,80}\.service$")
PROPERTIES = ("ActiveState", "SubState", "ExecMainStatus", "Result", "MainPID", "ControlGroup")


class RunnerError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_sealed(path: Path, label: str) -> dict[str, Any]:
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.is_symlink() or not path.is_file() or sidecar.is_symlink() or not sidecar.is_file():
        raise RunnerError(label + " is absent or unsafe")
    if sidecar.read_text(encoding="ascii") != f"{sha256(path)}  {path.name}\n":
        raise RunnerError(label + " sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RunnerError(label + " must contain one object")
    return value


def token_digest(path: Path) -> str:
    path = Path(path)
    mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
    if path.is_symlink() or not path.is_file() or mode != 0o600 or path.stat().st_uid != 1008:
        raise RunnerError("main-agent token must be a uid-1008 regular 0600 file")
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 32 or any(character.isspace() for character in token):
        raise RunnerError("main-agent token file differs")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def verify_python(path: Path = PYTHON_PATH) -> Path:
    resolved = Path(path).resolve(strict=True)
    if resolved != PYTHON_PATH or resolved.is_symlink() or sha256(resolved) != PYTHON_SHA:
        raise RunnerError("sealed JOB Python 3.11.11 differs")
    return resolved


def validate_launch(path: Path, support: Path) -> tuple[dict[str, Any], list[str]]:
    launch = load_sealed(path, "JOB qualification launch")
    command_path = Path(str(launch.get("command_spec") or ""))
    authority_path = Path(str(launch.get("main_agent_authority") or ""))
    token_path = Path(str(launch.get("main_agent_token_file") or ""))
    command = load_sealed(command_path, "JOB qualification command")
    authority = load_sealed(authority_path, "JOB qualification authority")
    digest = token_digest(token_path)
    dispatch = (support / "qualification/mysql_job_xp_shared_qualification_dispatch_v1.py").resolve(strict=True)
    backend = (Path(str(launch.get("bundle") or "")) /
               "qualification/mysql_job_distributed_live_readback_qualification_v2.py").resolve(strict=True)
    output = Path(str(launch.get("output") or ""))
    now = time.time()
    if (
        support.name != CONTROL_ID or sha256(dispatch) != DISPATCH_SHA
        or backend.parents[1].name != JOB_ID or sha256(backend) != BACKEND_SHA
        or launch.get("schema_version") != "mysql_job_v13_qualification_launch_descriptor_v2"
        or launch.get("status") != "MATERIALIZED_NOT_EXECUTED"
        or launch.get("formal_scope") != "mysql_only"
        or launch.get("cross_engine_comparison_prohibited") is not True
        or launch.get("postgresql_mutation_prohibited") is not True
        or launch.get("scenario_backend") != "job"
        or launch.get("mode") not in ("qualification_solo", "qualification_paired")
        or launch.get("shared_dispatch_sha256") != DISPATCH_SHA
        or Path(str(launch.get("bundle") or "")).name != JOB_ID
        or command.get("bundle_id") != JOB_ID or command.get("mode") != launch.get("mode")
        or authority.get("schema_version") != "mysql_algorithm_c_main_agent_qualification_authority_v1"
        or authority.get("bundle_id") != JOB_ID or authority.get("mode") != launch.get("mode")
        or authority.get("qualification_attempt_id") != command.get("qualification_attempt_id")
        or authority.get("command_spec_sha256") != sha256(command_path)
        or authority.get("main_agent_token_sha256") != digest
        or float(authority.get("not_before_epoch") or 0) > now
        or float(authority.get("expires_epoch") or 0) <= now + 240
        or any(value.get(key) is not False for value in (launch, authority)
               for key in ("capture_authorized", "formal_r0_authorized", "accepted_round_authorized"))
        or not output.is_absolute() or output.exists() or Path(str(output) + ".sha256").exists()
    ):
        raise RunnerError("JOB launch/authority/token binding differs")
    argv = [
        str(verify_python()), str(dispatch), "--scenario-backend", "job",
        "--backend-path", str(backend),
        "--" + str(launch["mode"]).replace("qualification_", "qualification-"),
        "--bundle", str(launch["bundle"]), "--container", str(launch["container"]),
        "--mysql-client-container", str(launch["mysql_client_container"]),
        "--mysql-binary-in-client", "/usr/bin/mysql",
        "--mysql-defaults-file", str(launch["mysql_defaults_file"]),
        "--output", str(output), "--main-agent-token-file", str(token_path),
        "--main-agent-authority", str(authority_path), "--command-spec", str(command_path),
    ]
    if launch["mode"] == "qualification_paired":
        if not launch.get("peer_readback") or not launch.get("peer_container"):
            raise RunnerError("paired JOB launch peer binding differs")
        argv.extend(["--peer-readback", str(launch["peer_readback"]),
                     "--peer-container", str(launch["peer_container"])])
    elif launch.get("peer_readback") is not None or launch.get("peer_container") is not None:
        raise RunnerError("solo JOB launch binds a peer")
    if token_path.read_text(encoding="ascii").strip() in argv:
        raise RunnerError("token content escaped into argv")
    return launch, argv


def parse_properties(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if line.count("=") != 1:
            raise RunnerError("systemd property output differs")
        key, value = line.split("=", 1)
        if key not in PROPERTIES or key in result:
            raise RunnerError("systemd property output differs")
        result[key] = value
    if set(result) != set(PROPERTIES):
        raise RunnerError("systemd property closure differs")
    return result


def systemctl_argv(unit: str) -> list[str]:
    return ["systemctl", "show", *["--property=" + key for key in PROPERTIES], unit]


def run(argv: list[str], timeout: int = 30) -> str:
    completed = subprocess.run(argv, check=False, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, timeout=timeout)
    if completed.returncode != 0:
        raise RunnerError("command failed: " + argv[0])
    return completed.stdout.strip()


def startticks(pid: int, proc_root: Path = Path("/proc")) -> int:
    text = (proc_root / str(pid) / "stat").read_text(encoding="ascii")
    close = text.rfind(")")
    fields = text[close + 2:].split()
    if close < 0 or len(fields) < 20:
        raise RunnerError("worker process stat differs")
    return int(fields[19])


def write_sealed(path: Path, value: Mapping[str, Any]) -> None:
    payload = canonical(value)
    sidecar = Path(str(path) + ".sha256")
    if path.exists() or path.is_symlink() or sidecar.exists() or sidecar.is_symlink():
        raise RunnerError("refusing to overwrite systemd receipt")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
    sidecar.write_text(f"{hashlib.sha256(payload).hexdigest()}  {path.name}\n", encoding="ascii")
    sidecar.chmod(0o600)


def start(launch_path: Path, support: Path, unit: str, receipt: Path,
          proc_root: Path = Path("/proc")) -> dict[str, Any]:
    if UNIT_RE.fullmatch(unit) is None:
        raise RunnerError("qualification unit name differs")
    launch, worker = validate_launch(launch_path, support.resolve(strict=True))
    argv = [
        "systemd-run", "--quiet", "--unit=" + unit,
        "--service-type=oneshot", "--remain-after-exit",
        "--uid=1008", "--gid=1008", "--property=TimeoutStartSec=900",
        "--property=KillMode=control-group", *worker,
    ]
    if any(value in argv for value in ("--wait", "--collect", "--pipe")):
        raise RunnerError("systemd 219 prohibited option present")
    run(argv)
    deadline = time.monotonic() + 15.0
    while True:
        state = parse_properties(run(systemctl_argv(unit)))
        if state["ActiveState"] == "activating" and state["SubState"] == "start":
            pid = int(state["MainPID"] or 0)
            if pid <= 1:
                raise RunnerError("systemd worker MainPID differs")
            ticks = startticks(pid, proc_root)
            break
        if state["ActiveState"] in ("failed", "inactive"):
            raise RunnerError("qualification unit failed before live PID readback")
        if time.monotonic() >= deadline:
            raise RunnerError("qualification unit live PID readback timed out")
        time.sleep(0.1)
    value = {
        "schema_version": "mysql_job_v3_qualification_systemd219_start_v1",
        "status": "PASS_STARTED_NOT_COMPLETED_NO_CREDIT",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "unit": unit,
        "launch_path": str(launch_path.resolve()), "launch_sha256": sha256(launch_path),
        "worker_argv_sha256": hashlib.sha256(canonical(worker)).hexdigest(),
        "worker_pid": pid, "worker_process_start_ticks": ticks,
        "output": launch["output"], "token_transport": "file_only",
        "qualification_credit": 0, "r0_credit": 0, "candidate_credit": 0,
        "top5_replay_credit": 0, "accepted_rounds_credit": 0,
        "accepted_round_authorized": False,
    }
    write_sealed(receipt, value)
    return value


def validate_evidence(path: Path) -> dict[str, Any]:
    value = load_sealed(path, "JOB qualification evidence")
    samples = value.get("samples")
    summary = value.get("summary") or {}
    if (
        value.get("schema_version") != "mysql_algorithm_c_debug_qualification_evidence_v2"
        or value.get("status") != "PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY"
        or value.get("bundle_id") != JOB_ID or value.get("duration_seconds") != 180
        or value.get("minimum_samples") != 181 or not isinstance(samples, list)
        or len(samples) < 181 or int(summary.get("sample_count") or 0) < 181
        or float(summary.get("elapsed_seconds") or 0) < 180.0
        or value.get("accepted_round_authorized") is not False
    ):
        raise RunnerError("JOB qualification completion evidence differs")
    return value


def complete(start_path: Path, output: Path, timeout_seconds: int = 660,
             proc_root: Path = Path("/proc")) -> tuple[dict[str, Any], bool]:
    started = load_sealed(start_path, "systemd start receipt")
    if (started.get("status") != "PASS_STARTED_NOT_COMPLETED_NO_CREDIT"
            or started.get("accepted_round_authorized") is not False):
        raise RunnerError("systemd start receipt differs")
    deadline = time.monotonic() + timeout_seconds
    success = False
    final: dict[str, str] = {}
    reason = "timeout"
    while time.monotonic() <= deadline:
        final = parse_properties(run(systemctl_argv(str(started["unit"]))))
        if final["ActiveState"] == "activating" and final["SubState"] == "start":
            pid = int(final["MainPID"] or 0)
            if (pid != int(started["worker_pid"])
                    or startticks(pid, proc_root) != int(started["worker_process_start_ticks"])):
                reason = "worker_pid_or_startticks_changed"
                break
            time.sleep(1.0)
            continue
        if final == {
            "ActiveState": "active", "SubState": "exited", "ExecMainStatus": "0",
            "Result": "success", "MainPID": "0", "ControlGroup": final["ControlGroup"],
        }:
            validate_evidence(Path(str(started["output"])))
            success, reason = True, "sealed_180_second_181_sample_evidence"
        else:
            reason = "unit_terminal_state_not_success"
        break
    value = {
        "schema_version": "mysql_job_v3_qualification_systemd219_completion_v1",
        "status": "PASS_COMPLETE_DEBUG_QUALIFICATION_NO_CREDIT" if success else "FAIL_NO_CREDIT",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True, "unit": started["unit"],
        "start_receipt_sha256": sha256(start_path), "systemd_final": final,
        "reason": reason, "qualification_credit": 0, "r0_credit": 0,
        "candidate_credit": 0, "top5_replay_credit": 0,
        "accepted_rounds_credit": 0, "accepted_round_authorized": False,
    }
    write_sealed(output, value)
    return value, success


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--start", action="store_true")
    modes.add_argument("--complete", action="store_true")
    parser.add_argument("--launch", type=Path)
    parser.add_argument("--support-bundle", type=Path)
    parser.add_argument("--unit")
    parser.add_argument("--start-receipt", type=Path, required=True)
    parser.add_argument("--completion-output", type=Path)
    args = parser.parse_args()
    if args.start:
        if args.launch is None or args.support_bundle is None or args.unit is None:
            raise RunnerError("start inputs are incomplete")
        value = start(args.launch, args.support_bundle, args.unit, args.start_receipt)
        ok = True
    else:
        if args.completion_output is None:
            raise RunnerError("completion output is required")
        value, ok = complete(args.start_receipt, args.completion_output)
    print(json.dumps(value, sort_keys=True))
    return 0 if ok else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RunnerError, OSError, UnicodeError, ValueError, KeyError,
            subprocess.TimeoutExpired) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
