#!/usr/bin/env python3
"""Install the v55 co-resident PostgreSQL isolation policy."""

from __future__ import annotations

from pathlib import Path

from scripts import mysql_co_resident_pg_isolation_v2 as predecessor
from scripts import mysql_cross_project_wave_coordination_v1 as coordination


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260816_v55"
    / "coordination_contract.json"
)


live_sealed_pg_container_readback = predecessor.live_sealed_pg_container_readback


def install() -> None:
    """Activate the unchanged sealed profiles under the v55 timing contract."""

    if not CONTRACT_PATH.is_file() or CONTRACT_PATH.is_symlink():
        raise RuntimeError(f"coordination contract absent: {CONTRACT_PATH}")
    coordination.CONTRACT_PATH = CONTRACT_PATH
    coordination._live_frozen_pg_container_readback = (
        live_sealed_pg_container_readback
    )

