from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import mysql_external_compose_maintenance_guard_v1 as guard


def contract() -> dict:
    return json.loads(guard.DEFAULT_CONTRACT.read_text(encoding="utf-8"))


def inventory(value: dict, *, paused: bool) -> dict[str, dict]:
    return {
        row["name"]: {
            "Name": f"/{row['name']}",
            "Id": row["container_id"],
            "State": {"Running": True, "Paused": paused},
            "Config": {
                "Labels": {
                    "com.docker.compose.project": row["compose_project"],
                    "com.docker.compose.service": row["compose_service"],
                }
            },
        }
        for row in value["containers"]
    }


def test_frozen_contract_and_guard_source_binding_match() -> None:
    value = contract()
    guard.validate_contract(value, guard.DEFAULT_CONTRACT.resolve())
    source = Path(value["source_binding"]["guard"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == value["source_binding"]["guard_sha256"]
    names = {row["name"] for row in value["containers"]}
    assert set(value["pause_order"]) == names
    assert set(value["resume_order"]) == names
    assert value["requires_explicit_acknowledgement"] == guard.ACKNOWLEDGEMENT


def test_unpaused_and_paused_inventory_are_strictly_distinct() -> None:
    value = contract()
    initial = guard.validate_inventory(value, inventory(value, paused=False), require_paused=False)
    assert len(initial) == 7
    assert all(row["paused"] is False for row in initial)
    held = guard.validate_inventory(value, inventory(value, paused=True), require_paused=True)
    assert all(row["paused"] is True for row in held)
    with pytest.raises(RuntimeError, match="state differs"):
        guard.validate_inventory(value, inventory(value, paused=False), require_paused=True)


def test_container_identity_and_compose_labels_fail_closed() -> None:
    value = contract()
    changed = inventory(value, paused=False)
    changed["mongo"]["Id"] = "unexpected"
    with pytest.raises(RuntimeError, match="state differs: mongo"):
        guard.validate_inventory(value, changed, require_paused=False)
    changed = inventory(value, paused=False)
    changed["baibh-overleaf-pro-mongo"]["Config"]["Labels"]["com.docker.compose.project"] = "overleaf"
    with pytest.raises(RuntimeError, match="state differs: baibh-overleaf-pro-mongo"):
        guard.validate_inventory(value, changed, require_paused=False)


def test_pause_and_resume_orders_preserve_dependency_direction() -> None:
    value = contract()
    pause = value["pause_order"]
    resume = value["resume_order"]
    assert pause.index("sharelatex") < pause.index("mongo")
    assert pause.index("baibh-overleaf-pro") < pause.index("baibh-overleaf-pro-mongo")
    assert resume.index("mongo") < resume.index("sharelatex")
    assert resume.index("baibh-overleaf-pro-mongo") < resume.index("baibh-overleaf-pro")
