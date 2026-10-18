from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import mysql_co_resident_pg_isolation_v2 as isolation
from scripts import rebind_mysql_algorithm_c_empty_replay_quarantine_v2 as rebind


OLD = "88-91"
WIDE = "32-39,88-95,132-133,184-189,196-197,228-241"


def policy() -> dict:
    return {
        "allowed_external_cpu_union": (
            "32-103,128-199,228-255"
        ),
        "containers": [{
            "name": "pg_w1",
            "container_id": "a" * 64,
            "cpuset_cpus": OLD,
            "memory_bytes": 12 * 1024**3,
            "allowed_cpuset_cpus": [OLD, WIDE],
            "allowed_memory_bytes": [12 * 1024**3, 24 * 1024**3],
            "disk1_mount_source": "/disk1/pg/w1",
        }],
    }


def inspect_document(*, cpuset: str = WIDE, memory: int = 24 * 1024**3,
                     mount: str = "/disk1/pg/w1") -> str:
    return json.dumps([{
        "Id": "a" * 64,
        "State": {"Running": True},
        "HostConfig": {
            "CpusetCpus": cpuset,
            "CpusetMems": "",
            "Memory": memory,
            "MemorySwap": -1,
            "PidsLimit": None,
        },
        "Mounts": [{"Source": mount}],
    }])


def test_sealed_wide_profile_passes_when_cpu_and_disk_are_disjoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(isolation.coordination, "_target_cpu_union", lambda: {0, 104, 200})
    monkeypatch.setattr(
        isolation.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=inspect_document(),
        ),
    )
    result = isolation.live_sealed_pg_container_readback(policy())
    assert result["passed"] is True
    assert result["errors"] == []
    assert result["containers"][0]["cpuset_cpus"] == WIDE


@pytest.mark.parametrize(
    ("cpuset", "memory", "mount", "message"),
    [
        ("104", 24 * 1024**3, "/disk1/pg/w1", "container_cpu_overlaps_mysql"),
        (WIDE, 20 * 1024**3, "/disk1/pg/w1", "container_memory_not_in_sealed_profiles"),
        (WIDE, 24 * 1024**3, "/disk2/pg/w1", "container_mounts_mysql_disk2"),
    ],
)
def test_unsealed_or_overlapping_profile_fails_closed(
    monkeypatch: pytest.MonkeyPatch, cpuset: str, memory: int,
    mount: str, message: str,
) -> None:
    monkeypatch.setattr(isolation.coordination, "_target_cpu_union", lambda: {0, 104, 200})
    monkeypatch.setattr(
        isolation.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=inspect_document(cpuset=cpuset, memory=memory, mount=mount),
        ),
    )
    result = isolation.live_sealed_pg_container_readback(policy())
    assert result["passed"] is False
    assert any(message in error for error in result["errors"])


def test_empty_quarantine_can_rebind_across_two_checkpoint_owned_outers(
    tmp_path: Path,
) -> None:
    prior_path = tmp_path / "prior.json"
    checkpoint_path = tmp_path / "checkpoint.json"
    empty = {scene: [] for scene in rebind.SCENES}
    prior = {
        "schema_version": "mysql_algorithm_c_replay_quarantine_v6",
        "status": "PASS",
        "resume_checkpoint_outer_attempt": 10,
        "formal_state_mutation": False,
        "freeze_sha256": "f" * 64,
        "quarantined_param_keys_by_scene": copy.deepcopy(empty),
        "soft_replayed_param_keys_by_scene": copy.deepcopy(empty),
        "permanent_invalid_param_keys_by_scene": copy.deepcopy(empty),
    }
    checkpoint = {
        "status": "PREPARED",
        "completed_outer_attempts": 12,
        "freeze_sha256": "f" * 64,
        "reconstructed_state": {"outer_attempt": 12},
        "evidence": {"outers": {"11": {}, "12": {}}},
    }
    prior_path.write_text(json.dumps(prior), encoding="utf-8")
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    result = rebind.build_document(
        prior, checkpoint, prior_path=prior_path,
        checkpoint_path=checkpoint_path,
    )
    assert result["resume_checkpoint_outer_attempt"] == 12
    assert result["completed_outer_range_owned_by_checkpoint"] == [11, 12]
    assert result["formal_state_mutation"] is False

