#!/usr/bin/env python3
"""Four-worker XP reset authority for the Algorithm C cohort successor."""

from __future__ import annotations

import fcntl
from pathlib import Path
import threading

from scripts import mysql_algorithm_c_job_to_xp_slots_v1 as slots
from scripts import reset_mysql_xp_data_planes_v7 as predecessor
from scripts.reset_mysql_xp_data_planes_v7 import *  # noqa: F401,F403


for _name in dir(predecessor):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(predecessor, _name))


AUTHORITY_VERSION = "xp_four_worker_v1"
OUTPUT_SCHEMA = "mysql_algorithm_c_xp_four_worker_reprepared_binding_v1"
XP_IDS = {
    "xp_cpu_w1", "xp_cpu_w2", "xp_cpu_w3", "xp_cpu_w4",
    "xp_io_w1", "xp_io_w2", "xp_io_w3", "xp_io_w4",
}
XP_CONTAINER_ALLOWLIST = {
    "xp_cpu_w1": "ra_mysql_disk2_algc_xp_cpu_w1_20260809",
    "xp_cpu_w2": "ra_mysql_disk2_algc_xp_cpu_w2_20260809",
    "xp_io_w1": "ra_mysql_disk2_algc_xp_io_w1_20260809",
    "xp_io_w2": "ra_mysql_disk2_algc_xp_io_w2_20260809",
    **slots.NEW_CONTAINER,
}
DOCKER_CONTROL_CONCURRENCY = 4
_DOCKER_CONTROL_SEMAPHORE = threading.BoundedSemaphore(
    DOCKER_CONTROL_CONCURRENCY
)
_BASE_DOCKER_RUN = predecessor._v6._docker_run
_DOCKER_START_LOCK = Path("/tmp/mysql_xp_four_worker_docker_start_v1.lock")


def _docker_run(argv, *, timeout):
    """Serialize only Docker start RPCs across independent scene processes."""
    if list(argv[:2]) != ["docker", "start"]:
        return _BASE_DOCKER_RUN(argv, timeout=timeout)
    descriptor = _DOCKER_START_LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX)
        # Docker may finish the start RPC while delaying the CLI response
        # under NVMe control-plane pressure.  The v6 caller already handles
        # TimeoutExpired by inspecting the exact frozen container identity and
        # requiring Running=true, so release the cross-scene lock promptly.
        return _BASE_DOCKER_RUN(argv, timeout=min(float(timeout), 30.0))
    finally:
        fcntl.flock(descriptor.fileno(), fcntl.LOCK_UN)
        descriptor.close()


def install() -> None:
    """Bind constants into the v6 function globals used by the v7 facade."""
    implementation = predecessor._v6
    implementation.AUTHORITY_VERSION = AUTHORITY_VERSION
    implementation.OUTPUT_SCHEMA = OUTPUT_SCHEMA
    implementation.XP_IDS = set(XP_IDS)
    implementation.XP_CONTAINER_ALLOWLIST = dict(XP_CONTAINER_ALLOWLIST)
    implementation.DOCKER_CONTROL_CONCURRENCY = DOCKER_CONTROL_CONCURRENCY
    implementation._DOCKER_CONTROL_SEMAPHORE = _DOCKER_CONTROL_SEMAPHORE
    implementation._docker_run = _docker_run


install()
