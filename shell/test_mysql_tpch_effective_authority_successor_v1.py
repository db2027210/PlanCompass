#!/usr/bin/env python3
"""Offline tests for the TPC-H effective-authority-first successor."""

from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
BUILDER_PATH = HERE / "build_mysql_tpch_effective_authority_successor_v1.py"
SPEC = importlib.util.spec_from_file_location("tpch_effective_builder", BUILDER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("builder import failed")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)

BUNDLE_ID = "a" * 64
ATTEMPT = "tpch-v2-test-attempt-0123456789"
CONTAINER_ID = "b" * 64
PID = 42001
DEVICE = "/dev/nvme0n1"
MAJOR_MINOR = "259:1"
RATE = 100663296


class EffectiveAuthorityProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "collector.py"
        path.write_bytes(builder.collector_payload())
        spec = importlib.util.spec_from_file_location(
            "tpch_effective_collector", path
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("collector import failed")
        cls.collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.collector)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def authority(self, **changes):
        value = {
            "schema_version": "mysql_tpch_blkio_v1_throttle_authority_v1",
            "authority_sha256": "c" * 64,
            "bundle_id": BUNDLE_ID,
            "attempt_token": ATTEMPT,
            "database_container_id": CONTAINER_ID,
            "database_pid": PID,
            "same_live_identity_verified": True,
            "cgroup_path": "/sys/fs/cgroup/blkio/docker/" + CONTAINER_ID,
            "device_major_minor": MAJOR_MINOR,
            "read_bps": RATE,
            "write_bps": RATE,
            "docker_hostconfig_is_declaration_not_effective_authority": True,
        }
        value.update(changes)
        return value

    def project(self, read_rows, write_rows, authority):
        return self.collector._validate_docker_blkio_projection(
            read_rows,
            write_rows,
            DEVICE,
            MAJOR_MINOR,
            RATE,
            RATE,
            CONTAINER_ID,
            PID,
            BUNDLE_ID,
            ATTEMPT,
            authority,
        )

    def test_real_empty_empty_projection_requires_and_accepts_exact_authority(self):
        self.assertEqual(
            self.project([], [], self.authority()),
            "empty_arrays_with_exact_effective_cgroup_v1_authority",
        )

    def test_empty_projection_without_authority_is_rejected(self):
        with self.assertRaisesRegex(
            self.collector.QualificationError, "lacks exact effective authority"
        ):
            self.project([], [], None)

    def test_asymmetric_empty_projection_is_rejected(self):
        row = [{"Path": DEVICE, "Rate": RATE}]
        for read_rows, write_rows in (([], row), (row, [])):
            with self.assertRaisesRegex(
                self.collector.QualificationError, "asymmetric"
            ):
                self.project(read_rows, write_rows, self.authority())

    def test_wrong_nonempty_rate_device_and_major_minor_are_rejected(self):
        valid = [{"Path": DEVICE, "Rate": RATE}]
        bad_rows = (
            [{"Path": DEVICE, "Rate": RATE - 1}],
            [{"Path": "/dev/nvme0n1p1", "Rate": RATE}],
            [{"Major": 259, "Minor": 4, "Rate": RATE}],
        )
        for bad in bad_rows:
            with self.assertRaises(self.collector.QualificationError):
                self.project(bad, valid, self.authority())
            with self.assertRaises(self.collector.QualificationError):
                self.project(valid, bad, self.authority())

    def test_wrong_authority_identity_is_rejected(self):
        changes = (
            {"bundle_id": "d" * 64},
            {"attempt_token": ATTEMPT + "-wrong"},
            {"database_container_id": "e" * 64},
            {"database_pid": PID + 1},
            {"cgroup_path": "/sys/fs/cgroup/blkio/docker/" + "e" * 64},
            {"device_major_minor": "259:4"},
            {"read_bps": RATE - 1},
            {"write_bps": RATE - 1},
            {"same_live_identity_verified": False},
        )
        for change in changes:
            with self.assertRaisesRegex(
                self.collector.QualificationError, "lacks exact effective authority"
            ):
                self.project([], [], self.authority(**change))

    def test_manifest_bound_authority_file_is_verified_before_projection(self):
        with tempfile.TemporaryDirectory() as temporary:
            attempt_root = Path(temporary) / ATTEMPT
            credentials = attempt_root / "credentials"
            credentials.mkdir(parents=True)
            defaults_file = credentials / "mysql.cnf"
            defaults_file.write_text("[client]\n", encoding="ascii")
            authority_path = attempt_root / "blkio_throttle_authority.json"
            cgroup = "/sys/fs/cgroup/blkio/docker/" + CONTAINER_ID
            value = {
                "schema_version": "mysql_tpch_blkio_v1_throttle_authority_v1",
                "status": "PASS_EFFECTIVE_CGROUP_V1_THROTTLE",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "postgresql_mutation_prohibited": True,
                "bundle_id": BUNDLE_ID,
                "attempt_token": ATTEMPT,
                "database_container_id": CONTAINER_ID,
                "database_pid": PID,
                "host_effective_uid_gid": [0, 0],
                "cgroup_version": "v1",
                "cgroup_path": cgroup,
                "device_major_minor": MAJOR_MINOR,
                "read_bps": RATE,
                "write_bps": RATE,
                "read_throttle_file":
                    cgroup + "/blkio.throttle.read_bps_device",
                "write_throttle_file":
                    cgroup + "/blkio.throttle.write_bps_device",
                "docker_hostconfig_is_declaration_not_effective_authority": True,
                "database_or_client_started": True,
                "workload_started": False,
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
            }

            def seal(document):
                unsigned = dict(document)
                unsigned.pop("authority_sha256", None)
                document["authority_sha256"] = hashlib.sha256(
                    self.collector.canonical(unsigned)
                ).hexdigest()
                payload = self.collector.canonical(document)
                authority_path.write_bytes(payload)
                Path(str(authority_path) + ".sha256").write_text(
                    hashlib.sha256(payload).hexdigest()
                    + "  "
                    + authority_path.name
                    + "\n",
                    encoding="ascii",
                )

            seal(value)
            container = {
                "Id": CONTAINER_ID,
                "State": {"Running": True, "Pid": PID},
            }
            runtime = {
                "resource_envelope": {
                    "io_read_bps": RATE,
                    "io_write_bps": RATE,
                },
                "storage": {"expected_live_major_minor": MAJOR_MINOR},
            }
            result = self.collector.validate_tpch_effective_blkio_authority(
                container, runtime, defaults_file, BUNDLE_ID
            )
            self.assertEqual(result["bundle_id"], BUNDLE_ID)
            self.assertEqual(result["attempt_token"], ATTEMPT)
            self.assertTrue(result["same_live_identity_verified"])

            value["bundle_id"] = "d" * 64
            seal(value)
            with self.assertRaisesRegex(
                self.collector.QualificationError, "manifest/attempt"
            ):
                self.collector.validate_tpch_effective_blkio_authority(
                    container, runtime, defaults_file, BUNDLE_ID
                )

    def test_exact_nonempty_declaration_remains_strict(self):
        path_rows = [{"Path": DEVICE, "Rate": RATE}]
        mm_rows = [{"Major": 259, "Minor": 1, "Rate": RATE}]
        self.assertEqual(
            self.project(path_rows, mm_rows, self.authority()),
            "exact_nonempty_docker_declaration",
        )

    def test_collector_orders_effective_authority_before_container_projection(self):
        payload = builder.collector_payload()
        authority_call = payload.index(
            b"effective_blkio_authority = "
            b"validate_tpch_effective_blkio_authority("
        )
        readback_call = payload.index(
            b"container_readback = validate_container_readback(",
            authority_call,
        )
        self.assertLess(authority_call, readback_call)


if __name__ == "__main__":
    unittest.main()
