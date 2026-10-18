from __future__ import annotations

import json

from scripts import mysql_co_resident_pg_isolation_v13 as isolation


def test_unused_sealed_stopped_profile_does_not_block(monkeypatch, tmp_path) -> None:
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({
        "co_resident_isolated_pg_allowance": {
            "inactive_sealed_base_container_policy": {
                "enabled": True,
                "current_workload_pid_membership_required_absent": True,
                "all_non_liveness_profile_checks_must_pass": True,
            }
        }
    }), encoding="utf-8")
    error = "container_isolation_readback_differs:idle:container_not_running"
    monkeypatch.setattr(isolation, "CONTRACT_PATH", contract)
    monkeypatch.setattr(isolation, "PID_CONTAINER_ID", lambda pid: "active-id")
    monkeypatch.setattr(isolation, "BASE_EVALUATE", lambda workloads: {
        "status": "BLOCKED_FAIL_CLOSED", "passed": False, "errors": [error],
        "container_readback": {
            "passed": False, "errors": [error],
            "containers": [
                {"name": "idle", "container_id": "idle-id", "running": False},
                {"name": "active", "container_id": "active-id", "running": True},
            ],
        },
    })
    result = isolation.evaluate_co_resident_pg_isolation([{"pid": 7}])
    assert result["passed"] is True
    assert result["errors"] == []
    assert result["container_readback"]["passed"] is True
    assert result["inactive_sealed_base_container_policy"]["ignored_count"] == 1


def test_active_or_drifted_profile_still_blocks(monkeypatch, tmp_path) -> None:
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({
        "co_resident_isolated_pg_allowance": {
            "inactive_sealed_base_container_policy": {
                "enabled": True,
                "current_workload_pid_membership_required_absent": True,
                "all_non_liveness_profile_checks_must_pass": True,
            }
        }
    }), encoding="utf-8")
    stopped = "container_isolation_readback_differs:active:container_not_running"
    drift = "container_isolation_readback_differs:idle:container_memory_not_in_sealed_profiles"
    monkeypatch.setattr(isolation, "CONTRACT_PATH", contract)
    monkeypatch.setattr(isolation, "PID_CONTAINER_ID", lambda pid: "active-id")
    monkeypatch.setattr(isolation, "BASE_EVALUATE", lambda workloads: {
        "status": "BLOCKED_FAIL_CLOSED", "passed": False,
        "errors": [stopped, drift],
        "container_readback": {
            "passed": False, "errors": [stopped, drift],
            "containers": [
                {"name": "active", "container_id": "active-id", "running": False},
                {"name": "idle", "container_id": "idle-id", "running": False},
            ],
        },
    })
    result = isolation.evaluate_co_resident_pg_isolation([{"pid": 7}])
    assert result["passed"] is False
    assert result["errors"] == [stopped, drift]
    assert result["inactive_sealed_base_container_policy"]["ignored_count"] == 0
