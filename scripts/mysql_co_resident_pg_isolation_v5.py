#!/usr/bin/env python3
"""Install v57 read-only isolation with the sealed bench-pg-row disk3 profile."""

from pathlib import Path

from scripts.mysql_co_resident_pg_isolation_v4 import *  # noqa: F401,F403
from scripts import mysql_co_resident_pg_isolation_v4 as implementation


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v57"
    / "coordination_contract.json"
)


def install() -> None:
    implementation.CONTRACT_PATH = CONTRACT_PATH
    implementation.install()
