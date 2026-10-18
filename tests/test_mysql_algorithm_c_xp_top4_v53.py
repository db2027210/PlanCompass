import json
import subprocess

from scripts import preflight_mysql_four_scene_v4 as preflight
from scripts import run_mysql_algorithm_c_async_scene_v53 as release


def test_v53_contract() -> None:
    contract = release.validate_contract()
    assert contract["container_inventory_successor"] == {
        "algorithm_or_parameter_flow_changed": False,
        "database_inspect_timeout_fails_closed": True,
        "intended_container_inspect_timeout_fails_closed": True,
        "process_inventory_unchanged": True,
        "trigger_observations": [
            "ra_mysql_disk2_alga_job_w3_20260821_disappeared_during_teardown",
            "overleaf-cloudflared_inspect_timeout",
        ],
        "unrelated_non_database_inspect_timeout_blocks": False,
        "unrelated_timeout_inventory_summary_required": True,
    }


def test_unrelated_non_database_inspect_timeout_is_inventoried(monkeypatch) -> None:
    summary = {
        "Names": "overleaf-cloudflared",
        "ID": "abc",
        "Image": "cloudflare/cloudflared:latest",
        "Command": "cloudflared tunnel run",
        "Mounts": "",
        "State": "running",
        "Status": "Up 1 hour",
    }

    def fake_run(argv, **kwargs):
        if argv[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(
                argv, 0, stdout=json.dumps(summary) + "\n", stderr=""
            )
        raise subprocess.TimeoutExpired(argv, timeout=15)

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    inventory, blockers = preflight._docker_inventory(set(), set())
    assert blockers == []
    assert inventory[0]["name"] == "overleaf-cloudflared"
    assert inventory[0]["inspect_status"] == (
        "TIMEOUT_OR_TRANSIENT_ERROR_SUMMARY_RECORDED"
    )


def test_database_inspect_timeout_fails_closed(monkeypatch) -> None:
    summary = {
        "Names": "mysql-live",
        "ID": "def",
        "Image": "mysql:8.0",
        "Command": "docker-entrypoint.sh mysqld",
        "Mounts": "/disk2/mysql",
        "State": "running",
        "Status": "Up 1 hour",
    }

    def fake_run(argv, **kwargs):
        if argv[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(
                argv, 0, stdout=json.dumps(summary) + "\n", stderr=""
            )
        raise subprocess.TimeoutExpired(argv, timeout=15)

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    inventory, blockers = preflight._docker_inventory(set(), set())
    assert inventory[0]["database_like"] is True
    assert blockers[0]["reason"] == (
        "database_or_intended_container_inspect_failed_closed"
    )
