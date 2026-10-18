#!/usr/bin/env python3
"""Bound Docker-control serialization to one formal measurement horizon.

The lock still covers one Docker API call at a time; it is not a scene, wave,
measurement, storage, or outer-round lease.  Under concurrent XP resets the
old 360-second acquisition bound could expire while other bounded Docker
calls were making progress.  A 900-second bound matches the formal maximum
measurement horizon and avoids turning control-plane queueing into an
optimizer failure.
"""

from __future__ import annotations

from scripts import mysql_docker_control_resilience_v1 as core
from scripts import mysql_docker_control_resilience_v2 as predecessor


LOCK_WAIT_SECONDS = 900.0


def install() -> None:
    core.LOCK_WAIT_SECONDS = LOCK_WAIT_SECONDS
    predecessor.install()


def uninstall() -> None:
    try:
        predecessor.uninstall()
    finally:
        core.LOCK_WAIT_SECONDS = 360.0

