#!/usr/bin/env python3
"""Offline regression tests for TPC-H Docker blkio projection normalization."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
BUILDER_PATH = HERE / "build_mysql_tpch_formal10_successor_blkio_v3.py"
SPEC = importlib.util.spec_from_file_location("tpch_builder_v3", BUILDER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("TPC-H builder import failed")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
AUTHORITY_PATH = HERE / "mysql_tpch_distributed_v2_authority_blkio_v2.py"
AUTHORITY_SPEC = importlib.util.spec_from_file_location("tpch_authority_v2", AUTHORITY_PATH)
if AUTHORITY_SPEC is None or AUTHORITY_SPEC.loader is None:
    raise RuntimeError("TPC-H authority import failed")
authority = importlib.util.module_from_spec(AUTHORITY_SPEC)
AUTHORITY_SPEC.loader.exec_module(authority)


class DockerBlkioProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = builder.tpch_collector_payload()
        cls.tempdir = tempfile.TemporaryDirectory()
        path = Path(cls.tempdir.name) / "collector.py"
        path.write_bytes(payload)
        spec = importlib.util.spec_from_file_location("tpch_collector_v3", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("generated collector import failed")
        cls.collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.collector)

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def assert_binding(self, row, expected=True):
        self.assertEqual(
            self.collector._exact_rate_binding(
                [row], "/dev/nvme0n1", "259:1", 100663296
            ),
            expected,
        )

    def test_path_projection_for_whole_nvme(self):
        self.assert_binding({"Path": "/dev/nvme0n1", "Rate": 100663296})

    def test_major_minor_projection_for_whole_nvme(self):
        self.assert_binding({"Major": 259, "Minor": 1, "Rate": 100663296})
        self.assert_binding({"Path": "", "Major": "259", "Minor": "1", "Rate": "100663296"})

    def test_partition_or_mismatched_cgroup_identity_is_rejected(self):
        self.assert_binding({"Path": "/dev/nvme0n1p1", "Rate": 100663296}, False)
        self.assert_binding({"Path": "/dev/nvme0n1", "Major": 259, "Minor": 4, "Rate": 100663296}, False)

    def test_rate_and_ambiguous_row_are_rejected(self):
        self.assert_binding({"Path": "/dev/nvme0n1", "Rate": 100663295}, False)
        self.assertFalse(
            self.collector._exact_rate_binding(
                [
                    {"Path": "/dev/nvme0n1", "Rate": 100663296},
                    {"Path": "/dev/nvme0n1", "Rate": 100663296},
                ], "/dev/nvme0n1", "259:1", 100663296
            )
        )

    def test_bootstrap_authority_uses_the_same_strict_whole_disk_projection(self):
        self.assertTrue(authority._docker_blkio_binding(
            [{"Major": 259, "Minor": 1, "Rate": 100663296}],
            "/dev/nvme0n1", "259:1", 100663296,
        ))
        self.assertFalse(authority._docker_blkio_binding(
            [{"Path": "/dev/nvme0n1p1", "Rate": 100663296}],
            "/dev/nvme0n1", "259:1", 100663296,
        ))


if __name__ == "__main__":
    unittest.main()
