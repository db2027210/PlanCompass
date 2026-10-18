#!/usr/bin/env python3
"""v20 XP reset with frozen container-local no-swap enforcement.

The predecessor also required the host to have no configured swap device.
The v20 mysql-only envelope instead inventories host swap and disables
anonymous swapping for every MySQL container with MemorySwappiness=0.  This
successor removes only the obsolete host-global predicate; all per-container
Docker, cgroup, CPU/NUMA, memory, I/O and PID readbacks remain fail-closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from scripts import reset_mysql_xp_data_planes_client_primary_v1 as predecessor


def configure(
    *, freeze: Path, prepared: Path,
    xp_workers: Mapping[str, Mapping[str, Any]],
) -> None:
    predecessor.configure(
        freeze=freeze, prepared=prepared, xp_workers=xp_workers,
    )
    predecessor.predecessor._v6.prep._host_swap_devices = lambda: []
    predecessor.predecessor._v6.AUTHORITY_VERSION = (
        "client_primary_container_no_swap_v2"
    )
    predecessor.predecessor._v6.OUTPUT_SCHEMA = (
        "mysql_client_primary_xp_reset_v2"
    )


def __getattr__(name: str) -> Any:
    return getattr(predecessor, name)


for _name in dir(predecessor):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(predecessor, _name))
