#!/usr/bin/env python3
"""Offline tests for the TPC-H readiness-evidence successor."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
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
    "tpch_readiness_builder",
    HERE / "build_mysql_tpch_readiness_evidence_successor_v1.py",
)


class ReadinessEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.authority_path = (
            Path(cls.temporary.name) / "mysql_tpch_distributed_v2_authority.py"
        )
        cls.authority_path.write_bytes(builder.authority_payload())
        sys.path.insert(0, str(builder.PREDECESSOR))
        try:
            cls.authority = import_path(
                "tpch_readiness_authority", cls.authority_path
            )
        finally:
            sys.path.remove(str(builder.PREDECESSOR))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_secret_redaction_and_truncation(self):
        result = self.authority._redacted_bounded_text(
            "password=hunter2 token:abc IDENTIFIED BY 'CHANGE_ME' " + "x" * 500,
            256,
        )
        self.assertNotIn("hunter2", result["text"])
        self.assertNotIn("abc", result["text"])
        self.assertNotIn("CHANGE_ME", result["text"])
        self.assertLessEqual(len(result["text"]), 256)
        self.assertTrue(result["truncated"])

    def test_each_probe_is_journaled_and_success_gate_is_unchanged(self):
        authority = self.authority
        original_run = authority._run
        original_sleep = authority.time.sleep
        original_time = authority.time.time
        calls = iter(
            (
                SimpleNamespace(returncode=1, stdout="", stderr="not ready"),
                SimpleNamespace(returncode=0, stdout="mysqld is alive", stderr=""),
            )
        )
        ticks = iter((1.0, 1.1, 2.0, 2.2))
        try:
            authority._run = lambda *args, **kwargs: next(calls)
            authority.time.sleep = lambda seconds: None
            authority.time.time = lambda: next(ticks)
            with tempfile.TemporaryDirectory() as temporary:
                journal = Path(temporary) / "readiness.json"
                authority._wait_mysql(
                    "db",
                    stage="bootstrap_database_socket",
                    journal_path=journal,
                )
                value = json.loads(journal.read_text(encoding="utf-8"))
                self.assertEqual(value["status"], "PASS_READY")
                self.assertEqual(len(value["records"]), 2)
                self.assertEqual(
                    [row["returncode"] for row in value["records"]], [1, 0]
                )
                self.assertEqual(
                    {
                        row["stage"] for row in value["records"]
                    },
                    {"bootstrap_database_socket"},
                )
                self.assertTrue(
                    all(row["duration_seconds"] >= 0 for row in value["records"])
                )
                self.assertTrue(Path(str(journal) + ".sha256").is_file())
        finally:
            authority._run = original_run
            authority.time.sleep = original_sleep
            authority.time.time = original_time

    def test_180_failures_remain_fail_closed_and_zero_credit(self):
        authority = self.authority
        original_run = authority._run
        original_sleep = authority.time.sleep
        original_time = authority.time.time
        counter = iter(float(index) / 10 for index in range(360))
        try:
            authority._run = lambda *args, **kwargs: SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="password=must-not-persist",
            )
            authority.time.sleep = lambda seconds: None
            authority.time.time = lambda: next(counter)
            with tempfile.TemporaryDirectory() as temporary:
                journal = Path(temporary) / "readiness.json"
                with self.assertRaisesRegex(
                    authority.AuthorityError, "did not become ready"
                ):
                    authority._wait_mysql(
                        "db",
                        "/credentials/mysql.cnf",
                        stage="formal_database_tcp",
                        journal_path=journal,
                    )
                value = json.loads(journal.read_text(encoding="utf-8"))
                self.assertEqual(
                    value["status"], "FAIL_NOT_READY_AFTER_180_PROBES"
                )
                self.assertEqual(len(value["records"]), 180)
                self.assertEqual(value["r0_credit"], 0)
                self.assertEqual(value["accepted_rounds_credit"], 0)
                self.assertNotIn(
                    "must-not-persist",
                    journal.read_text(encoding="utf-8"),
                )
        finally:
            authority._run = original_run
            authority.time.sleep = original_sleep
            authority.time.time = original_time

    def test_precleanup_projection_excludes_environment_and_redacts_logs(self):
        authority = self.authority
        original_run = authority._run

        def completed(argv, **kwargs):
            if argv[:2] == ["docker", "inspect"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        [{
                            "Id": "a" * 64,
                            "Name": "/db",
                            "Created": "now",
                            "Image": "sha256:" + "b" * 64,
                            "State": {
                                "Status": "running", "Running": True,
                                "Pid": 123, "ExitCode": 0,
                                "OOMKilled": False, "Error": "",
                                "StartedAt": "now", "FinishedAt": "",
                            },
                            "RestartCount": 0,
                            "Config": {
                                "Image": "mysql", "Entrypoint": None,
                                "Cmd": ["mysqld"], "User": "",
                                "Env": ["MYSQL_PASSWORD=never-copy-this"],
                            },
                            "HostConfig": {"PortBindings": {}},
                            "Mounts": [],
                        }]
                    ),
                    stderr="",
                )
            if argv[:2] == ["docker", "logs"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="password=never-copy-this",
                    stderr="",
                )
            if argv[0] == "ss":
                return SimpleNamespace(
                    returncode=0,
                    stdout="LISTEN 0 80 127.0.0.1:65103 0.0.0.0:*",
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout="socket_present=1",
                stderr="",
            )

        try:
            authority._run = completed
            value = authority._formal_database_failure_projection("db")
            encoded = json.dumps(value)
            self.assertNotIn("never-copy-this", encoded)
            self.assertNotIn("MYSQL_PASSWORD", encoded)
            self.assertTrue(
                value["inspect"][
                    "environment_excluded_to_prevent_secret_capture"
                ]
            )
            self.assertIn(
                "65103",
                value["host_tcp_listener_65103"]["matching_lines"]["text"],
            )
        finally:
            authority._run = original_run

    def test_formal_failure_seal_precedes_outer_cleanup_in_source(self):
        source = builder.authority_payload()
        seal = source.index(b"_seal_formal_database_readiness_failure(")
        cleanup = source.index(b"def _cleanup_failed_bootstrap")
        self.assertLess(seal, cleanup)
        self.assertIn(
            b"evidence_sealed_before_cleanup", source
        )


class PublisherTests(unittest.TestCase):
    def test_predecessor_and_zero_credit_invalidation_verify(self):
        manifest = builder.verify_predecessor()
        builder.verify_invalidation()
        self.assertEqual(manifest["bundle_id"], builder.PREDECESSOR_ID)

    def test_successor_closure_changes_only_declared_files(self):
        predecessor = builder.verify_predecessor()
        result = builder.payloads(predecessor)
        changed = {
            relative
            for relative, expected in predecessor["files"].items()
            if builder.sha_bytes(result[relative]) != expected
        }
        self.assertEqual(changed, {builder.AUTHORITY_RELATIVE})
        self.assertIn(builder.INVALIDATION_RELATIVE, result)
        self.assertIn(builder.BUILDER_RELATIVE, result)


if __name__ == "__main__":
    unittest.main()
