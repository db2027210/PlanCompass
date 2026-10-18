from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import mysql_external_compose_stop_maintenance_guard_v1 as guard


def contract() -> dict:
    return json.loads(guard.DEFAULT_CONTRACT.read_text(encoding="utf-8"))


def inventory(value: dict, *, running: bool) -> dict[str, dict]:
    return {
        row["name"]: {
            "Name": f"/{row['name']}",
            "Id": row["container_id"],
            "State": {"Running": running, "Paused": False},
            "Config": {"Labels": {
                "com.docker.compose.project": row["compose_project"],
                "com.docker.compose.service": row["compose_service"],
            }},
        }
        for row in value["containers"]
    }


def test_frozen_contract_source_and_authorization_bindings() -> None:
    value = contract()
    guard.validate_contract(value, guard.DEFAULT_CONTRACT.resolve())
    source = Path(value["source_binding"]["guard"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == value["source_binding"]["guard_sha256"]
    assert value["authorization"]["authorized_compose_projects"] == ["overleaf", "baibh-overleaf-pro"]
    assert value["authorization"]["authorized_container_count"] == 7
    assert value["maximum_maintenance_seconds"] == 28800
    assert value["requires_explicit_acknowledgement"] == guard.ACKNOWLEDGEMENT


def test_running_and_stopped_inventory_are_strictly_distinct() -> None:
    value = contract()
    initial = guard.validate_inventory(value, inventory(value, running=True), require_running=True)
    assert len(initial) == 7
    stopped = guard.validate_inventory(value, inventory(value, running=False), require_running=False)
    assert all(row["running"] is False for row in stopped)
    with pytest.raises(RuntimeError, match="state differs"):
        guard.validate_inventory(value, inventory(value, running=True), require_running=False)


def test_paused_identity_or_compose_drift_fails_closed() -> None:
    value = contract()
    changed = inventory(value, running=True)
    changed["mongo"]["State"]["Paused"] = True
    with pytest.raises(RuntimeError, match="state differs: mongo"):
        guard.validate_inventory(value, changed, require_running=True)
    changed = inventory(value, running=True)
    changed["baibh-overleaf-pro-mongo"]["Id"] = "unexpected"
    with pytest.raises(RuntimeError, match="state differs: baibh-overleaf-pro-mongo"):
        guard.validate_inventory(value, changed, require_running=True)


def test_stop_and_start_orders_preserve_dependency_direction() -> None:
    value = contract()
    stop = value["stop_order"]
    start = value["start_order"]
    assert stop.index("sharelatex") < stop.index("mongo")
    assert stop.index("baibh-overleaf-pro") < stop.index("baibh-overleaf-pro-mongo")
    assert start.index("mongo") < start.index("sharelatex")
    assert start.index("baibh-overleaf-pro-mongo") < start.index("baibh-overleaf-pro")
