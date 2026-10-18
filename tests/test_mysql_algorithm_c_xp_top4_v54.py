import http.client
from contextlib import contextmanager

from scripts import preflight_mysql_four_scene_v5 as preflight
from scripts import run_mysql_algorithm_c_async_scene_v54 as release


@contextmanager
def _fake_lock():
    yield {
        "lock_path": "/tmp/test.lock",
        "wait_seconds": 0.0,
        "scope": "docker_list_and_inspect_snapshot_only",
    }


def test_v54_contract() -> None:
    contract = release.validate_contract()
    inventory = contract["docker_unix_socket_inventory_successor"]
    assert inventory["inventory_transport"] == "docker_unix_socket_http_api"
    assert inventory["snapshot_lock_scope"] == (
        "docker_list_and_inspect_snapshot_only"
    )
    assert inventory["database_or_workload_resource_lease"] is False
    assert inventory["cross_scene_completion_dependency"] is False
    assert inventory["algorithm_or_parameter_flow_changed"] is False


def test_socket_inventory_and_name_normalization(monkeypatch) -> None:
    summary = {
        "Id": "abc",
        "Names": ["/xp-cpu"],
        "Image": "mysql:8.0",
        "Command": "mysqld",
        "Mounts": [],
        "State": "running",
        "Status": "Up",
    }
    inspected = {
        "Id": "abc",
        "State": {"Pid": 42},
        "Path": "mysqld",
        "Args": [],
        "HostConfig": {"CpusetCpus": "4-5", "CpusetMems": "0"},
        "Mounts": [{"Source": "/disk2/xp-cpu"}],
    }

    def fake_get(path, timeout):
        return [summary] if path == "/containers/json?all=0" else inspected

    monkeypatch.setattr(preflight, "_docker_get", fake_get)
    monkeypatch.setattr(preflight, "_snapshot_lock", _fake_lock)
    inventory, blockers = preflight._docker_inventory({4}, {"xp-cpu"})
    assert blockers == []
    assert inventory[0]["name"] == "xp-cpu"
    assert inventory[0]["overlap_cpus"] == "4"
    assert inventory[0]["inventory_source"] == "docker_unix_socket_api"


def test_unrelated_failure_continues_but_database_failure_blocks(monkeypatch) -> None:
    summaries = [
        {
            "Id": "cloud",
            "Names": ["/overleaf-cloudflared"],
            "Image": "cloudflare/cloudflared:latest",
            "Command": "cloudflared tunnel run",
            "Mounts": [],
            "State": "running",
            "Status": "Up",
        },
        {
            "Id": "mysql",
            "Names": ["/mysql-live"],
            "Image": "mysql:8.0",
            "Command": "mysqld",
            "Mounts": [],
            "State": "running",
            "Status": "Up",
        },
    ]

    def fake_get(path, timeout):
        if path == "/containers/json?all=0":
            return summaries
        raise http.client.RemoteDisconnected("test disconnect")

    monkeypatch.setattr(preflight, "_docker_get", fake_get)
    monkeypatch.setattr(preflight, "_snapshot_lock", _fake_lock)
    inventory, blockers = preflight._docker_inventory(set(), set())
    assert len(inventory) == 2
    assert len(blockers) == 1
    assert blockers[0]["name"] == "mysql-live"
    assert blockers[0]["reason"] == (
        "database_or_intended_container_inspect_failed_closed"
    )
