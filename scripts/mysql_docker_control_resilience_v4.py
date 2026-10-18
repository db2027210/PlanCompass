#!/usr/bin/env python3
"""Allow slow XP container starts without changing measurement time limits.

Docker start has exceeded the reset implementation's 120-second response
deadline on this host.  The predecessor already serializes each individual
Docker API call; this successor extends only that call's response deadline.
It does not hold a scene, wave, measurement, or outer-round lease.
"""

from __future__ import annotations

from scripts import mysql_docker_control_resilience_v1 as core
from scripts import mysql_docker_control_resilience_v3 as predecessor
from scripts import reset_mysql_xp_data_planes_v6 as reset


START_TIMEOUT_SECONDS = 600


def resilient_run(argv: list[str], *, timeout: int = 240):
    effective = max(timeout, START_TIMEOUT_SECONDS) if argv[:2] == ["docker", "start"] else timeout
    return core.resilient_run(argv, timeout=effective)


def install() -> None:
    predecessor.install()
    reset._docker_run = resilient_run


def uninstall() -> None:
    predecessor.uninstall()
