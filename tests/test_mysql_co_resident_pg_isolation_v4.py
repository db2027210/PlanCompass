from __future__ import annotations

import json
from pathlib import Path
import pytest

from scripts import mysql_co_resident_pg_isolation_v4 as isolation
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v11 as runner


CLIENT_ID = "c" * 64


def policy() -> dict:
    return {
        "allowed_external_cpu_union": "32-103,128-199,228-255,384-415",
        "containers": [],
        "additional_container_profiles": [{
            "name": "bench-run-mixstrg-opt",
            "role": "benchbase_client",
            "required": False,
            "allowed_statuses": ["running"],
            "image_id": "sha256:image",
            "cpuset_cpus": "128-131,384-387",
            "nano_cpus": 8_000_000_000,
            "memory_bytes": 12 * 1024**3,
            "memory_swap_bytes": -1,
            "pids_limit": None,
            "required_mount_sources": ["/home/anonymous/bench-profiles"],
            "required_mount_prefixes": ["/home/anonymous/results/opt/"],
            "allowed_mount_prefixes": ["/home/anonymous/"],
        }],
    }


def inspect_document(*, cpuset: str = "128-131,384-387",
                     mount: str = "/home/anonymous/results/opt/run_1") -> str:
    return json.dumps([{
        "Name": "/bench-run-mixstrg-opt",
        "Id": CLIENT_ID,
        "Image": "sha256:image",
        "State": {"Status": "running", "Running": True, "Pid": 123},
        "HostConfig": {
            "CpusetCpus": cpuset,
            "CpusetMems": "",
            "NanoCpus": 8_000_000_000,
            "Memory": 12 * 1024**3,
            "MemorySwap": -1,
            "PidsLimit": None,
        },
        "Mounts": [
            {"Source": "/home/anonymous/bench-profiles"},
            {"Source": mount},
        ],
        "Config": {"Labels": {}},
    }])


def docker_readback(stdout: str):
    document = json.loads(stdout)[0]

    def read(name: str):
        if name == "bench-run-mixstrg-opt":
            return document, ""
        return None, ""
    return read


def test_exact_disjoint_additional_profile_passes_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(isolation.coordination, "_target_cpu_union", lambda: {104, 200})
    monkeypatch.setattr(
        isolation,
        "_docker_document",
        docker_readback(inspect_document()),
    )
    result = isolation.live_sealed_pg_container_readback(policy())
    assert result["passed"] is True
    assert result["read_only"] is True
    assert result["external_mutation"] is False
    assert result["additional_containers"][0]["container_id"] == CLIENT_ID


@pytest.mark.parametrize(
    ("cpuset", "mount", "message"),
    [
        ("104", "/home/anonymous/results/opt/run_1", "cpu_overlaps_mysql"),
        ("128-131,384-387", "/disk2/foreign", "mounts_mysql_disk2"),
    ],
)
def test_overlap_or_disk2_mount_fails_closed(
    monkeypatch: pytest.MonkeyPatch, cpuset: str, mount: str, message: str,
) -> None:
    monkeypatch.setattr(isolation.coordination, "_target_cpu_union", lambda: {104, 200})
    monkeypatch.setattr(
        isolation,
        "_docker_document",
        docker_readback(inspect_document(cpuset=cpuset, mount=mount)),
    )
    result = isolation.live_sealed_pg_container_readback(policy())
    assert result["passed"] is False
    assert any(message in error for error in result["errors"])


def test_only_exact_live_cgroup_membership_removes_command_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    pid = 321
    proc = tmp_path / str(pid)
    proc.mkdir()
    (proc / "cgroup").write_text(
        f"0::/docker-limited/{CLIENT_ID}\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        isolation,
        "_pid_container_id",
        lambda candidate: CLIENT_ID if candidate == pid else None,
    )
    monkeypatch.setattr(
        isolation,
        "ORIGINAL_EVALUATE",
        lambda workloads: {
            "status": "BLOCKED_FAIL_CLOSED",
            "passed": False,
            "errors": [f"pid_{pid}:external_command_not_in_frozen_pg_cohort"],
            "workloads": [{
                "pid": pid,
                "errors": ["external_command_not_in_frozen_pg_cohort"],
            }],
            "container_readback": {
                "passed": True,
                "errors": [],
                "additional_containers": [{
                    "name": "bench-run-mixstrg-opt",
                    "container_id": CLIENT_ID,
                    "present": True,
                    "running": True,
                    "errors": [],
                }],
            },
        },
    )
    result = isolation.evaluate_co_resident_pg_isolation([
        {"pid": pid, "command": "java -jar benchbase.jar"}
    ])
    assert result["passed"] is True
    assert result["errors"] == []
    assert result["workloads"][0]["sealed_additional_container"] == (
        "bench-run-mixstrg-opt"
    )


def test_unknown_process_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(isolation, "_pid_container_id", lambda pid: "d" * 64)
    monkeypatch.setattr(
        isolation,
        "ORIGINAL_EVALUATE",
        lambda workloads: {
            "status": "BLOCKED_FAIL_CLOSED",
            "passed": False,
            "errors": ["pid_9:external_command_not_in_frozen_pg_cohort"],
            "workloads": [{
                "pid": 9,
                "errors": ["external_command_not_in_frozen_pg_cohort"],
            }],
            "container_readback": {
                "additional_containers": [{
                    "name": "bench-run-mixstrg-opt",
                    "container_id": CLIENT_ID,
                    "present": True,
                    "running": True,
                    "errors": [],
                }],
            },
        },
    )
    result = isolation.evaluate_co_resident_pg_isolation([
        {"pid": 9, "command": "java -jar benchbase.jar"}
    ])
    assert result["passed"] is False
    assert result["errors"]


def test_exact_host_docker_exec_proxy_is_controller_but_inner_psql_is_not() -> None:
    assert isolation.command_kind(
        "docker exec bench-pg-mixstrg-0817 psql -U postgres -d benchbase -c SELECT"
    ) == "controller"
    assert isolation.command_kind(
        "docker exec unknown-pg psql -U postgres -d benchbase -c SELECT"
    ) == "workload"
    assert isolation.command_kind("psql -U postgres -d benchbase -c SELECT") == (
        "workload"
    )


def test_v56_release_validator_preserves_old_gate_and_checks_new_profiles() -> None:
    isolation.install()
    runner.v10.v4.runtime.WAVE_COORDINATION_CONTRACT = isolation.CONTRACT_PATH
    contract = runner.validate_wave_coordination_contract()
    assert contract["release_version"] == "v56"
    assert contract["maximum_quiet_wait_while_held_seconds"] == 60
