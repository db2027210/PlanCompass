from __future__ import annotations

import json

import pytest

from scripts import mysql_co_resident_pg_isolation_v5 as isolation


CONTAINER_ID = "e" * 64


def policy() -> dict:
    return {
        "allowed_external_cpu_union": "32-103,128-199,228-255,384-415",
        "containers": [],
        "additional_container_profiles": [{
            "name": "bench-pg-row",
            "role": "row_postgresql_server",
            "required": False,
            "allowed_statuses": ["running"],
            "image_id": "sha256:image",
            "cpuset_cpus": "144-159,400-415",
            "cpuset_mems": "",
            "nano_cpus": 32_000_000_000,
            "memory_bytes": 64 * 1024**3,
            "memory_swap_bytes": -1,
            "pids_limit": None,
            "required_mount_sources": ["/disk3/wxr-data/cmp/row"],
            "allowed_mount_prefixes": ["/disk3/wxr-data/cmp/"],
            "required_labels": {"com.docker.compose.service": "pg-row"},
        }],
    }


def document(source: str) -> dict:
    return {
        "Id": CONTAINER_ID,
        "Image": "sha256:image",
        "State": {"Status": "running", "Running": True, "Pid": 123},
        "HostConfig": {
            "CpusetCpus": "144-159,400-415", "CpusetMems": "",
            "NanoCpus": 32_000_000_000, "Memory": 64 * 1024**3,
            "MemorySwap": -1, "PidsLimit": None,
        },
        "Mounts": [{"Source": source}],
        "Config": {"Labels": {"com.docker.compose.service": "pg-row"}},
    }


def test_exact_disk3_profile_passes_and_disk2_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    predecessor = isolation.implementation
    monkeypatch.setattr(predecessor.coordination, "_target_cpu_union", lambda: {104, 200})
    monkeypatch.setattr(
        predecessor, "_docker_documents",
        lambda names: ({"bench-pg-row": document("/disk3/wxr-data/cmp/row")}, ""),
    )
    assert predecessor.live_sealed_pg_container_readback(policy())["passed"] is True
    monkeypatch.setattr(
        predecessor, "_docker_documents",
        lambda names: ({"bench-pg-row": document("/disk2/foreign")}, ""),
    )
    failed = predecessor.live_sealed_pg_container_readback(policy())
    assert failed["passed"] is False
    assert any("mounts_mysql_disk2" in row for row in failed["errors"])


def test_v57_contract_seals_disk3_without_external_mutation() -> None:
    value = json.loads(isolation.CONTRACT_PATH.read_text(encoding="utf-8"))
    profile = next(
        row for row in value["co_resident_isolated_pg_allowance"]
        ["additional_container_profiles"]
        if row["name"] == "bench-pg-row"
    )
    evidence = value["co_resident_isolated_pg_allowance"][
        "additional_container_release_evidence"
    ]
    assert value["release_version"] == "v57"
    assert profile["required_mount_sources"] == ["/disk3/wxr-data/cmp/row"]
    assert profile["allowed_mount_prefixes"] == ["/disk3/wxr-data/cmp/"]
    assert evidence["bench_pg_row_storage_source"] == "/dev/sda1"
    assert evidence["bench_pg_row_mysql_storage_overlap"] is False
    assert evidence["external_mutation"] is False
