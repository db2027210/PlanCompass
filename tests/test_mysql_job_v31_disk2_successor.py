from __future__ import annotations

import copy
import hashlib

from shell.verify_mysql_job_v31_disk2_successor import (
    CONTRACT_PATH,
    load_object,
    validate_successor,
)


def fixtures():
    contract = load_object(CONTRACT_PATH, "contract")
    expected = contract["required_executable_bindings"]
    lane = contract["shared_device_lane"]
    storage_manifest = {
        "schema_version": "mysql_job_local_storage_v3",
        "profile_id": expected["profile_id"],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "template_path": expected["template_path"],
        "device": expected["device"],
        "device_major_minor": expected["device_major_minor"],
        "filesystem": "ext4",
        "fresh_copy_mode": expected["fresh_copy_mode"],
        "template_tree_sha256": "a" * 64,
        "regular_file_count": 194,
        "sealed_read_only": True,
        "clean_shutdown": True,
        "overlay": False,
        "reflink": False,
    }
    manifest_sha = hashlib.sha256(
        b"synthetic storage manifest fixture"
    ).hexdigest()
    resource = {
        "storage_device": expected["device"],
        "storage_device_major_minor": expected["device_major_minor"],
    }
    storage = {
        "device": expected["device"],
        "device_major_minor": expected["device_major_minor"],
        "filesystem": "ext4",
        "fresh_copy_mode": expected["fresh_copy_mode"],
        "manifest_path": expected["manifest_path"],
        "manifest_sha256": manifest_sha,
        "profile_id": expected["profile_id"],
        "template_path": expected["template_path"],
        "overlay": False,
        "reflink": False,
        "execution_authorized": True,
    }
    job_manifest = {
        "execution_resource_authority": resource,
        "execution_storage_authority": storage,
        "fresh_resource_readback_required": True,
        "old_resource_readback_reusable": False,
        "accepted_round_authorized": False,
        "shared_device_lane": lane,
    }
    job_config = {
        "execution_resource_authority": resource,
        "execution_storage_authority": storage,
        "local_storage_manifest": expected["manifest_path"],
        "local_storage_manifest_sha256": manifest_sha,
        "shared_device_lane": lane,
    }
    tpch_manifest = {
        "execution_storage_authority": {"device_major_minor": "259:6"},
        "shared_device_lane": lane,
    }
    tpch_config = {"shared_device_lane": lane}
    return (
        contract,
        job_manifest,
        job_config,
        tpch_manifest,
        tpch_config,
        storage_manifest,
        manifest_sha,
    )


def test_complete_disk2_successor_contract_passes() -> None:
    values = fixtures()
    assert validate_successor(
        values[1], values[2], values[3], values[4], values[5], values[6], values[0]
    ) == []


def test_disk1_execution_authority_fails_closed() -> None:
    values = list(fixtures())
    values[1] = copy.deepcopy(values[1])
    values[1]["execution_resource_authority"]["storage_device"] = "/dev/nvme1n1"
    problems = validate_successor(
        values[1], values[2], values[3], values[4], values[5], values[6], values[0]
    )
    assert any("disk1" in item or "disk2" in item for item in problems)


def test_missing_tpch_shared_lane_fails_closed() -> None:
    values = list(fixtures())
    values[3] = copy.deepcopy(values[3])
    values[3].pop("shared_device_lane")
    problems = validate_successor(
        values[1], values[2], values[3], values[4], values[5], values[6], values[0]
    )
    assert "TPC-H manifest lacks the exact shared disk2 lane" in problems


def test_old_readback_reuse_fails_closed() -> None:
    values = list(fixtures())
    values[1] = copy.deepcopy(values[1])
    values[1]["old_resource_readback_reusable"] = True
    problems = validate_successor(
        values[1], values[2], values[3], values[4], values[5], values[6], values[0]
    )
    assert "old disk1 resource readback is not explicitly rejected" in problems
