#!/usr/bin/env python3
"""Complete reset facade preserving the full Docker-start caller timeout."""

from __future__ import annotations

import fcntl
from pathlib import Path

from scripts import reset_mysql_xp_data_planes_four_worker_v1 as four_worker_v1
from scripts.reset_mysql_xp_data_planes_four_worker_v1 import *  # noqa: F401,F403

for _name in dir(four_worker_v1):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(four_worker_v1, _name))

AUTHORITY_VERSION = "xp_four_worker_v4_start_timeout"
_BASE_DOCKER_RUN = four_worker_v1._BASE_DOCKER_RUN
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
    implementation = four_worker_v1.predecessor._v6
    four_worker_v1.install()
    implementation.AUTHORITY_VERSION = AUTHORITY_VERSION
    implementation._docker_run = _docker_run


install()
