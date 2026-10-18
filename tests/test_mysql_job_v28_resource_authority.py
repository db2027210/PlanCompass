from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from shell.mysql_algorithm_c_local_services_v28 import (
    EXPECTED_PROFILE,
    EXPECTED_RESOURCE,
    ResourceAuthorityError,
    validate_resource_authority,
)


class ResourceAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.profile = Path(self.temp.name) / "job_memory.json"
        self.profile.write_text(
            json.dumps(EXPECTED_PROFILE), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def config(self) -> dict:
        resource = {
            **EXPECTED_RESOURCE,
            "profile_path": str(self.profile),
            "profile_sha256": hashlib.sha256(
                self.profile.read_bytes()
            ).hexdigest(),
        }
        return {
            "profile": str(self.profile),
            "execution_resource_authority": resource,
            "workload": {"host_control_cpuset": "176-183,432-439"},
            "resource_authority_policy": {
                "executable_authority_count": 1,
                "second_cpu_authority_action": "FAIL_CLOSED",
            },
        }

    def test_unified_resource_authority_passes(self) -> None:
        validate_resource_authority(self.config())

    def test_real_v27_profile_mismatch_fails_closed(self) -> None:
        self.profile.write_text(
            json.dumps({
                **EXPECTED_PROFILE,
                "cpuset": "18-20,54-56",
            }),
            encoding="utf-8",
        )
        candidate = self.config()
        with self.assertRaises(ResourceAuthorityError):
            validate_resource_authority(candidate)

    def test_workload_second_cpuset_fails_closed(self) -> None:
        candidate = self.config()
        candidate["workload"]["host_control_cpuset"] = "18-20,54-56"
        with self.assertRaises(ResourceAuthorityError):
            validate_resource_authority(candidate)

    def test_readiness_authority_cannot_contain_old_cpuset(self) -> None:
        candidate = self.config()
        candidate["execution_resource_authority"]["cpuset_cpus"] = (
            "18-20,54-56"
        )
        with self.assertRaises(ResourceAuthorityError):
            validate_resource_authority(candidate)

    def test_profile_digest_mismatch_fails_closed(self) -> None:
        candidate = self.config()
        candidate["execution_resource_authority"]["profile_sha256"] = "0" * 64
        with self.assertRaises(ResourceAuthorityError):
            validate_resource_authority(candidate)


if __name__ == "__main__":
    unittest.main()
