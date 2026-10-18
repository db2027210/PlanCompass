#!/usr/bin/env python3
"""Tests for transaction-local JOB SQL execution without a live database."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

import job_session_local_runner as runner
import prepare


DEFAULTS = {
    "seq_page_cost": "1",
    "random_page_cost": "4",
    "cpu_tuple_cost": "0.01",
    "cpu_index_tuple_cost": "0.005",
    "cpu_operator_cost": "0.0025",
    "parallel_setup_cost": "1000",
    "parallel_tuple_cost": "0.1",
}


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.description = None
        self.rows = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, sql: str, params=None) -> None:
        normalized = " ".join(sql.strip().split())
        self.connection.factory.record(normalized)
        self.rows = []
        self.description = None
        if normalized == "BEGIN":
            self.connection.in_transaction = True
            return
        if normalized.startswith("SET LOCAL "):
            assignment = normalized[len("SET LOCAL "):]
            name, raw_value = assignment.split(" = ", 1)
            self.connection.local[name] = raw_value
            return
        if normalized == "SELECT current_setting(%s)":
            name = str(params[0])
            value = self.connection.local.get(name, self.connection.factory.defaults[name])
            if self.connection.factory.corrupt_readback and name == "random_page_cost":
                value = "999"
            self.rows = [(value,)]
            self.description = (("current_setting",),)
            return
        if normalized.startswith("SELECT"):
            self.rows = [(1, "stable-result")]
            self.description = (("value",),)
            return
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchmany(self, size: int):
        values = self.rows[:size]
        self.rows = self.rows[size:]
        return values


class FakeConnection:
    def __init__(self, factory: "FakeFactory", application_name: str) -> None:
        self.factory = factory
        self.application_name = application_name
        self.autocommit = False
        self.local = {}
        self.in_transaction = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def rollback(self) -> None:
        self.local.clear()
        self.in_transaction = False
        self.factory.rollbacks += 1

    def close(self) -> None:
        self.closed = True
        self.factory.closed += 1


class FakeFactory:
    def __init__(self, corrupt_readback: bool = False) -> None:
        self.defaults = copy.deepcopy(DEFAULTS)
        self.corrupt_readback = corrupt_readback
        self.connections = []
        self.statements = []
        self.rollbacks = 0
        self.closed = 0
        self.lock = threading.Lock()

    def __call__(self, **kwargs):
        connection = FakeConnection(self, str(kwargs.get("application_name") or ""))
        with self.lock:
            self.connections.append(connection)
        return connection

    def record(self, sql: str) -> None:
        with self.lock:
            self.statements.append(sql)


class JobSessionRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sql_dir = self.root / "sql"
        self.sql_dir.mkdir()
        self.sql = self.sql_dir / "1a.sql"
        self.sql.write_text("SELECT 1\n", encoding="utf-8")
        self.resources = self.root / "measurement_resource_samples.jsonl"
        with self.resources.open("w", encoding="utf-8") as handle:
            for index in range(180):
                handle.write(json.dumps({
                    "end_epoch": index + 1,
                    "cpu_utilization": 0.40 + (index % 3) * 0.001,
                    "memory_utilization": 0.86 + (index % 2) * 0.0001,
                    "io_utilization": 0.35 + (index % 4) * 0.001,
                }) + "\n")
        contract = prepare.read_json(prepare.CONTRACT_PATH)
        self.output = self.root / "output"
        self.config = {
            "parameter_application_contract": copy.deepcopy(
                contract["parameter_application_contract"]
            ),
            "initial_params": copy.deepcopy(contract["initial_params"]),
            "param_ranges": copy.deepcopy(contract["parameter_ranges"]),
            "job_session_runner": {
                "endpoint": {
                    "host": "127.0.0.1",
                    "port": 64311,
                    "database": "imdbload",
                    "user": "postgres",
                    "password_env": "JOB_TEST_PASSWORD",
                },
                "sql_dir": str(self.sql_dir),
                "query_sequence": ["1a.sql"],
                "query_weights": [100],
                "query_sha256": {
                    "1a.sql": hashlib.sha256(self.sql.read_bytes()).hexdigest()
                },
                "workers": 2,
                "duration_seconds": 60,
                "max_queries_per_worker": 2,
                "seed": 20260727,
                "output_dir": str(self.output),
                "resource_samples_path": str(self.resources),
                "resource_sample_interval_seconds": 1,
                "stability_window_seconds": 30,
                "required_stability_windows": 6,
            },
        }
        self.old_password = runner.os.environ.get("JOB_TEST_PASSWORD")
        runner.os.environ["JOB_TEST_PASSWORD"] = "CHANGE_ME"

    def tearDown(self) -> None:
        if self.old_password is None:
            runner.os.environ.pop("JOB_TEST_PASSWORD", None)
        else:
            runner.os.environ["JOB_TEST_PASSWORD"] = self.old_password
        self.temp.cleanup()

    def test_every_worker_query_uses_local_settings_and_readback(self) -> None:
        factory = FakeFactory()
        result = runner.run(self.config, connect=factory)
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["measurement"]["correctness_passed"])
        self.assertTrue(result["measurement"]["resource_stability"]["passed"])
        self.assertTrue(result["cleanup"]["cleanup_complete"])
        self.assertTrue(result["readback"]["global_defaults_unchanged"])
        self.assertEqual(factory.defaults, DEFAULTS)

        rows = [
            json.loads(line)
            for line in (self.output / "measurement_queries.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        self.assertEqual(len(rows), 4)
        self.assertEqual({row["terminal"] for row in rows}, {1, 2})
        for row in rows:
            self.assertTrue(row["success"])
            self.assertEqual(row["parameter_application"], "SET LOCAL")
            self.assertTrue(row["readback_before_workload"])
            self.assertTrue(row["rollback_after_query"])
            self.assertEqual(set(row["parameter_readback"]), set(runner.PARAMETERS))
        local_statements = [
            sql for sql in factory.statements if sql.startswith("SET LOCAL ")
        ]
        self.assertEqual(len(local_statements), 4 * len(runner.PARAMETERS))

        point = json.loads(
            (self.output / "point_result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(point["status"], "complete")
        self.assertIn("resource_mean_utilization", point["measurement"])
        self.assertIn("resource_stability", point["measurement"])
        self.assertEqual(point["cleanup"]["closed_worker_connections"], 2)

    def test_immediate_readback_mismatch_fails_closed(self) -> None:
        factory = FakeFactory(corrupt_readback=True)
        result = runner.run(self.config, connect=factory)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["measurement"]["correctness_passed"])
        self.assertIn("correctness_failed", result["failure_reasons"])
        rows = [
            json.loads(line)
            for line in (self.output / "measurement_queries.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        self.assertTrue(any(not row["success"] for row in rows))

    def test_numeric_readback_accepts_postgresql_display_rounding(self) -> None:
        self.assertTrue(
            runner.numeric_readback_matches("3162.28", 3162.2776601683795)
        )
        self.assertTrue(runner.numeric_readback_matches("0.00025", 0.00025))

    def test_numeric_readback_rejects_real_parameter_drift(self) -> None:
        self.assertFalse(
            runner.numeric_readback_matches("3162.30", 3162.2776601683795)
        )
        self.assertFalse(runner.numeric_readback_matches("0.00026", 0.00025))

    def test_source_contains_no_persistent_application_path(self) -> None:
        source = Path(runner.__file__).read_text(encoding="utf-8")
        for pattern in prepare.PROHIBITED_APPLICATION_REGEXES:
            self.assertIsNone(
                prepare.re.search(
                    pattern,
                    source,
                    flags=prepare.re.IGNORECASE | prepare.re.DOTALL,
                )
            )


if __name__ == "__main__":
    unittest.main()
