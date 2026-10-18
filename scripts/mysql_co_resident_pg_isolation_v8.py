#!/usr/bin/env python3
"""Install v60 isolation with raw-cmdline clone control recognition."""

from __future__ import annotations

import json
from pathlib import Path
import re

from scripts import mysql_co_resident_pg_isolation_v7 as predecessor
from scripts import mysql_co_resident_pg_isolation_v4 as implementation
from scripts import mysql_cross_project_wave_coordination_v1 as coordination


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v60/coordination_contract.json"
ORIGINAL_COMMAND_KIND = implementation.command_kind
CLONE_COMMAND = re.compile(
    r"(?:^|\s)psql\s+.*?(?:^|\s)-p\s+(\d+)\s+.*?(?:^|\s)-c\s+"
    r"CREATE DATABASE (fullgrid_job|fullgrid_tpch_sf5) WITH TEMPLATE "
    r"(template_fullgrid_job|template_fullgrid_tpch_sf5) OWNER postgres;?\s*$"
)


def command_kind(command: str) -> str | None:
    match = CLONE_COMMAND.search(command)
    if match is not None:
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        policy = contract.get("co_resident_isolated_pg_allowance") or {}
        ports = {str(value) for value in policy.get("allowed_postgres_ports", [])}
        database = match.group(2).removeprefix("fullgrid_")
        template = match.group(3).removeprefix("template_fullgrid_")
        if match.group(1) in ports and database == template:
            return "controller"
    return ORIGINAL_COMMAND_KIND(command)


def install() -> None:
    predecessor.CONTRACT_PATH = CONTRACT_PATH
    predecessor.install()
    implementation.CONTRACT_PATH = CONTRACT_PATH
    coordination.CONTRACT_PATH = CONTRACT_PATH
    coordination.command_kind = command_kind


if __name__ == "__main__":
    install()
