#!/usr/bin/env python3
"""Pure local tests for JOB's adopted-client TCP readiness proof."""

from __future__ import print_function

import importlib.util
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "job_collector", HERE / "mysql_job_distributed_live_readback_qualification_v2.py"
)
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


TRANSPORT = {
    "client_container": "ra_mysql_job_v2_attempt_client",
    "client_container_id": "a" * 64,
    "client_container_name": "ra_mysql_job_v2_attempt_client",
    "client_container_short_id": "a" * 12,
    "client_pid": 1234,
    "client_process_start_ticks": 5678,
    "client_inspect_config_sha256": "b" * 64,
    "mysql_binary_in_client": "/usr/bin/mysql",
    "mysql_defaults_file_in_client": "/credentials/mysql.cnf",
}


class ClientTcpReadinessTest(unittest.TestCase):
    def setUp(self):
        self.original_verified = collector._verified_database_counter_client
        self.original_run = collector.run
        self.original_monotonic = collector.time.monotonic
        self.original_sleep = collector.time.sleep
        self.calls = []
        collector._verified_database_counter_client = lambda transport: "a" * 64
        collector.time.sleep = lambda seconds: None

    def tearDown(self):
        collector._verified_database_counter_client = self.original_verified
        collector.run = self.original_run
        collector.time.monotonic = self.original_monotonic
        collector.time.sleep = self.original_sleep

    def test_success_is_client_internal_tcp_and_identity_bound(self):
        collector.time.monotonic = lambda: 0

        def ready(argv, timeout, label):
            self.calls.append((argv, timeout, label))
            return "65102\troot\n"

        collector.run = ready
        result = collector.client_tcp_mysql_readiness(TRANSPORT, 65102, seconds=120)
        self.assertEqual(result["status"], "PASS_EXACT_CLIENT_INTERNAL_TCP_MYSQL_READINESS")
        argv = self.calls[0][0]
        self.assertEqual(argv[:5], ["docker", "exec", "--user", "1008:1008", "a" * 64])
        self.assertIn("--protocol=TCP", argv)
        self.assertIn("--host=127.0.0.1", argv)
        self.assertIn("--port=65102", argv)
        self.assertFalse(result["host_port_publication_required"])

    def test_timeout_fails_closed(self):
        ticks = iter((0, 0, 121))
        collector.time.monotonic = lambda: next(ticks)

        def unavailable(argv, timeout, label):
            raise collector.QualificationError("connection refused")

        collector.run = unavailable
        with self.assertRaisesRegex(collector.QualificationError, "timed out"):
            collector.client_tcp_mysql_readiness(TRANSPORT, 65102, seconds=120)

    def test_wrong_authenticated_identity_fails_closed(self):
        ticks = iter((0, 0, 2))
        collector.time.monotonic = lambda: next(ticks)
        collector.run = lambda argv, timeout, label: "65102\tother\n"
        with self.assertRaisesRegex(collector.QualificationError, "timed out"):
            collector.client_tcp_mysql_readiness(TRANSPORT, 65102, seconds=1)


if __name__ == "__main__":
    unittest.main()
