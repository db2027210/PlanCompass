#!/usr/bin/env python3
"""Install the v63 device-scoped D-state coordination release."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v11 as predecessor
from scripts import mysql_cross_project_wave_coordination_v1 as coordination
from scripts import mysql_cross_project_wave_coordination_v2 as scoped_gate


CONTRACT_PATH = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v63"
    / "coordination_contract.json"
)


def install() -> None:
    predecessor.CONTRACT_PATH = CONTRACT_PATH
    predecessor.predecessor.CONTRACT_PATH = CONTRACT_PATH
    predecessor.install()
    coordination.CONTRACT_PATH = CONTRACT_PATH
    coordination.rolling_quiet_gate = scoped_gate.rolling_quiet_gate


def validate_release() -> None:
    if not CONTRACT_PATH.is_file() or CONTRACT_PATH.is_symlink():
        raise RuntimeError(f"v63 coordination contract absent: {CONTRACT_PATH}")
    predecessor.validate_release()
    install()
    if coordination.rolling_quiet_gate is not scoped_gate.rolling_quiet_gate:
        raise RuntimeError("device-scoped rolling gate was not installed")
    if coordination.CONTRACT_PATH != CONTRACT_PATH:
        raise RuntimeError("v63 coordination contract pointer differs")


if __name__ == "__main__":
    validate_release()
