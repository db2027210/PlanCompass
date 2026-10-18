#!/usr/bin/env python3
"""Install the v62 isolation contract with a reset-aware wave lease."""

from pathlib import Path

from scripts import mysql_co_resident_pg_isolation_v9 as predecessor


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v62"
    / "coordination_contract.json"
)


def install() -> None:
    predecessor.CONTRACT_PATH = CONTRACT_PATH
    predecessor.install()


def evaluate_co_resident_pg_isolation(workloads):
    predecessor.CONTRACT_PATH = CONTRACT_PATH
    return predecessor.evaluate_co_resident_pg_isolation(workloads)


if __name__ == "__main__":
    install()
