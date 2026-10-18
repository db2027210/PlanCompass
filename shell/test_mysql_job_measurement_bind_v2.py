#!/usr/bin/env python3
"""Regression tests for JOB's one permitted writable client bind.

Run directly with Python; no Docker daemon or data-plane process is touched.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "mysql_job_distributed_live_readback_qualification_v2.py"
SPEC = importlib.util.spec_from_file_location("job_collector_v2", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("collector module is not importable")
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


CONTAINER_ID = "a" * 64
MEASUREMENT_SOURCE = "/dev/shm/ra_mysql_job_v2/job-v2-test-0123456789abcdef"


def client_row(extra_mount=None, measurement_type="bind", measurement_rw=True):
    mounts = [
        {
            "Type": "bind", "Source": "/sealed", "Destination": "/sealed",
            "RW": False,
        },
        {
            "Type": measurement_type, "Source": MEASUREMENT_SOURCE,
            "Destination": "/measurement", "RW": measurement_rw,
        },
    ]
    if extra_mount is not None:
        mounts.append(extra_mount)
    return {
        "Id": CONTAINER_ID, "Name": "/job-client", "Image": "sha256:" + "b" * 64,
        "Created": "2026-07-31T00:00:00Z",
        "Config": {"Image": "sha256:" + "b" * 64, "User": "1008:1008"},
        "HostConfig": {
            "CpuPeriod": 100000, "CpuQuota": 100000,
            "CpusetCpus": "0", "CpusetMems": "0",
            "Memory": 1024, "MemorySwap": 1024, "PidsLimit": 32,
            "NetworkMode": "host", "ReadonlyRootfs": True,
            "LogConfig": {"Type": "none"}, "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"], "Tmpfs": {},
        },
        "State": {"Running": True, "Pid": 1234}, "Mounts": mounts,
    }


def expected():
    return {
        "cpu_quota_cores": 1, "cpuset_cpus": "0", "cpuset_mems": "0",
        "memory_bytes": 1024, "memory_swap_bytes": 1024, "pids_max": 32,
        "network_mode": "host", "docker_log_driver": "none", "tmpfs_bytes": 1073741824,
    }


def binding(row):
    return {
        "container_id": CONTAINER_ID, "container_name": "job-client",
        "image_id": row["Image"], "created": row["Created"],
        "inspect_config_sha256": collector._client_container_config_identity(row),
        "required_read_only_mounts": [{"source": "/sealed", "destination": "/sealed"}],
        "measurement_bind": {"source": MEASUREMENT_SOURCE, "destination": "/measurement"},
    }


class MeasurementBindTests(unittest.TestCase):
    def setUp(self):
        self.original_status = collector.process_status
        self.original_ticks = collector.process_start_ticks
        collector.process_status = lambda _pid: {"Cpus_allowed_list": "0", "Mems_allowed_list": "0"}
        collector.process_start_ticks = lambda _pid: 1

    def tearDown(self):
        collector.process_status = self.original_status
        collector.process_start_ticks = self.original_ticks

    def test_exact_host_tmpfs_bind_is_the_only_permitted_rw_mount(self):
        row = client_row()
        result = collector.validate_client_container_readback(
            row, expected(), binding(row), "/measurement"
        )
        self.assertEqual(result["container_id"], CONTAINER_ID)

    def test_unexpected_rw_bind_is_rejected(self):
        row = client_row({"Type": "bind", "Source": "/bad", "Destination": "/bad", "RW": True})
        with self.assertRaisesRegex(collector.QualificationError, "mutable mount"):
            collector.validate_client_container_readback(row, expected(), binding(row), "/measurement")

    def test_measurement_must_be_one_rw_bind_not_a_volume(self):
        row = client_row(measurement_type="volume")
        with self.assertRaisesRegex(collector.QualificationError, "host tmpfs bind"):
            collector.validate_client_container_readback(row, expected(), binding(row), "/measurement")

    def test_collect_only_constructs_exact_attempt_owned_measurement_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempt_parent = root / "attempts"
            measurement_parent = root / "measurements"
            attempt_id = "job-v2-20260731t000000z-0123456789abcdef"
            credentials = attempt_parent / attempt_id / "credentials"
            credentials.mkdir(parents=True)
            defaults = credentials / "mysql.cnf"
            defaults.write_text("[client]\n", encoding="ascii")
            measurement = measurement_parent / attempt_id
            measurement.mkdir(parents=True)
            bundle = root / "bundle-id"
            bundle.mkdir()
            runtime_root = root / "python-runtime"
            runtime_root.mkdir()

            database_name = "ra_job_test_db"
            client_name = "ra_job_test_client"
            labels = {
                "ra.owner": "mysql_algorithm_c_job_v2",
                "ra.scenario": "job_memory",
                "ra.attempt": attempt_id,
                "ra.bundle_id": bundle.name,
            }
            row = client_row()
            row["Name"] = "/" + client_name
            row["Config"]["Labels"] = dict(labels)
            row["Mounts"] = [
                {
                    "Type": "bind", "Source": str(bundle),
                    "Destination": str(bundle), "RW": False,
                },
                {
                    "Type": "bind", "Source": str(runtime_root),
                    "Destination": str(runtime_root), "RW": False,
                },
                {
                    "Type": "bind", "Source": str(credentials),
                    "Destination": str(credentials), "RW": False,
                },
                {
                    "Type": "bind", "Source": str(measurement),
                    "Destination": "/measurement", "RW": True,
                },
            ]
            database_row = {
                "Config": {"Labels": dict(labels)},
                "Mounts": [{
                    "Type": "bind", "Source": str(root / "datadir"),
                    "Destination": "/var/lib/mysql", "RW": True,
                }],
            }
            runtime = {
                "database_counter_transport": {
                    "schema_version":
                        "mysql_algorithm_c_database_counter_transport_v1",
                    "kind": "exact_client_container_docker_exec_stdout_v1",
                    "mysql_binary_in_client": "/usr/bin/mysql",
                    "stdout_only": True,
                    "host_mysql_cli_required": False,
                    "docker_socket_mount_prohibited": True,
                    "target_datadir_mount_prohibited": True,
                },
                "client_resource_envelope": expected(),
                "toolchain": {"python_runtime_root": str(runtime_root)},
            }
            saved = (
                collector.JOB_ATTEMPT_ROOT,
                collector.JOB_MEASUREMENT_ROOT,
                collector.inspect_container,
                collector.validate_host_tmpfs_output,
                collector.run,
            )
            collector.JOB_ATTEMPT_ROOT = attempt_parent
            collector.JOB_MEASUREMENT_ROOT = measurement_parent
            collector.inspect_container = lambda _name: row
            collector.validate_host_tmpfs_output = (
                lambda path, expected_path, size: {
                    "target": str(path), "size_bytes": size
                }
            )
            collector.run = lambda *_args, **_kwargs: "mysql  Ver 8.0"
            try:
                result = collector.prepare_database_counter_transport(
                    runtime,
                    bundle,
                    database_name,
                    database_row,
                    defaults,
                    client_name,
                    "/usr/bin/mysql",
                )
                self.assertEqual(result["client_container_name"], client_name)
            finally:
                (
                    collector.JOB_ATTEMPT_ROOT,
                    collector.JOB_MEASUREMENT_ROOT,
                    collector.inspect_container,
                    collector.validate_host_tmpfs_output,
                    collector.run,
                ) = saved


if __name__ == "__main__":
    unittest.main()
