from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from shell.mysql_tpch_algorithm_c_local_services_v27 import (
    EXPECTED_COPY_MODE,
    EXPECTED_MANIFEST,
    EXPECTED_PROFILE,
    EXPECTED_PROFILE_ID,
    EXPECTED_RESOURCE,
    EXPECTED_SERVICE,
    EXPECTED_STORAGE,
    TpchAuthorityError,
    validate_authorities,
)


class TpchV35AuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.profile = Path(self.temp.name) / "tpch_v35.json"
        self.profile.write_text(
            json.dumps(EXPECTED_PROFILE), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def config(self) -> dict:
        manifest_sha = hashlib.sha256(
            EXPECTED_MANIFEST.read_bytes()
        ).hexdigest()
        resource = {
            **EXPECTED_RESOURCE,
            "profile_path": str(self.profile),
            "profile_sha256": hashlib.sha256(
                self.profile.read_bytes()
            ).hexdigest(),
        }
        return {
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "profile": str(self.profile),
            "local_storage_profile_id": EXPECTED_PROFILE_ID,
            "local_storage_manifest": str(EXPECTED_MANIFEST),
            "local_storage_manifest_sha256": manifest_sha,
            "execution_storage_authority": {
                **EXPECTED_STORAGE,
                "manifest_sha256": manifest_sha,
            },
            "execution_resource_authority": resource,
            "workload": {
                "host_control_cpuset": "200-207,456-463",
                "tpch_terminals": 4,
                "fresh_copy_mode": EXPECTED_COPY_MODE,
                "fresh_full_physical_copy_required": True,
                "v35_resource_contract": {
                    "authority_role": "mirror_only",
                    "cpu_quota_cores": 4,
                    "memory_bytes": 5583454208,
                    "io_read_bps": 100663296,
                    "io_write_bps": 100663296,
                    "terminals": 4,
                },
            },
            "authority_policy": {
                "executable_storage_authority_count": 1,
                "executable_resource_authority_count": 1,
                "mismatch_action": "FAIL_CLOSED",
            },
            "provenance_only": {
                "authority_role": "provenance_only",
                "execution_authorized": False,
                "may_participate_in_launch": False,
            },
            "production": {"service_factory": EXPECTED_SERVICE},
        }

    def test_exact_authority_passes(self) -> None:
        validate_authorities(self.config())

    def test_real_3277_byte_memory_mismatch_fails(self) -> None:
        self.profile.write_text(
            json.dumps({
                **EXPECTED_PROFILE,
                "memory_bytes": 5583457485,
            }),
            encoding="utf-8",
        )
        with self.assertRaises(TpchAuthorityError):
            validate_authorities(self.config())

    def test_phantom_device_fails(self) -> None:
        candidate = self.config()
        candidate["execution_storage_authority"]["device"] = "/dev/nvme3n1"
        with self.assertRaises(TpchAuthorityError):
            validate_authorities(candidate)

    def test_second_data_template_fails(self) -> None:
        candidate = self.config()
        candidate["data_template"] = {"ready": True}
        with self.assertRaises(TpchAuthorityError):
            validate_authorities(candidate)

    def test_cpu_or_io_mismatch_fails(self) -> None:
        for key, value in (
            ("cpuset_cpus", "18-20,54-56"),
            ("io_read_bps", 100663295),
            ("pids_max", 511),
        ):
            with self.subTest(key=key):
                candidate = self.config()
                candidate["execution_resource_authority"][key] = value
                with self.assertRaises(TpchAuthorityError):
                    validate_authorities(candidate)


if __name__ == "__main__":
    unittest.main()
