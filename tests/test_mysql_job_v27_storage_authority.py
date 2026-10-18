from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest

from shell.mysql_algorithm_c_local_services_v27 import (
    StorageAuthorityError,
    validate_storage_authority,
)
from shell.verify_mysql_job_v27_readiness import verify_document


MANIFEST = Path("/disk1/ra_mysql_isolated_v2/job-storage-v2.manifest.json")


def manifest_sha() -> str:
    return hashlib.sha256(MANIFEST.read_bytes()).hexdigest()


def storage() -> dict:
    return {
        "schema_version": "mysql_job_execution_storage_authority_v1",
        "authority_role": "sole_execution_authority",
        "execution_authorized": True,
        "scenario": "job_memory",
        "host_id": "compute00",
        "template_path": "/disk1/ra_mysql_isolated_v2/job-base",
        "manifest_path": str(MANIFEST),
        "manifest_sha256": manifest_sha(),
        "profile_id": "job_local_storage_v2_nvme_fullcopy",
        "device": "/dev/nvme1n1",
        "device_major_minor": "259:5",
        "filesystem": "ext4",
        "fresh_copy_mode": "full_physical_copy_no_overlay_no_reflink",
        "overlay": False,
        "reflink": False,
    }


def config() -> dict:
    return {
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "local_storage_profile_id": "job_local_storage_v2_nvme_fullcopy",
        "local_storage_manifest": str(MANIFEST),
        "local_storage_manifest_sha256": manifest_sha(),
        "execution_storage_authority": storage(),
        "template_provenance": {
            "authority_role": "provenance_only",
            "execution_authorized": False,
            "may_participate_in_launch": False,
            "legacy_path": "/mnt/mysql_nvme1n1/old/job/template",
        },
        "workload": {
            "fresh_copy_mode": "full_physical_copy_no_overlay_no_reflink",
            "fresh_full_physical_copy_required": True,
        },
        "production": {
            "service_factory":
                "shell.mysql_algorithm_c_local_services_v27:build",
        },
    }


class StorageAuthorityTests(unittest.TestCase):
    def test_single_local_authority_passes(self) -> None:
        validate_storage_authority(config())

    def test_second_data_template_fails_closed(self) -> None:
        candidate = config()
        candidate["data_template"] = {
            "path": "/mnt/mysql_nvme1n1/old/job/template",
            "ready": True,
        }
        with self.assertRaises(StorageAuthorityError):
            validate_storage_authority(candidate)

    def test_phantom_device_fails_closed(self) -> None:
        candidate = config()
        candidate["execution_storage_authority"]["device"] = "/dev/nvme3n1"
        with self.assertRaises(StorageAuthorityError):
            validate_storage_authority(candidate)

    def test_provenance_requires_non_executable_markers(self) -> None:
        candidate = config()
        candidate["template_provenance"]["execution_authorized"] = True
        with self.assertRaises(StorageAuthorityError):
            validate_storage_authority(candidate)

    def test_readiness_rejects_phantom_execution_device(self) -> None:
        pointer_path = Path(__file__).resolve()
        pointer = {
            "path": str(pointer_path),
            "sha256": hashlib.sha256(pointer_path.read_bytes()).hexdigest(),
        }
        document = {
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "status": "READY",
            "launch_authorized": True,
            "execution_authorized_scenarios": ["job_memory"],
            "scenarios": {"job_memory": {}},
            "execution_authority": {
                "storage": storage(),
                "runtime_interference_gate_revision": 2,
                "pre_session_cpu_device_first_clean_sample": True,
                "workload_start_requires_runtime_gate_ready": True,
                "fresh_full_physical_copy_required": True,
            },
            "provenance_only": {
                "authority_role": "provenance_only",
                "execution_authorized": False,
                "may_participate_in_launch": False,
                "legacy_remote_scenarios": {},
            },
            "method_bundle": {
                "method_manifest": pointer,
                "config": pointer,
            },
        }
        checks = verify_document(document)
        self.assertTrue(all(item.passed for item in checks))
        self.assertEqual(
            {item.requirement for item in checks},
            {"AUTH", "SCHEMA", *(f"R{index:02d}" for index in range(1, 12))},
        )
        bad = copy.deepcopy(document)
        bad["execution_authority"]["extra"] = "/dev/nvme3n1"
        failed = {item.item for item in verify_document(bad) if not item.passed}
        self.assertIn("no_phantom_execution_device", failed)


if __name__ == "__main__":
    unittest.main()
