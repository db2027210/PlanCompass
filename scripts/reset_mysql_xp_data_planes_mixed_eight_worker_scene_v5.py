#!/usr/bin/env python3
"""Scene-scoped XP8 reset resilient to Docker daemon start latency.

Candidate measurement remains eight-way parallel.  Only short-lived Docker
control-plane calls are bounded to four concurrent calls per scene.  A slow
``docker start`` gets a long response bound and an authoritative inspect before
retry, preventing the client-side timeout from turning a healthy reset into a
container ``context canceled`` failure.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v4 as predecessor


DOCKER_CONTROL_CONCURRENCY = 4
DOCKER_START_ATTEMPT_TIMEOUT_SECONDS = 600
DOCKER_START_ATTEMPTS = 3
_installed_original: Any = None


def _inspect_container(container: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "inspect", container], capture_output=True, text=True,
        check=True, timeout=60,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError(f"Docker inspect cardinality differs for {container}")
    return dict(value[0])


def _reap_exact_orphan_shim(container: str, inspected: Mapping[str, Any]) -> bool:
    """Reap only a unique stopped-container shim bound to the exact full ID."""
    state = dict(inspected.get("State") or {})
    container_id = str(inspected.get("Id") or "")
    if (
        len(container_id) != 64
        or state.get("Running") is not False
        or int(state.get("Pid") or 0) != 0
    ):
        raise RuntimeError(f"{container} orphan-shim recovery identity differs")
    matches: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        values = [value.decode("utf-8", "replace") for value in argv if value]
        if not values or not values[0].endswith("containerd-shim-runc-v2"):
            continue
        try:
            task_id = values[values.index("-id") + 1]
            namespace = values[values.index("-namespace") + 1]
        except (ValueError, IndexError):
            continue
        if task_id == container_id and namespace == "moby":
            matches.append(int(entry.name))
    if not matches:
        return False
    if len(matches) != 1:
        raise RuntimeError(
            f"{container} orphan-shim recovery cardinality differs: {matches}"
        )
    pid = matches[0]
    subprocess.run(
        ["sudo", "-n", "kill", "-KILL", str(pid)], capture_output=True,
        text=True, check=True, timeout=30,
    )
    deadline = time.monotonic() + 30.0
    while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.25)
    if Path(f"/proc/{pid}").exists():
        raise RuntimeError(f"{container} exact orphan shim {pid} was not reaped")
    print(
        f"reaped exact stopped-container orphan shim: {container} pid={pid}",
        file=sys.stderr, flush=True,
    )
    return True


def _successful_start(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        argv, 0, stdout=f"{argv[2]}\n", stderr="",
    )


def _published_ports_ready(inspected: Mapping[str, Any]) -> bool:
    expected = dict((inspected.get("HostConfig") or {}).get("PortBindings") or {})
    actual = dict((inspected.get("NetworkSettings") or {}).get("Ports") or {})
    for container_port, bindings in expected.items():
        expected_rows = {
            (str(row.get("HostIp") or ""), str(row.get("HostPort") or ""))
            for row in (bindings or [])
        }
        actual_rows = {
            (str(row.get("HostIp") or ""), str(row.get("HostPort") or ""))
            for row in (actual.get(container_port) or [])
        }
        if expected_rows != actual_rows:
            return False
    return True


def _stop_for_endpoint_repair(container: str) -> None:
    _installed_original(
        ["docker", "stop", "--time", "30", container], timeout=180,
    )


def _resilient_docker_run(
    argv: list[str], *, timeout: int = 240,
) -> subprocess.CompletedProcess[str]:
    if list(argv[:2]) != ["docker", "start"]:
        return _installed_original(argv, timeout=timeout)
    container = str(argv[2])
    last_error: Exception | None = None
    for attempt in range(1, DOCKER_START_ATTEMPTS + 1):
        try:
            completed = _installed_original(
                argv,
                timeout=max(timeout, DOCKER_START_ATTEMPT_TIMEOUT_SECONDS),
            )
            inspected = _inspect_container(container)
            state = dict(inspected.get("State") or {})
            if state.get("Running") is True and _published_ports_ready(inspected):
                return completed
            if state.get("Running") is True:
                _stop_for_endpoint_repair(container)
            if attempt == DOCKER_START_ATTEMPTS:
                raise RuntimeError(
                    f"{container} Docker endpoint was not restored after start"
                )
            time.sleep(2.0)
            continue
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as error:
            last_error = error
            inspected = _inspect_container(container)
            state = dict(inspected.get("State") or {})
            if state.get("Running") is True:
                if _published_ports_ready(inspected):
                    return _successful_start(argv)
                _stop_for_endpoint_repair(container)
            diagnostic = " ".join(
                str(value or "") for value in (
                    state.get("Error"), getattr(error, "stdout", ""),
                    getattr(error, "stderr", ""),
                )
            ).lower()
            retryable = (
                isinstance(error, subprocess.TimeoutExpired)
                or "context canceled" in diagnostic
                or "already exists" in diagnostic
                or "operation in progress" in diagnostic
            )
            if "already exists" in diagnostic:
                _reap_exact_orphan_shim(container, inspected)
            if not retryable or attempt == DOCKER_START_ATTEMPTS:
                raise
            time.sleep(2.0)
    assert last_error is not None
    raise last_error


def install_for_scene(workers: Mapping[str, Mapping[str, Any]]) -> None:
    global _installed_original
    predecessor.install_for_scene(workers)
    base = predecessor.predecessor.base
    _installed_original = base._impl["_docker_run"]
    if _installed_original is _resilient_docker_run:
        raise RuntimeError("XP8 resilient Docker control recursively installed")
    values = {
        "AUTHORITY_VERSION": "xp_mixed_eight_worker_scene_a_v5_resilient_start",
        "OUTPUT_SCHEMA": "mysql_xp_mixed_eight_worker_scene_a_resilient_start_binding_v5",
        "DOCKER_CONTROL_CONCURRENCY": DOCKER_CONTROL_CONCURRENCY,
        "_DOCKER_CONTROL_SEMAPHORE": threading.BoundedSemaphore(
            DOCKER_CONTROL_CONCURRENCY
        ),
        "_docker_run": _resilient_docker_run,
    }
    base._impl.update(values)
    globals().update(values)


def configure(*, freeze: Path, prepared: Path) -> None:
    predecessor.configure(freeze=freeze, prepared=prepared)
    globals().update({"FREEZE": freeze, "PREPARED": prepared})


def __getattr__(name: str) -> Any:
    try:
        return getattr(predecessor, name)
    except AttributeError:
        base = predecessor.predecessor.base
        if name in base._impl:
            return base._impl[name]
        raise
