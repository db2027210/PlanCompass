#!/usr/bin/env python3
"""Guard a formal MySQL run with a reversible external Compose maintenance window."""

from __future__ import annotations

import argparse
import atexit
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_external_compose_maintenance_20260813_v1"
    / "maintenance_contract.json"
)
LOCK_PATH = Path("/tmp/mysql_external_compose_maintenance_v1.lock")
ACKNOWLEDGEMENT = "overleaf+baibh-overleaf-pro"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha256(path)}  {path.name}\n", encoding="ascii",
    )


def append(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def run(command: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False,
        timeout=timeout,
    )


def docker_inventory(names: list[str]) -> dict[str, dict[str, Any]]:
    completed = run(["docker", "inspect", *names])
    if completed.returncode != 0:
        raise RuntimeError(f"docker inspect failed: {completed.stderr[-1000:]}")
    documents = json.loads(completed.stdout)
    if not isinstance(documents, list) or len(documents) != len(names):
        raise RuntimeError("docker inspect cardinality differs")
    result: dict[str, dict[str, Any]] = {}
    for document in documents:
        name = str(document.get("Name") or "").lstrip("/")
        if not name or name in result:
            raise RuntimeError("docker inspect name cardinality differs")
        result[name] = document
    if set(result) != set(names):
        raise RuntimeError("docker inspect names differ from maintenance contract")
    return result


def validate_inventory(
    contract: dict[str, Any], inventory: dict[str, dict[str, Any]],
    *, require_paused: bool,
) -> list[dict[str, Any]]:
    expected = contract.get("containers")
    if not isinstance(expected, list) or not expected:
        raise RuntimeError("maintenance contract containers differ")
    rows: list[dict[str, Any]] = []
    for frozen in expected:
        name = str(frozen.get("name") or "")
        document = inventory.get(name)
        if document is None:
            raise RuntimeError(f"maintenance container missing: {name}")
        state = document.get("State") or {}
        labels = (document.get("Config") or {}).get("Labels") or {}
        paused = state.get("Paused") is True
        if (
            document.get("Id") != frozen.get("container_id")
            or labels.get("com.docker.compose.project") != frozen.get("compose_project")
            or labels.get("com.docker.compose.service") != frozen.get("compose_service")
            or state.get("Running") is not True
            or paused is not require_paused
        ):
            raise RuntimeError(f"maintenance container state differs: {name}")
        rows.append({
            "name": name,
            "container_id": document.get("Id"),
            "compose_project": labels.get("com.docker.compose.project"),
            "compose_service": labels.get("com.docker.compose.service"),
            "running": True,
            "paused": paused,
        })
    return rows


def validate_contract(contract: dict[str, Any], contract_path: Path) -> None:
    names = [str(row.get("name") or "") for row in contract.get("containers", [])]
    if (
        contract_path != DEFAULT_CONTRACT.resolve()
        or contract_path.is_symlink()
        or contract.get("schema_version")
        != "mysql_external_compose_maintenance_contract_v1"
        or contract.get("status") != "FROZEN_READY_REQUIRES_USER_AUTHORIZATION"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("lock_path") != str(LOCK_PATH)
        or len(names) != len(set(names))
        or set(contract.get("pause_order") or []) != set(names)
        or set(contract.get("resume_order") or []) != set(names)
        or contract.get("requires_explicit_acknowledgement") != ACKNOWLEDGEMENT
    ):
        raise RuntimeError("frozen maintenance contract differs")


def pause_one(name: str) -> None:
    completed = run(["docker", "pause", name])
    if completed.returncode != 0 or completed.stdout.strip() != name:
        raise RuntimeError(f"docker pause failed for {name}: {completed.stderr[-1000:]}")


def unpause_one(name: str) -> None:
    completed = run(["docker", "unpause", name])
    if completed.returncode != 0 or completed.stdout.strip() != name:
        raise RuntimeError(f"docker unpause failed for {name}: {completed.stderr[-1000:]}")


def systemd_active(unit: str) -> bool:
    completed = run(["systemctl", "--user", "is-active", unit], timeout=30)
    return completed.stdout.strip() in {"active", "activating", "reloading"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--execute-maintenance", action="store_true")
    parser.add_argument("--acknowledge-service-outage")
    parser.add_argument("--waiter-result", type=Path)
    parser.add_argument("--maximum-seconds", type=int, default=28800)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.validate_only == args.execute_maintenance:
        parser.error("select exactly one of --validate-only or --execute-maintenance")
    if args.execute_maintenance and (
        args.acknowledge_service_outage != ACKNOWLEDGEMENT
        or args.waiter_result is None
        or not args.command
    ):
        parser.error("execution requires exact outage acknowledgement, waiter result, and command")
    if args.validate_only and (args.waiter_result is not None or args.command):
        parser.error("validation-only mode cannot launch a command")
    if args.maximum_seconds < 600:
        parser.error("maximum maintenance duration must be at least 600 seconds")

    contract_path = args.contract.expanduser().resolve(strict=True)
    contract = load(contract_path)
    validate_contract(contract, contract_path)
    result_path = args.result.expanduser().resolve()
    log_path = args.log.expanduser().resolve()
    for path in (result_path, result_path.with_name(result_path.name + ".sha256"), log_path):
        if path.exists():
            raise RuntimeError(f"fresh maintenance artifact required: {path}")
    names = [str(row["name"]) for row in contract["containers"]]
    initial = validate_inventory(contract, docker_inventory(names), require_paused=False)
    base = {
        "schema_version": "mysql_external_compose_maintenance_result_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "contract_path": str(contract_path),
        "contract_sha256": sha256(contract_path),
        "guard_source_path": str(Path(__file__).resolve()),
        "guard_source_sha256": sha256(Path(__file__).resolve()),
        "initial_inventory": initial,
    }
    if args.validate_only:
        document = {
            **base,
            "status": "READY_NOT_EXECUTED",
            "completed_at": now(),
            "external_state_mutated": False,
            "authorization_required": True,
        }
        atomic(result_path, document)
        print(json.dumps(document, ensure_ascii=False, sort_keys=True))
        return 0

    lock_stream = LOCK_PATH.open("w", encoding="ascii")
    try:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another external maintenance guard is active") from error
    lock_stream.write(f"{os.getpid()}\n")
    lock_stream.flush()
    paused_by_guard: list[str] = []
    child: subprocess.Popen[str] | None = None
    cleanup_errors: list[str] = []
    cleanup_done = False

    def cleanup() -> None:
        nonlocal cleanup_done
        if cleanup_done:
            return
        cleanup_done = True
        for name in contract["resume_order"]:
            if name not in paused_by_guard:
                continue
            try:
                unpause_one(name)
                append(log_path, {"event": "container_unpaused", "captured_at": now(), "container": name})
            except Exception as error:  # pragma: no cover - operational recovery path
                cleanup_errors.append(f"{name}: {error}")

    termination_signal: int | None = None

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal termination_signal
        termination_signal = _signum
        if child is not None and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    atexit.register(cleanup)
    started = time.monotonic()
    command_returncode: int | None = None
    formal_unit: str | None = None
    failure: str | None = None
    try:
        append(log_path, {"event": "maintenance_started", "captured_at": now(), "guard_pid": os.getpid()})
        for name in contract["pause_order"]:
            if termination_signal is not None:
                raise RuntimeError(f"maintenance interrupted by signal {termination_signal}")
            pause_one(name)
            paused_by_guard.append(name)
            append(log_path, {"event": "container_paused", "captured_at": now(), "container": name})
        settle = float(contract["post_pause_settle_seconds"])
        time.sleep(settle)
        paused = validate_inventory(contract, docker_inventory(names), require_paused=True)
        append(log_path, {"event": "paused_inventory_validated", "captured_at": now(), "inventory": paused})
        child = subprocess.Popen(args.command, cwd=ROOT, text=True)
        append(log_path, {"event": "waiter_started", "captured_at": now(), "pid": child.pid})
        next_validation = time.monotonic()
        while child.poll() is None:
            if termination_signal is not None:
                raise RuntimeError(f"maintenance interrupted by signal {termination_signal}")
            if time.monotonic() - started > args.maximum_seconds:
                child.terminate()
                raise TimeoutError("maintenance waiter duration exceeded")
            if time.monotonic() >= next_validation:
                validate_inventory(contract, docker_inventory(names), require_paused=True)
                next_validation = time.monotonic() + float(contract["runtime_validation_interval_seconds"])
            time.sleep(1.0)
        command_returncode = child.returncode
        append(log_path, {"event": "waiter_finished", "captured_at": now(), "returncode": command_returncode})
        if command_returncode != 0:
            raise RuntimeError(f"waiter failed with return code {command_returncode}")
        waiter_result = load(args.waiter_result.expanduser().resolve(strict=True))
        formal_unit = str(waiter_result.get("formal_unit") or "")
        if waiter_result.get("status") != "STARTED_WITH_RUNTIME_WATCHDOG" or not formal_unit:
            raise RuntimeError("waiter receipt does not prove formal launch")
        append(log_path, {"event": "formal_unit_monitor_started", "captured_at": now(), "unit": formal_unit})
        while systemd_active(formal_unit):
            if termination_signal is not None:
                raise RuntimeError(f"maintenance interrupted by signal {termination_signal}")
            if time.monotonic() - started > args.maximum_seconds:
                run(["systemctl", "--user", "stop", formal_unit], timeout=30)
                raise TimeoutError("formal maintenance duration exceeded")
            validate_inventory(contract, docker_inventory(names), require_paused=True)
            time.sleep(float(contract["runtime_validation_interval_seconds"]))
        append(log_path, {"event": "formal_unit_inactive", "captured_at": now(), "unit": formal_unit})
    except BaseException as error:  # preserve recovery evidence for every operational failure
        failure = f"{type(error).__name__}: {error}"
        append(log_path, {"event": "maintenance_failed", "captured_at": now(), "error": failure})
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=30)
        if formal_unit and systemd_active(formal_unit):
            stopped = run(["systemctl", "--user", "stop", formal_unit], timeout=30)
            append(log_path, {
                "event": "formal_unit_stopped_fail_closed",
                "captured_at": now(),
                "unit": formal_unit,
                "returncode": stopped.returncode,
            })
    finally:
        cleanup()

    final_inventory: list[dict[str, Any]] = []
    try:
        final_inventory = validate_inventory(
            contract, docker_inventory(names), require_paused=False,
        )
    except BaseException as error:
        cleanup_errors.append(f"final inventory: {type(error).__name__}: {error}")
    document = {
        **base,
        "status": (
            "RESTORE_FAILED" if cleanup_errors
            else "FAILED_AND_EXTERNAL_SERVICES_RESTORED" if failure
            else "COMPLETED_AND_EXTERNAL_SERVICES_RESTORED"
        ),
        "completed_at": now(),
        "external_state_mutated": True,
        "maintenance_seconds": time.monotonic() - started,
        "command_returncode": command_returncode,
        "formal_unit": formal_unit,
        "final_inventory": final_inventory,
        "failure": failure,
        "termination_signal": termination_signal,
        "cleanup_errors": cleanup_errors,
    }
    atomic(result_path, document)
    print(json.dumps(document, ensure_ascii=False, sort_keys=True))
    if cleanup_errors:
        return 4
    if failure:
        return 128 + termination_signal if termination_signal is not None else 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
