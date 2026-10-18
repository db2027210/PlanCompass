#!/usr/bin/env python3
"""Install v59 isolation with exact template-clone control recognition."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shlex

from scripts import mysql_co_resident_pg_isolation_v6 as predecessor
from scripts import mysql_co_resident_pg_isolation_v4 as implementation
from scripts import mysql_cross_project_wave_coordination_v1 as coordination


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v59"
    / "coordination_contract.json"
)
ORIGINAL_COMMAND_KIND = implementation.command_kind
CLONE_SQL = re.compile(
    r"^CREATE DATABASE (fullgrid_job|fullgrid_tpch_sf5) WITH TEMPLATE "
    r"(template_fullgrid_job|template_fullgrid_tpch_sf5) OWNER postgres;?$"
)


def command_kind(command: str) -> str | None:
    try:
        fields = shlex.split(command)
    except ValueError:
        fields = command.split()
    if fields and Path(fields[0]).name == "psql":
        contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        policy = contract.get("co_resident_isolated_pg_allowance") or {}
        allowed_ports = {str(value) for value in policy.get("allowed_postgres_ports", [])}
        port = None
        sql = None
        for index, field in enumerate(fields):
            if field in {"-p", "--port"} and index + 1 < len(fields):
                port = fields[index + 1]
            elif field.startswith("--port="):
                port = field.split("=", 1)[1]
            elif field in {"-c", "--command"} and index + 1 < len(fields):
                sql = fields[index + 1]
        match = CLONE_SQL.fullmatch(str(sql or ""))
        if (
            port in allowed_ports
            and match is not None
            and match.group(1).removeprefix("fullgrid_")
            == match.group(2).removeprefix("template_fullgrid_")
        ):
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
