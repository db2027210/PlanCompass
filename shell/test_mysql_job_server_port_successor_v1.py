#!/usr/bin/env python3
"""Pure-local tests for JOB v35 dual-port readiness and publication."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent


def import_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module is not importable: %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = import_path(
    "job_v35_builder", HERE / "build_mysql_job_server_port_successor_v1.py"
)


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


class ClientDualPortReadinessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "collector.py"
        path.write_bytes(builder.collector_payload())
        qualification = builder.PREDECESSOR / "qualification"
        sys.path.insert(0, str(qualification))
        try:
            cls.collector = import_path("job_v35_collector", path)
        finally:
            sys.path.remove(str(qualification))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        collector = self.collector
        self.original_verified = collector._verified_database_counter_client
        self.original_run = collector.run
        self.original_monotonic = collector.time.monotonic
        self.original_sleep = collector.time.sleep
        self.identity_calls = []

        def verified(transport):
            self.identity_calls.append(dict(transport))
            return "a" * 64

        collector._verified_database_counter_client = verified
        collector.time.sleep = lambda seconds: None

    def tearDown(self):
        collector = self.collector
        collector._verified_database_counter_client = self.original_verified
        collector.run = self.original_run
        collector.time.monotonic = self.original_monotonic
        collector.time.sleep = self.original_sleep

    def test_3306_server_via_65102_target_passes_with_two_identity_checks(self):
        collector = self.collector
        collector.time.monotonic = lambda: 0
        calls = []

        def ready(argv, timeout, label):
            calls.append((argv, timeout, label))
            return "3306\troot\n"

        collector.run = ready
        result = collector.client_tcp_mysql_readiness(
            TRANSPORT, 65102, 3306, seconds=120
        )
        self.assertEqual(
            result["status"],
            "PASS_EXACT_CLIENT_INTERNAL_TCP_MYSQL_READINESS",
        )
        self.assertEqual(result["tcp_target_port"], 65102)
        self.assertEqual(result["server_mysql_port"], 3306)
        self.assertEqual(result["authenticated_user"], "root")
        self.assertEqual(result["client_container_id_before_probe"], "a" * 64)
        self.assertEqual(result["client_container_id_after_probe"], "a" * 64)
        self.assertEqual(len(self.identity_calls), 2)
        self.assertIn("--port=65102", calls[0][0])

    def test_wrong_target_contract_fails_before_probe(self):
        collector = self.collector
        collector.run = lambda argv, timeout, label: "3306\troot\n"
        with self.assertRaisesRegex(
            collector.QualificationError, "target/server port contract differs"
        ):
            collector.client_tcp_mysql_readiness(
                TRANSPORT, 65103, 3306, seconds=1
            )
        self.assertEqual(self.identity_calls, [])

    def test_wrong_server_contract_fails_before_probe(self):
        collector = self.collector
        collector.run = lambda argv, timeout, label: "3306\troot\n"
        with self.assertRaisesRegex(
            collector.QualificationError, "target/server port contract differs"
        ):
            collector.client_tcp_mysql_readiness(
                TRANSPORT, 65102, 3307, seconds=1
            )
        self.assertEqual(self.identity_calls, [])

    def _assert_observed_identity_fails(self, row):
        collector = self.collector
        ticks = iter((0, 0, 2))
        collector.time.monotonic = lambda: next(ticks)
        collector.run = lambda argv, timeout, label: row
        with self.assertRaisesRegex(
            collector.QualificationError, "timed out after 1 seconds"
        ):
            collector.client_tcp_mysql_readiness(
                TRANSPORT, 65102, 3306, seconds=1
            )

    def test_wrong_observed_server_fails_closed(self):
        self._assert_observed_identity_fails("65102\troot\n")

    def test_wrong_authenticated_user_fails_closed(self):
        self._assert_observed_identity_fails("3306\tother\n")

    def test_collect_readback_keeps_database_counter_on_host_port(self):
        source = builder.collector_payload().decode("utf-8")
        self.assertIn(
            "client_tcp_mysql_readiness(\n"
            "        database_counter_transport, port, 3306\n"
            "    )",
            source,
        )
        self.assertIn(
            "db_counter_sample(\n"
            "        mysql_binary,\n"
            "        defaults_file,\n"
            "        port,\n"
            "        database_counter_transport,\n"
            "    )",
            source,
        )


class PublisherTest(unittest.TestCase):
    def test_predecessor_and_invalidation_verify(self):
        manifest = builder.verify_predecessor()
        builder.verify_invalidation()
        self.assertEqual(manifest["bundle_id"], builder.PREDECESSOR_ID)

    def test_generated_closure_is_complete_successor(self):
        predecessor = builder.verify_predecessor()
        payloads = builder.payloads(predecessor)
        self.assertEqual(
            set(payloads) - {builder.INVALIDATION_RELATIVE},
            set(predecessor["files"]),
        )
        self.assertIn(builder.INVALIDATION_RELATIVE, payloads)
        closure = builder.json.loads(
            payloads["closure.json"].decode("utf-8")
        )
        self.assertEqual(
            closure["client_tcp_readiness_contract"][
                "database_counter_tcp_target_port"
            ],
            65102,
        )
        self.assertEqual(
            closure["client_tcp_readiness_contract"]["server_mysql_port"],
            3306,
        )


if __name__ == "__main__":
    unittest.main()
