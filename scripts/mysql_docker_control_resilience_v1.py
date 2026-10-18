#!/usr/bin/env python3
"""Short-operation Docker control serialization and inspect retry.

This lock covers only individual Docker control-plane requests.  It is not a
measurement, storage, scene, wave, or outer-round lease.  Workloads continue
independently while reset-related API calls are bounded.
"""

from __future__ import annotations

import fcntl
from pathlib import Path
import subprocess
import time
from typing import Any, Callable

from scripts import reset_mysql_xp_data_planes_v6 as reset


LOCK_PATH = Path("/tmp/resource_adaptive_mysql_docker_control_api_v1.lock")
INSPECT_TIMEOUT_SECONDS = 60
INSPECT_ATTEMPTS = 4
LOCK_WAIT_SECONDS = 360.0
_ORIGINAL_RUN = reset._docker_run
_ORIGINAL_JSON = reset._docker_json
_ORIGINAL_TIMEOUT = reset.DOCKER_CONTROL_TIMEOUT_SECONDS


def _serialized(call: Callable[[], Any]) -> Any:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as stream:
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Docker control API serialization timeout")
                time.sleep(0.25)
        try:
            return call()
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def resilient_run(argv: list[str], *, timeout: int = 240):
    return _serialized(lambda: _ORIGINAL_RUN(argv, timeout=timeout))


def resilient_json(*argv: str) -> dict[str, Any]:
    last: BaseException | None = None
    for attempt in range(1, INSPECT_ATTEMPTS + 1):
        try:
            return _serialized(lambda: _ORIGINAL_JSON(*argv))
        except subprocess.TimeoutExpired as error:
            last = error
            if attempt < INSPECT_ATTEMPTS:
                time.sleep(float(attempt * 2))
    assert last is not None
    raise last


def install() -> None:
    reset.DOCKER_CONTROL_TIMEOUT_SECONDS = INSPECT_TIMEOUT_SECONDS
    reset._docker_run = resilient_run
    reset._docker_json = resilient_json


def uninstall() -> None:
    reset._docker_json = _ORIGINAL_JSON
    reset._docker_run = _ORIGINAL_RUN
    reset.DOCKER_CONTROL_TIMEOUT_SECONDS = _ORIGINAL_TIMEOUT
