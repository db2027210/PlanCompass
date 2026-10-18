#!/usr/bin/env python3
"""Keep serialized Docker starts without truncating the caller timeout."""

from __future__ import annotations

import fcntl
from pathlib import Path

from scripts import reset_mysql_xp_data_planes_four_worker_v1 as predecessor
from scripts.reset_mysql_xp_data_planes_four_worker_v1 import *  # noqa: F401,F403

AUTHORITY_VERSION = "xp_four_worker_v2_start_timeout"
_BASE_DOCKER_RUN = predecessor._BASE_DOCKER_RUN
_DOCKER_START_LOCK = Path("/tmp/mysql_xp_four_worker_docker_start_v1.lock")


def _docker_run(argv, *, timeout):
    if list(argv[:2]) != ["docker", "start"]:
        return _BASE_DOCKER_RUN(argv, timeout=timeout)
    descriptor = _DOCKER_START_LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX)
        return _BASE_DOCKER_RUN(argv, timeout=float(timeout))
    finally:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_UN)
        descriptor.close()


def install() -> None:
    implementation = predecessor.predecessor._v6
    predecessor.install()
    implementation.AUTHORITY_VERSION = AUTHORITY_VERSION
    implementation._docker_run = _docker_run


install()
