from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from shell.build_mysql_job_tpch_disk2_shared_successors import (
    BuildError,
    JOB_PREDECESSOR,
    ROOT,
    SUPERSEDING_CONTRACT,
    build_pair,
    job_overlay_payloads,
    load_object,
    successor_config,
)
from shell.mysql_disk2_shared_lane_v1 import (
    Disk2LaneError,
    Disk2SharedLane,
)
from shell.prepare_mysql_job_disk2_template_v3 import (
    PrepareError,
    atomic_write,
    tree_identity,
)
from shell.verify_mysql_job_v31_disk2_successor import CONTRACT_PATH


def test_shared_disk_builder_is_permanently_deprecated() -> None:
    assert SUPERSEDING_CONTRACT.is_file()
    with pytest.raises(BuildError, match="DEPRECATED_SHARED_PG_DEVICE"):
        build_pair(execute=False)
    with pytest.raises(BuildError, match="DEPRECATED_SHARED_PG_DEVICE"):
        build_pair(execute=True)


def test_tree_identity_is_stable_and_rejects_symlink(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "b").write_bytes(b"two")
    (root / "a").write_bytes(b"one")
    first = tree_identity(root)
    second = tree_identity(root)
    assert first == second
    assert first[1] == 2
    (root / "link").symlink_to("a")
    with pytest.raises(PrepareError, match="symlink prohibited"):
        tree_identity(root)


def test_atomic_write_is_exact_and_replaceable(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    atomic_write(path, b"first\n")
    assert path.read_bytes() == b"first\n"
    atomic_write(path, b"second\n")
    assert path.read_bytes() == b"second\n"
    assert not list(tmp_path.glob(".*.tmp.*"))


def test_shared_disk2_lane_is_exclusive_and_journaled(
    tmp_path: Path,
) -> None:
    control = ROOT / "history/mysql_gradient_debug/host_control"
    lock = control / f".test-{tmp_path.name}.lock"
    journal = control / f".test-{tmp_path.name}.jsonl"
    try:
        first = Disk2SharedLane(
            "job_memory", lock_path=lock, journal_path=journal
        )
        second = Disk2SharedLane(
            "tpch_memory", lock_path=lock, journal_path=journal
        )
        with first:
            with pytest.raises(Disk2LaneError, match="must run serially"):
                second.__enter__()
        with second:
            pass
        rows = [
            json.loads(line)
            for line in journal.read_text(encoding="utf-8").splitlines()
        ]
        assert [row["event"] for row in rows] == [
            "lease_acquired",
            "lease_released",
            "lease_acquired",
            "lease_released",
        ]
        assert {row["scenario"] for row in rows} == {
            "job_memory",
            "tpch_memory",
        }
    finally:
        lock.unlink(missing_ok=True)
        journal.unlink(missing_ok=True)


def test_job_overlay_rebinds_only_frozen_runtime_authorities() -> None:
    predecessor = load_object(
        JOB_PREDECESSOR / "bundle_manifest.json", "JOB manifest"
    )
    source = Path(predecessor["source_directory"])
    payloads = job_overlay_payloads(source)
    combined = b"\n".join(payloads.values()).decode("utf-8")
    assert "/disk2/ra_mysql_isolated_v2/job-base" in combined
    assert "/disk2/ra_mysql_isolated_v2/job-storage-v3.manifest.json" in combined
    assert "/disk2/resource_adaptive_mysql_algorithm_c_local_v3/job" in combined
    assert 'EXPECTED_DEVICE = "/dev/nvme2n1"' in combined
    assert '"storage_device_major_minor": "259:6"' in combined
    assert "/disk1/ra_mysql_isolated_v2/job-base" not in combined
    assert "/disk1/ra_mysql_isolated_v2/job-storage-v2.manifest.json" not in combined


def test_successor_configs_share_exact_lane_and_job_xp_is_not_mutex() -> None:
    contract = load_object(CONTRACT_PATH, "contract")
    predecessor = load_object(JOB_PREDECESSOR / "config.json", "JOB config")
    storage = copy.deepcopy(predecessor["execution_storage_authority"])
    storage.update({
        "device": "/dev/nvme2n1",
        "device_major_minor": "259:6",
        "template_path": "/disk2/ra_mysql_isolated_v2/job-base",
        "manifest_path":
            "/disk2/ra_mysql_isolated_v2/job-storage-v3.manifest.json",
        "manifest_sha256": "a" * 64,
        "profile_id": "job_local_storage_v3_disk2_nvme_fullcopy",
    })
    successor = successor_config(
        "job", predecessor, contract, storage=storage
    )
    assert successor["shared_device_lane"] == contract["shared_device_lane"]
    assert successor["execution_resource_authority"][
        "storage_device_major_minor"
    ] == "259:6"
    assert successor["execution_storage_authority"] == storage
    assert successor["host02_job_xp_mutex"]["required"] is False
    assert successor["fresh_resource_readback_required"] is True
    assert successor["old_resource_readback_reusable"] is False
